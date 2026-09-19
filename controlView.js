// controlView.js — the "Control" view in the Big Brother sidebar. Start/stop the
// capture engine + judge (+ surface mailbox) in THIS window, and see what state
// everything is in. All lifecycle work is Runner's; this only displays it.
const vscode = require('vscode');
const crypto = require('crypto');

const POLL_MS = 3000;

async function getJson(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(1500) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

class ControlView {
  constructor(runner) {
    this.runner = runner;
    this.view = null;
    this.live = { engine: null, mailbox: null };   // last /health and /state answers
    runner.onChange(() => this.push());
  }

  resolveWebviewView(view) {
    this.view = view;
    const nonce = crypto.randomBytes(16).toString('hex');
    view.webview.options = { enableScripts: true };
    view.webview.html = html(nonce);
    view.webview.onDidReceiveMessage(m => this.command(m.cmd));
    const timer = setInterval(() => view.visible && this.poll(), POLL_MS);
    view.onDidDispose(() => { clearInterval(timer); this.view = null; });
    view.onDidChangeVisibility(() => view.visible && this.poll());
    this.poll();
  }

  command(cmd) {
    const r = this.runner;
    ({
      start: () => r.start(),
      stop: () => r.stop(),
      restart: () => r.restart(),
      dashboard: () => r.openDashboard(),
      decisions: () => r.openDecisionLog(),
      log: () => vscode.commands.executeCommand('bigBrother.showLog'),
      test: () => vscode.commands.executeCommand('bigBrother.testNotification'),
      settings: () => vscode.commands.executeCommand('workbench.action.openSettings', 'bigBrother'),
      env: () => r.openEnv(),
      install: () => r.installRequirements(),
    })[cmd]?.();
  }

  async poll() {
    const s = this.runner.snapshot();
    const [engine, mailbox] = await Promise.all([
      s.procs.engine ? getJson(`http://127.0.0.1:${s.port}/health`) : null,
      getJson(`http://127.0.0.1:${s.mailboxPort}/state`),
    ]);
    this.live = { engine, mailbox };
    this.push();
  }

  push() {
    if (!this.view) return;
    this.view.webview.postMessage({ type: 'status', data: { ...this.runner.snapshot(), live: this.live } });
  }
}

function html(nonce) {
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size);
         color: var(--vscode-foreground); padding: 8px 12px 16px; margin: 0; }
  .state { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 1.1em; margin: 4px 0 2px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--vscode-disabledForeground); }
  .dot.running { background: var(--vscode-testing-iconPassed, #3fb950); }
  .dot.starting { background: var(--vscode-progressBar-background, #0e70c0); }
  .dot.degraded { background: var(--vscode-editorWarning-foreground, #cca700); }
  .dot.error { background: var(--vscode-editorError-foreground, #f14c4c); }
  .detail { color: var(--vscode-descriptionForeground); margin-bottom: 10px; line-height: 1.4; }
  .buttons { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0 14px; }
  button { font: inherit; border: none; padding: 5px 12px; border-radius: 2px; cursor: pointer;
           background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  button:hover { background: var(--vscode-button-secondaryHoverBackground); }
  button.primary { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
  button.primary:hover { background: var(--vscode-button-hoverBackground); }
  button.link { background: none; padding: 2px 0; color: var(--vscode-textLink-foreground); }
  button.link:hover { text-decoration: underline; background: none; }
  h3 { font-size: 0.85em; text-transform: uppercase; letter-spacing: .04em; margin: 14px 0 6px;
       color: var(--vscode-descriptionForeground); font-weight: 600; }
  table { border-collapse: collapse; width: 100%; }
  td { padding: 2px 0; vertical-align: top; }
  td:first-child { color: var(--vscode-descriptionForeground); width: 42%; padding-right: 8px; }
  .ok { color: var(--vscode-testing-iconPassed, #3fb950); }
  .bad { color: var(--vscode-editorError-foreground, #f14c4c); }
  .muted { color: var(--vscode-descriptionForeground); }
  .last { border-left: 2px solid var(--vscode-textLink-foreground); padding: 4px 8px; margin-top: 4px;
          background: var(--vscode-textBlockQuote-background); line-height: 1.4; word-break: break-word; }
  .last.silent { border-left-color: var(--vscode-disabledForeground); }
  code { font-family: var(--vscode-editor-font-family); font-size: .95em; }
  .links { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
</style></head>
<body>
  <div class="state"><span class="dot" id="dot"></span><span id="state">…</span></div>
  <div class="detail" id="detail"></div>
  <div class="buttons" id="buttons"></div>

  <h3>This window</h3>
  <table id="procs"></table>

  <h3>Decisions</h3>
  <table id="counts"></table>
  <div id="last"></div>

  <h3>Setup</h3>
  <table id="setup"></table>

  <h3>Open</h3>
  <div class="links">
    <button class="link" data-cmd="dashboard">Dashboard (restraint log)</button>
    <button class="link" data-cmd="decisions">Decision log file</button>
    <button class="link" data-cmd="log">Output log</button>
    <button class="link" data-cmd="test">Test notification</button>
    <button class="link" data-cmd="settings">Settings</button>
  </div>

<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
const $ = id => document.getElementById(id);
document.body.addEventListener('click', e => {
  const b = e.target.closest('button[data-cmd]');
  if (b) vscode.postMessage({ cmd: b.dataset.cmd });
});
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const row = (k, v) => '<tr><td>' + esc(k) + '</td><td>' + v + '</td></tr>';
const up = (on, yes, no) => on ? '<span class="ok">● ' + esc(yes) + '</span>' : '<span class="muted">○ ' + esc(no) + '</span>';
const ago = ts => { const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  return s < 60 ? s + 's ago' : s < 3600 ? Math.round(s / 60) + 'min ago' : Math.round(s / 3600) + 'h ago'; };
const LABEL = { starting: 'Starting…', running: 'Running', degraded: 'Capture only', stopped: 'Off', error: 'Failed' };

window.addEventListener('message', ({ data: m }) => {
  if (m.type !== 'status') return;
  const s = m.data, live = s.live || {};
  $('dot').className = 'dot ' + s.state;
  $('state').textContent = 'Big Brother: ' + LABEL[s.state];
  $('detail').textContent = s.detail || (s.state === 'stopped'
    ? 'Nothing is running in this window. Start runs the capture engine, the judge and the surface server here.' : '');

  const running = s.procs.engine || s.procs.judge || s.state === 'starting';
  let btns = running
    ? '<button data-cmd="stop">Stop</button><button data-cmd="restart">Restart</button>'
    : '<button class="primary" data-cmd="start">Start in this window</button>';
  if (s.judgeProblem && /installed/.test(s.judgeProblem)) btns += '<button data-cmd="install">Install requirements</button>';
  if (s.judgeProblem && /GEMINI_API_KEY/.test(s.judgeProblem)) btns += '<button data-cmd="env">Open .env</button>';
  $('buttons').innerHTML = btns;

  const eng = live.engine;
  const mb = live.mailbox;
  $('procs').innerHTML =
    row('Capture engine', up(s.procs.engine, 'running :' + s.port, 'stopped')
        + (eng ? ' <span class="muted">· ' + eng.buffered_events + ' events, ' + eng.candidates + ' candidates</span>' : '')) +
    row('Judge', up(s.procs.judge, 'running', s.judgeProblem ? 'not started' : 'stopped')) +
    row('Surface server', s.procs.mailbox ? up(true, 'running :' + s.mailboxPort, '')
        : mb ? '<span class="ok">● shared :' + s.mailboxPort + '</span> <span class="muted">(another process)</span>'
        : up(false, '', 'stopped')) +
    (mb && mb.snoozed_until > mb.now ? row('Snoozed', 'until ' + new Date(mb.snoozed_until).toLocaleTimeString()) : '') +
    (mb && mb.considering ? row('Judge', '<span class="ok">considering a moment…</span>') : '');

  $('counts').innerHTML =
    row('Spoke', String(s.spoke)) + row('Stayed quiet', String(s.silent)) +
    (s.startedAt ? row('Session', esc(s.session || '—') + ' <span class="muted">· started ' + ago(s.startedAt) + '</span>') : '');
  const d = s.lastDecision;
  $('last').innerHTML = !d ? '<div class="muted">No decisions yet in this window.</div>'
    : '<div class="last ' + (d.spoke ? '' : 'silent') + '"><b>' + (d.spoke ? 'Spoke' : 'Quiet (' + esc(d.reason) + ')')
      + '</b> <span class="muted">· ' + ago(d.ts) + ' · ' + d.ms + 'ms</span><br>'
      + (d.spoke ? esc(d.content) + '<br>' : '')
      + (d.why ? '<span class="muted">' + esc(d.why) + '</span>' : '') + '</div>';

  $('setup').innerHTML =
    row('Python', s.python ? '<code>' + esc(s.python) + '</code>' : '<span class="muted">not detected yet</span>') +
    row('Prompt', '<code>' + esc(s.prompt || 'default') + '</code>') +
    row('Budget', 'none: each moment judged on its merits') +
    row('Redaction', s.redaction ? '<span class="ok">on</span>' : '<span class="bad">off</span>') +
    (s.judgeProblem ? row('Problem', '<span class="bad">' + esc(s.judgeProblem) + '</span>') : '');
});
</script>
</body></html>`;
}

ControlView.id = 'bigBrother.control';

module.exports = { ControlView };
