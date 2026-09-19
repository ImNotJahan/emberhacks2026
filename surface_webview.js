// surface_webview.js — hosts Person 3's intervention surface
// (http://127.0.0.1:8766/surface) in the "Big Brother" view of the Restraint
// sidebar. The page does all the work; this file only hosts it:
//   * waits for p3_server.py to answer before loading the page, so a view that
//     opens before the server is up doesn't get stuck on an error page
//   * opens links the page asks for (the restraint log) in the real browser,
//     since the sandboxed iframe can't open tabs itself
//
// package.json -> "contributes" -> "views" -> "restraint":
//   { "type": "webview", "id": "restraint.surface", "name": "Big Brother" }
// extension.js activate(context):
//   context.subscriptions.push(
//     vscode.window.registerWebviewViewProvider(SurfaceView.id, new SurfaceView()));

const vscode = require('vscode');
const crypto = require('crypto');

const RETRY_MS = 1500;

class SurfaceView {
  constructor(base = 'http://127.0.0.1:8766') {   // not localhost: see p3_send.py
    this.base = base;
    this.origin = null;
  }

  async resolveWebviewView(view) {
    view.webview.options = { enableScripts: true };
    view.webview.onDidReceiveMessage(m => {
      // only ever open pages on our own server
      if (m && m.type === 'open' && typeof m.url === 'string' && this.origin && m.url.startsWith(this.origin + '/'))
        vscode.env.openExternal(vscode.Uri.parse(m.url));
    });
    const render = async () => { view.webview.html = await this.html(); };
    await render();
    // re-theme when the user switches VS Code themes
    const sub = vscode.window.onDidChangeActiveColorTheme(render);
    view.onDidDispose(() => sub.dispose());
  }

  async html() {
    const kind = vscode.window.activeColorTheme.kind;
    const light = kind === vscode.ColorThemeKind.Light || kind === vscode.ColorThemeKind.HighContrastLight;
    // asExternalUri makes the local server reachable from remote / Codespaces sessions too
    const src = await vscode.env.asExternalUri(
      vscode.Uri.parse(`${this.base}/surface?embed=1&theme=${light ? 'light' : 'dark'}`));
    const origin = this.origin = `${src.scheme}://${src.authority}`;
    const nonce = crypto.randomBytes(16).toString('hex');
    return `<!DOCTYPE html>
<html><head>
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; frame-src ${origin}; connect-src ${origin}; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<style>
  html,body,iframe{margin:0;padding:0;width:100%;height:100%;border:0;overflow:hidden;background:transparent}
  #wait{padding:12px 14px;font:12px var(--vscode-font-family);color:var(--vscode-descriptionForeground)}
  [hidden]{display:none}
</style>
</head><body>
<p id="wait">Waiting for the Big Brother server on ${origin}…</p>
<iframe id="page" title="Big Brother" hidden></iframe>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const src = ${JSON.stringify(src.toString(true))};
  const origin = ${JSON.stringify(origin)};
  const page = document.getElementById('page'), wait = document.getElementById('wait');
  // no-cors: we only need to know the server answers, not read the reply
  const up = () => fetch(origin + '/state', {mode: 'no-cors', cache: 'no-store'}).then(() => true, () => false);
  (async () => {
    while (!(await up())) await new Promise(r => setTimeout(r, ${RETRY_MS}));
    page.src = src; page.hidden = false; wait.hidden = true;
  })();
  window.addEventListener('message', e => {
    if (e.origin === origin && e.data && e.data.type === 'big-brother:open')
      vscode.postMessage({type: 'open', url: e.data.url});
  });
</script>
</body></html>`;
  }
}
SurfaceView.id = 'bigBrother.surface';

module.exports = { SurfaceView };
