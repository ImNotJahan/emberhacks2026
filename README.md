# Big Brother

A VS Code extension that watches how you work and decides **when**, and almost never, to interrupt you with help.

Most AI coding tools work on *what* to say. Big Brother works on *when* to say it. It tells a developer who is converging on a fix or exploring on purpose (leave them alone) apart from one who is thrashing or blocked (a short nudge helps). There is no quota: each moment is judged on its own merits, and it stays silent by default. Every decision is logged, including every time it stayed silent and why. We call that log the restraint log.

Built for EmberHacks 2026.

For the pipeline internals, settings, replay and evaluation, prompt versions, known limitations and repo layout, see [details.md](details.md).

## How it works

```
VS Code (extension.js)          capture engine (extention.py)            judge (judge/)
───────────────────────         ────────────────────────────────         ───────────────────────
edits, saves, focus,     ─raw─▶ redact → fingerprint → Event      ─HTTP─▶ snooze + cooldown gates  
diagnostics, terminal     JSONL  → episode → ring buffer → signals  :8765  → Gemini timing call
commands and output      stdin   → CandidateMoment                        → content call (if speaking)
                                                                          → logs/<session>.jsonl
```

1. **Collect**: [extension.js](extension.js) records edit *sizes* (never document text), saves, focus changes, error diagnostics, and terminal commands with their output.
2. **Capture**: [extention.py](extention.py) redacts secrets, groups activity into episodes, detects signals such as `repeated_error` and `file_thrash`, and nominates `CandidateMoment`s.
3. **Judge**: [judge/](judge/) asks Gemini to classify the trajectory (converging / exploring / thrashing / blocked) and whether to speak. Only when the answer is yes does a second call write the message. If anything fails, it stays silent.
4. **Surface**: [p3_server.py](p3_server.py) serves the intervention surface in the Big Brother sidebar and the restraint-log dashboard, and collects feedback.

## Setup

```bash
pip install -r requirements.txt          # google-genai for the judge; fastapi + uvicorn for the surface mailbox
echo 'GEMINI_API_KEY=...' > .env         # read by judge/env.py; real env vars win
# optional: GEMINI_MODEL (default gemini-2.5-flash)
```

VS Code 1.93 or later is required. The terminal events depend on the shell integration API.

## Running it live (one click)

Open this repo in VS Code and press **F5** ("Run Big Brother"). In the window that opens, open the project you want to observe.

The extension finds a Python interpreter, checks your setup, and starts the surface mailbox, the capture engine and the live judge. The status bar item on the left shows the current state; click it for Start/Stop, Restart and the logs.

The **Big Brother** icon in the activity bar opens a sidebar with two views:
- **Control**: what is running, event and decision counts, the last decision with its reasoning, and any setup problems.
- **Big Brother**: the intervention surface.

When the judge speaks, you get a notification. Its **Why now?** button shows the reasoning. To see it work without real coding, run `python -m judge.replay traces/thrash.candidates.jsonl` while the extension is running.

## Privacy

- Document text never leaves the editor. Edits are sent as character counts only.
- Secrets are redacted before anything is stored or written to disk.
