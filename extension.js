// extension.js — thin collector. No logic beyond observing and forwarding.
// Raw observations use contract.EventKind values as `k`, one JSON object per
// line, piped to the Python capture engine (extention.py). Redaction,
// fingerprinting, segmentation and signals all live in Python.
const vscode = require('vscode');
const path = require('path');
const { spawn } = require('child_process');

const MAX_TEXT = 1500;
const CURSOR_JUMP_LINES = 30;
const TEST_CMD = /\b(pytest|unittest|jest|vitest|mocha|go test|cargo test|npm (run )?test|yarn test|pnpm test|dotnet test)\b/;

let out;
let engine = null;

function log(line) {
  out.appendLine(line);
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
  log('raw ' + line.slice(0, 200));
  if (engine && engine.stdin.writable) engine.stdin.write(line + '\n');
}

function startEngine(context) {
  const cfg = vscode.workspace.getConfiguration('restraint');
  const script = path.join(context.extensionPath, 'extention.py');
  const dir = context.extensionPath;
  const args = [script, '--port', String(cfg.get('port', 8765)),
    '--record', path.join(dir, 'trace.raw.jsonl'),
    '--events-out', path.join(dir, 'trace.events.jsonl'),
    '--out', path.join(dir, 'trace.candidates.jsonl')];
  if (!cfg.get('redaction.enabled', true)) args.push('--no-redact');
  engine = spawn(cfg.get('python', 'python'), args, { cwd: context.extensionPath });
  engine.stdout.on('data', d => log('engine: ' + String(d).trimEnd()));
  engine.stderr.on('data', d => log('engine!: ' + String(d).trimEnd()));
  engine.on('error', e => { log('engine failed to start: ' + e.message); engine = null; });
  engine.on('exit', c => { log('engine exited ' + c); engine = null; });
}

function activate(context) {
  out = vscode.window.createOutputChannel('Restraint');
  context.subscriptions.push(out);
  log('activated');

  context.subscriptions.push(
    vscode.commands.registerCommand('restraint.hello', () => vscode.window.showInformationMessage('Restraint: Hello World')),
    vscode.commands.registerCommand('restraint.showLog', () => out.show(true)),
  );

  startEngine(context);
  context.subscriptions.push({ dispose: () => engine && engine.kill() });
  registerSources(context);
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

function deactivate() { if (engine) engine.kill(); }

module.exports = { activate, deactivate };
