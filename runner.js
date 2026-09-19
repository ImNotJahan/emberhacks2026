// runner.js — one-click lifecycle: finds Python, checks setup, starts the
// capture engine (extention.py), Person 3's surface mailbox (p3_server.py) and
// the live judge (judge.live), and keeps a status bar item that always says
// what state Restraint is in.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const MAILBOX_PORT = 8766;   // p3_server.py; surface_webview.js and p3_send.py assume it too

const STATE = {
  starting: { icon: '$(loading~spin)', bg: undefined },
  running: { icon: '$(eye)', bg: undefined },
  degraded: { icon: '$(warning)', bg: 'statusBarItem.warningBackground' },
  stopped: { icon: '$(circle-slash)', bg: undefined },
  error: { icon: '$(error)', bg: 'statusBarItem.errorBackground' },
};

class Runner {
  constructor(context, log) {
    this.context = context;
    this.dir = context.extensionPath;
    this.log = log;
    this.engine = null;
    this.judge = null;
    this.mailbox = null;
    this.python = null;
    this.session = null;
    this.state = 'stopped';
    this.detail = '';
    this.spoke = 0;
    this.silent = 0;
    this.announced = false;
    this.lastDecision = null;
    this.startedAt = null;
    this.listeners = new Set();

    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 1000);
    this.status.command = 'restraint.menu';
    this.status.show();
    context.subscriptions.push(this.status, { dispose: () => this.stop(true) });
    this.render();
  }

  cfg() { return vscode.workspace.getConfiguration('restraint'); }

  // --- for the sidebar Control view ---------------------------------------

  onChange(fn) { this.listeners.add(fn); }

  snapshot() {
    const cfg = this.cfg();
    return {
      state: this.state, detail: this.detail, python: this.python, session: this.session,
      spoke: this.spoke, silent: this.silent, lastDecision: this.lastDecision,
      startedAt: this.startedAt, judgeProblem: this.judgeProblem || null,
      procs: { engine: !!this.engine, judge: !!this.judge, mailbox: !!this.mailbox },
      port: cfg.get('port', 8765), mailboxPort: MAILBOX_PORT,
      prompt: cfg.get('prompt', ''), redaction: cfg.get('redaction.enabled', true),
    };
  }

  // judge.live prints "[<ms>ms] SPEAK: <content>" or "[<ms>ms] silent (<reason>)",
  // then "    why: <reasoning>". Counted here, so the numbers are this window's judge.
  parseJudgeLine(line) {
    const m = /^\[(\d+)ms\] (?:SPEAK: (.*)|silent \((\w+)\))$/.exec(line);
    if (m) {
      const spoke = m[2] !== undefined;
      this.lastDecision = { ts: Date.now(), ms: Number(m[1]), spoke, content: m[2] || null, reason: m[3] || null, why: '' };
      this.noteDecision(spoke);
      return;
    }
    const w = /^\s+why: (.*)$/.exec(line);
    if (w && this.lastDecision && !this.lastDecision.why) {
      this.lastDecision.why = w[1];
      this.render();
    }
  }

  setState(state, detail = '') {
    this.state = state;
    this.detail = detail;
    this.render();
  }

  render() {
    const s = STATE[this.state];
    const label = {
      starting: 'Restraint: starting…',
      running: `Restraint · ${this.spoke} spoke · ${this.silent} silent`,
      degraded: 'Restraint: capture only',
      stopped: 'Restraint: off',
      error: 'Restraint: failed',
    }[this.state];
    this.status.text = `${s.icon} ${label}`;
    this.status.backgroundColor = s.bg ? new vscode.ThemeColor(s.bg) : undefined;
    const lines = [`**Restraint** — ${this.state}`];
    if (this.detail) lines.push(this.detail);
    if (this.python) lines.push(`python: \`${this.python}\``);
    if (this.session) lines.push(`decision log: \`logs/${this.session}.jsonl\``);
    lines.push('', 'Click for options');
    this.status.tooltip = new vscode.MarkdownString(lines.join('\n\n'));
    for (const fn of this.listeners) { try { fn(); } catch (e) { this.log('status listener: ' + e.message); } }
  }

  noteDecision(spoke) {
    if (spoke) this.spoke++; else this.silent++;
    this.render();
  }

  // --- setup checks -------------------------------------------------------

  findPython() {
    const configured = this.cfg().get('python', '');
    const candidates = configured ? [configured] : [
      path.join(this.dir, '.venv', 'bin', 'python'),
      path.join(this.dir, 'venv', 'bin', 'python'),
      path.join(this.dir, '.venv', 'Scripts', 'python.exe'),
      path.join(this.dir, 'venv', 'Scripts', 'python.exe'),
      'python3', 'python', 'py',
    ];
    for (const c of candidates) {
      if (path.isAbsolute(c) && !fs.existsSync(c)) continue;
      const r = spawnSync(c, ['-c', 'import sys; assert sys.version_info >= (3, 9)'], { cwd: this.dir, timeout: 5000 });
      if (r.status === 0) return c;
    }
    return null;
  }

  hasGenai() {
    const r = spawnSync(this.python, ['-c', 'import google.genai'], { cwd: this.dir, timeout: 15000 });
    return r.status === 0;
  }

  hasMailboxDeps() {
    const r = spawnSync(this.python, ['-c', 'import fastapi, uvicorn'], { cwd: this.dir, timeout: 15000 });
    return r.status === 0;
  }

  hasApiKey() {
    if (process.env.GEMINI_API_KEY) return true;
    try {
      return /^\s*GEMINI_API_KEY\s*=\s*['"]?\S+/m.test(fs.readFileSync(path.join(this.dir, '.env'), 'utf8'));
    } catch { return false; }
  }

  // --- lifecycle ----------------------------------------------------------

  start() {
    if (this.engine || this.judge) return;
    this.spoke = this.silent = 0;
    this.lastDecision = null;
    this.startedAt = Date.now();
    this.setState('starting', 'Checking setup');

    this.python = this.findPython();
    if (!this.python) {
      this.setState('error', 'No Python 3.9+ found');
      vscode.window.showErrorMessage('Restraint: no Python 3.9+ found. Set "restraint.python" to your interpreter.', 'Open Settings')
        .then(p => p && vscode.commands.executeCommand('workbench.action.openSettings', 'restraint.python'));
      return;
    }
    this.log(`using python: ${this.python}`);

    const judgeProblem = !this.hasGenai() ? 'deps' : !this.hasApiKey() ? 'key' : null;
    this.startMailbox();
    this.startEngine();
    if (judgeProblem) {
      const msg = judgeProblem === 'deps'
        ? 'Judge not started: google-genai is not installed for this Python.'
        : 'Judge not started: GEMINI_API_KEY is missing from .env.';
      this.judgeProblem = msg;
      const action = judgeProblem === 'deps' ? 'Install requirements' : 'Open .env';
      vscode.window.showWarningMessage(`Restraint: ${msg} Capture still runs.`, action, 'Show log').then(p => {
        if (p === 'Install requirements') this.installRequirements();
        if (p === 'Open .env') this.openEnv();
        if (p === 'Show log') vscode.commands.executeCommand('restraint.showLog');
      });
    } else {
      this.judgeProblem = null;
    }
  }

  spawnPy(name, args, onLine) {
    const proc = spawn(this.python, args, { cwd: this.dir, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
    const pipe = (stream, tag) => {
      let buf = '';
      stream.on('data', d => {
        buf += String(d);
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trimEnd();
          buf = buf.slice(i + 1);
          if (!line) continue;
          this.log(`${name}${tag}: ${line}`);
          onLine && onLine(line);
        }
      });
    };
    pipe(proc.stdout, '');
    pipe(proc.stderr, '!');
    proc.on('error', e => this.log(`${name} failed to start: ${e.message}`));
    return proc;
  }

  startEngine() {
    const cfg = this.cfg();
    const port = String(cfg.get('port', 8765));
    const args = [path.join(this.dir, 'extention.py'), '--port', port,
      '--record', path.join(this.dir, 'trace.raw.jsonl'),
      '--events-out', path.join(this.dir, 'trace.events.jsonl'),
      '--out', path.join(this.dir, 'trace.candidates.jsonl')];
    if (!cfg.get('redaction.enabled', true)) args.push('--no-redact');

    this.engine = this.spawnPy('engine', args, line => {
      if (line.startsWith('engine up:')) {
        if (this.judgeProblem) this.setState('degraded', this.judgeProblem);
        else this.startJudge(port);
      }
      if (/Address already in use|Errno 48|Errno 98|WinError 10048/.test(line)) {
        this.portInUse = true;
      }
    });
    const engine = this.engine;
    engine.on('exit', code => {
      if (this.engine !== engine) return;     // stopped on purpose, or replaced by a restart
      this.engine = null;
      const why = this.portInUse
        ? `Port ${port} is already in use — is Restraint running in another window?`
        : `Capture engine exited (code ${code}).`;
      this.portInUse = false;
      this.fail(why);
    });
  }

  // The surface's server. Optional: without it the judge still decides and
  // logs, and p3_send.py parks records in unsent.jsonl.
  startMailbox() {
    if (!this.hasMailboxDeps()) {
      this.log('mailbox not started: fastapi/uvicorn missing (pip install -r requirements.txt); the surface will be empty');
      return;
    }
    this.mailbox = this.spawnPy('mailbox', ['-m', 'uvicorn', 'p3_server:app',
      '--host', '127.0.0.1', '--port', String(MAILBOX_PORT), '--log-level', 'warning']);
    const mailbox = this.mailbox;
    mailbox.on('exit', code => {
      if (this.mailbox !== mailbox) return;
      this.mailbox = null;
      // Not fatal: most likely another window (or a manual run) already owns the port.
      this.log(`mailbox exited (code ${code}); if port ${MAILBOX_PORT} is taken, the existing server is used`);
    });
  }

  openDashboard() {
    vscode.env.openExternal(vscode.Uri.parse(`http://localhost:${MAILBOX_PORT}/`));
  }

  startJudge(port) {
    const cfg = this.cfg();
    const n = new Date(), pad = x => String(x).padStart(2, '0');   // local time, matches the clock on the wall
    const stamp = `${n.getFullYear()}${pad(n.getMonth() + 1)}${pad(n.getDate())}_${pad(n.getHours())}${pad(n.getMinutes())}${pad(n.getSeconds())}`;
    this.session = `live_${stamp}`;
    // No --per-hour: since judge-v8 there is no interruption budget.
    const args = ['-m', 'judge.live', '--port', port, '--session', this.session];
    const prompt = cfg.get('prompt', '');
    if (prompt) args.push('--prompt', prompt);

    this.judge = this.spawnPy('judge', args, line => {
      if (line.startsWith('judge live:')) this.ready();
      else this.parseJudgeLine(line);
    });
    const judge = this.judge;
    judge.on('exit', code => {
      if (this.judge !== judge) return;
      this.judge = null;
      this.fail(`Judge exited (code ${code}).`);
    });
  }

  ready() {
    this.setState('running', 'Watching this workspace. Decisions appear as notifications.');
    if (this.announced) return;
    this.announced = true;
    vscode.window.showInformationMessage(
      'Restraint is running: it watches how you work and will rarely interrupt. Decisions appear here as notifications.',
      'Test notification', 'Show log',
    ).then(p => {
      if (p === 'Test notification') vscode.commands.executeCommand('restraint.testNotification');
      if (p === 'Show log') vscode.commands.executeCommand('restraint.showLog');
    });
  }

  fail(why) {
    this.stop();
    this.setState('error', why);
    vscode.window.showErrorMessage(`Restraint stopped: ${why}`, 'Restart', 'Show log').then(p => {
      if (p === 'Restart') this.restart();
      if (p === 'Show log') vscode.commands.executeCommand('restraint.showLog');
    });
  }

  stop(silent = false) {
    const procs = [this.judge, this.engine, this.mailbox];
    this.judge = this.engine = this.mailbox = null;         // exit handlers ignore procs no longer current
    for (const p of procs) if (p) p.kill();
    this.startedAt = null;
    if (!silent) this.setState('stopped');
  }

  restart() {
    this.stop();
    setTimeout(() => this.start(), 500);    // let the port free up
  }

  write(line) {
    if (this.engine && this.engine.stdin.writable) this.engine.stdin.write(line + '\n');
  }

  // --- helpers used by the menu -------------------------------------------

  installRequirements() {
    const t = vscode.window.createTerminal({ name: 'Restraint setup', cwd: this.dir });
    t.show();
    t.sendText(`"${this.python}" -m pip install -r requirements.txt`);
    vscode.window.showInformationMessage('Restraint: after the install finishes, choose Restart from the status bar.', 'Restart')
      .then(p => p && this.restart());
  }

  openEnv() {
    const p = path.join(this.dir, '.env');
    if (!fs.existsSync(p)) fs.writeFileSync(p, 'GEMINI_API_KEY=\n');
    vscode.window.showTextDocument(vscode.Uri.file(p));
  }

  openDecisionLog() {
    if (!this.session) return vscode.window.showInformationMessage('Restraint: no live decision log yet.');
    const p = path.join(this.dir, 'logs', `${this.session}.jsonl`);
    if (!fs.existsSync(p)) return vscode.window.showInformationMessage('Restraint: no decisions logged yet.');
    vscode.window.showTextDocument(vscode.Uri.file(p));
  }

  async menu() {
    const running = !!(this.engine || this.judge);
    const items = [
      running ? { label: '$(debug-stop) Stop', id: 'stop' } : { label: '$(play) Start', id: 'start' },
      { label: '$(debug-restart) Restart', id: 'restart' },
      { label: '$(output) Show log', id: 'log' },
      { label: '$(graph) Open dashboard', id: 'dashboard', description: `localhost:${MAILBOX_PORT}` },
      { label: '$(list-flat) Open decision log', id: 'decisions', description: this.session ? `logs/${this.session}.jsonl` : '' },
      { label: '$(bell) Test notification', id: 'test' },
      { label: '$(gear) Settings', id: 'settings' },
    ];
    const pick = await vscode.window.showQuickPick(items, { placeHolder: `Restraint — ${this.state}${this.detail ? ': ' + this.detail : ''}` });
    if (!pick) return;
    ({
      stop: () => this.stop(),
      start: () => this.start(),
      restart: () => this.restart(),
      log: () => vscode.commands.executeCommand('restraint.showLog'),
      decisions: () => this.openDecisionLog(),
      dashboard: () => this.openDashboard(),
      test: () => vscode.commands.executeCommand('restraint.testNotification'),
      settings: () => vscode.commands.executeCommand('workbench.action.openSettings', 'restraint'),
    })[pick.id]();
  }
}

module.exports = { Runner };
