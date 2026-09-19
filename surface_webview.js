// surface_webview.js — for the VS Code extension (EnderNasha).
// Shows Person 3's intervention surface (http://localhost:8766/surface) in a
// sidebar view. The page does all the work; this file only hosts it.
//
// 1. package.json -> "contributes":
//      "views": { "explorer": [ { "type": "webview", "id": "restraint.surface", "name": "Big Brother" } ] }
// 2. in extension.js activate(context):
//      const { SurfaceView } = require('./surface_webview');
//      context.subscriptions.push(
//        vscode.window.registerWebviewViewProvider(SurfaceView.id, new SurfaceView()));

const vscode = require('vscode');

class SurfaceView {
  constructor(base = 'http://localhost:8766') {
    this.base = base;
  }

  async resolveWebviewView(view) {
    view.webview.options = { enableScripts: true };
    const render = async () => { view.webview.html = await this.html(); };
    await render();
    // re-theme when the user switches VS Code themes
    const sub = vscode.window.onDidChangeActiveColorTheme(render);
    view.onDidDispose(() => sub.dispose());
  }

  async html() {
    const kind = vscode.window.activeColorTheme.kind;
    const light = kind === vscode.ColorThemeKind.Light || kind === vscode.ColorThemeKind.HighContrastLight;
    // asExternalUri makes localhost reachable from remote / Codespaces sessions too
    const src = await vscode.env.asExternalUri(
      vscode.Uri.parse(`${this.base}/surface?embed=1&theme=${light ? 'light' : 'dark'}`));
    const origin = `${src.scheme}://${src.authority}`;
    return `<!DOCTYPE html>
<html><head>
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; frame-src ${origin}; style-src 'unsafe-inline';">
<style>html,body,iframe{margin:0;padding:0;width:100%;height:100%;border:0;overflow:hidden;background:transparent}</style>
</head><body><iframe src="${src.toString(true)}" title="Big Brother"></iframe></body></html>`;
  }
}
SurfaceView.id = 'restraint.surface';

module.exports = { SurfaceView };
