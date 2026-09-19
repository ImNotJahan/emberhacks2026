// extension.js — thin collector. No logic beyond observing and forwarding.
// Raw observations use contract.EventKind values as `k`, one JSON object per
// line, piped to the Python capture engine (extention.py). Redaction,
// fingerprinting, segmentation and signals all live in Python.
const vscode = require('vscode');
const path = require('path');
const { Runner } = require('./runner');
const testSurface = require('./testSurface'); // TEMP: remove with testSurface.js

const MAX_TEXT = 1500;
const CURSOR_JUMP_LINES = 30;
const TEST_CMD = /\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (run )?test|yarn test|pnpm test|dotnet test)\b/;

let out;
let runner = null;

function log(line) {
  out.appendLine(`${new Date().toLocaleTimeString()}  ${line}`);
  console.log('[restraint] ' + line);
}

function relPath(uri) {
  if (!uri || uri.scheme !== 'file') return undefined;
  const rel = vscode.workspace.asRelativePath(uri, false).replace(/\\/g, '/');
  // Outside the workspace asRelativePath returns the absolute path; keep the
  // basename only so absolute paths never leave the editor.
  return path.isAbsolute(rel) || /^[a-zA-Z]:/.test(rel) ? path.basename(rel) : rel;
}

function send(ev) {
  ev.ts = ev.ts || Date.now();
  if (typeof ev.text === 'string' && ev.text.length > MAX_TEXT) ev.text = ev.text.slice(-MAX_TEXT);
  const line = JSON.stringify(ev);
  if (vscode.workspace.getConfiguration('restraint').get('verbose', false)) log('raw ' + line.slice(0, 200));
  if (runner) runner.write(line);
}

function activate(context) {
  out = vscode.window.createOutputChannel('Restraint');
  context.subscriptions.push(out);
  log('activated');

  runner = new Runner(context, log);
  let surface = null;
  if (vscode.workspace.getConfiguration('restraint').get('testSurface.enabled', true))
    surface = testSurface.start(context, log, spoke => runner.noteDecision(spoke)); // TEMP: remove with testSurface.js

  context.subscriptions.push(
    vscode.commands.registerCommand('restraint.showLog', () => out.show(true)),
    vscode.commands.registerCommand('restraint.menu', () => runner.menu()),
    vscode.commands.registerCommand('restraint.start', () => runner.start()),
    vscode.commands.registerCommand('restraint.stop', () => runner.stop()),
    vscode.commands.registerCommand('restraint.restart', () => runner.restart()),
    vscode.commands.registerCommand('restraint.openDecisionLog', () => runner.openDecisionLog()),
    vscode.commands.registerCommand('restraint.testNotification', () =>
      surface ? surface.test() : vscode.window.showInformationMessage('Restraint: the test surface is disabled (restraint.testSurface.enabled).')),
  );

  registerSources(context);
  if (vscode.workspace.getConfiguration('restraint').get('autoStart', true)) runner.start();
}

function registerSources(context) {
  const sub = d => context.subscriptions.push(d);

  // Edits: size only, never document text.
  sub(vscode.workspace.onDidChangeTextDocument(e => {
    if (e.document.uri.scheme !== 'file' || !e.contentChanges.length) return;
    const added = e.contentChanges.reduce((n, c) => n + c.text.length, 0);
    const removed = e.contentChanges.reduce((n, c) => n + c.rangeLength, 0);
    send({ k: 'edit', path: relPath(e.document.uri), lang: e.document.languageId, meta: { added, removed } });
  }));

  sub(vscode.workspace.onDidSaveTextDocument(d =>
    send({ k: 'save', path: relPath(d.uri), lang: d.languageId })));

  sub(vscode.workspace.onDidOpenTextDocument(d => {
    if (d.uri.scheme === 'file') send({ k: 'file_open', path: relPath(d.uri), lang: d.languageId });
  }));

  sub(vscode.window.onDidChangeActiveTextEditor(ed => {
    if (ed && ed.document.uri.scheme === 'file')
      send({ k: 'file_focus', path: relPath(ed.document.uri), lang: ed.document.languageId });
  }));

  let lastLine = new Map();
  sub(vscode.window.onDidChangeTextEditorSelection(e => {
    const key = e.textEditor.document.uri.toString();
    const line = e.selections[0].active.line;
    const prev = lastLine.get(key);
    lastLine.set(key, line);
    if (prev !== undefined && Math.abs(line - prev) >= CURSOR_JUMP_LINES && e.kind !== vscode.TextEditorSelectionChangeKind.Keyboard)
      send({ k: 'cursor_jump', path: relPath(e.textEditor.document.uri), lang: e.textEditor.document.languageId, meta: { from: prev, to: line } });
  }));

  // Diagnostics: report only errors, one event per changed file with errors.
  const hadErrors = new Set();
  sub(vscode.languages.onDidChangeDiagnostics(e => {
    for (const uri of e.uris) {
      if (uri.scheme !== 'file') continue;
      const errs = vscode.languages.getDiagnostics(uri).filter(d => d.severity === vscode.DiagnosticSeverity.Error);
      const doc = vscode.workspace.textDocuments.find(d => d.uri.toString() === uri.toString());
      // count:0 marks "errors cleared" so the engine can tell resolved from unresolved.
      if (!errs.length) {
        if (hadErrors.delete(uri.toString())) send({ k: 'diagnostic', path: relPath(uri), lang: doc && doc.languageId, meta: { count: 0 } });
        continue;
      }
      hadErrors.add(uri.toString());
      send({ k: 'diagnostic', path: relPath(uri), lang: doc && doc.languageId,
             text: errs[0].message, meta: { count: errs.length, line: errs[0].range.start.line } });
    }
  }));

  // Terminal: shell integration API (stable since VS Code 1.93). Terminals
  // without shell integration (e.g. cmd.exe) produce no events.
  const pending = new Map();
  sub(vscode.window.onDidStartTerminalShellExecution(async e => {
    const cmd = e.execution.commandLine.value;
    send({ k: 'terminal_cmd', text: cmd, meta: { is_test: TEST_CMD.test(cmd) } });
    // Store the read as a promise so the end handler awaits it: no race.
    pending.set(e.execution, (async () => {
      let buf = '';
      try {
        for await (const chunk of e.execution.read()) buf = (buf + chunk).slice(-MAX_TEXT * 4);
      } catch (err) { log('terminal read error: ' + err.message); }
      return buf;
    })());
  }));
  sub(vscode.window.onDidEndTerminalShellExecution(async e => {
    const cmd = e.execution.commandLine.value;
    const raw = await (pending.get(e.execution) || Promise.resolve(''));
    const output = raw.replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '').replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g, '');
    pending.delete(e.execution);
    send({ k: 'terminal_out', text: output, exit_code: e.exitCode, meta: { cmd, is_test: TEST_CMD.test(cmd) } });
  }));
}

function deactivate() { if (runner) runner.stop(true); }

module.exports = { activate, deactivate };
