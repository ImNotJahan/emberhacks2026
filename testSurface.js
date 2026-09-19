// testSurface.js — TEMPORARY stand-in for Person 3's surface. Testing only.
// Tails logs/*.jsonl (written by judge.live and judge.replay) and shows each
// new decision: SPEAK as a notification, silence in the Restraint output
// channel. Delete this file and its lines in extension.js once the real
// surface lands.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

const POLL_MS = 1000;

function start(context, log, onDecision = () => {}) {
  const dir = path.join(context.extensionPath, 'logs');
  const offsets = new Map();     // file -> bytes already read
  let first = true;              // skip history that existed before activation

  function show(rec, source) {
    const d = rec.decision || {};
    const signals = ((rec.candidate && rec.candidate.signals) || []).map(s => s.kind).join(', ') || 'none';
    const tag = `[${source}] ${d.trajectory} conf=${Number(d.confidence).toFixed(2)} ${d.latency_ms ?? '-'}ms`;
    if (source !== 'live_test') onDecision(!!d.should_speak);
    if (!d.should_speak) {
      log(`decision silent (${d.decline_reason}) ${tag}\n    signals: ${signals}\n    why: ${d.reasoning}`);
      return;
    }
    log(`decision SPEAK ${tag}\n    signals: ${signals}\n    why: ${d.reasoning}\n    says: ${d.content}`);
    const from = source.startsWith('live_') ? '' : ` (replay: ${source})`;
    vscode.window.showInformationMessage(`Restraint${from}: ${d.content || '(no content generated)'}`, 'Why now?', 'Show log')
      .then(pick => {
        if (pick === 'Why now?') {
          vscode.window.showInformationMessage('Why Restraint spoke now', {
            modal: true,
            detail: `${d.reasoning}\n\nTrajectory: ${d.trajectory} (confidence ${Number(d.confidence).toFixed(2)})\nSignals: ${signals}`,
          });
        }
        if (pick === 'Show log') vscode.commands.executeCommand('restraint.showLog');
      });
  }

  function poll() {
    let files;
    try { files = fs.readdirSync(dir).filter(f => f.endsWith('.jsonl')); } catch { return; }
    for (const f of files) {
      const p = path.join(dir, f);
      let size;
      try { size = fs.statSync(p).size; } catch { continue; }
      if (!offsets.has(p)) offsets.set(p, first ? size : 0);
      let from = offsets.get(p);
      if (size < from) from = 0;                 // replay truncates/recreates the log
      if (size === from) continue;
      const buf = Buffer.alloc(size - from);
      const fd = fs.openSync(p, 'r');
      try { fs.readSync(fd, buf, 0, buf.length, from); } finally { fs.closeSync(fd); }
      const text = buf.toString('utf8');
      const end = text.lastIndexOf('\n');        // only consume complete lines
      if (end < 0) continue;
      offsets.set(p, from + Buffer.byteLength(text.slice(0, end + 1)));
      for (const line of text.slice(0, end).split('\n')) {
        if (!line.trim()) continue;
        try { show(JSON.parse(line), path.basename(f, '.jsonl')); } catch (e) { log(`test surface: bad line in ${f}: ${e.message}`); }
      }
    }
    first = false;
  }

  poll();
  const timer = setInterval(poll, POLL_MS);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
  log('test surface: tailing ' + dir);

  return {
    // Fake SPEAK decision, to check notifications work without waiting for the judge.
    test() {
      show({
        candidate: { signals: [{ kind: 'repeated_error' }, { kind: 'test_loop' }] },
        decision: {
          should_speak: true, trajectory: 'thrashing', confidence: 0.9, latency_ms: 0,
          reasoning: 'This is a test notification. Real ones come from the judge.',
          content: 'Test notification: if you can see this, decisions will show up here.',
        },
      }, 'live_test');
    },
  };
}

module.exports = { start };
