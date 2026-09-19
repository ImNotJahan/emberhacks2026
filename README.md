# Big Brother

A VS Code extension that watches how you work and decides **when**, and almost never, to interrupt you with help.

Most AI coding tools work on *what* to say. Big Brother works on *when* to say it. It tells a developer who is converging on a fix or exploring on purpose (leave them alone) apart from one who is thrashing or blocked (a short nudge helps). There is no quota: each moment is judged on its own merits, and it stays silent by default. Every decision is logged, including every time it stayed silent and why. We call that log the restraint log.

Built for EmberHacks 2026.

## How it works

```
VS Code (extension.js)          capture engine (extention.py)            judge (judge/)
───────────────────────         ────────────────────────────────         ───────────────────────
edits, saves, focus,     ─raw─▶ redact → fingerprint → Event      ─HTTP─▶ snooze + cooldown gates  
diagnostics, terminal     JSONL  → episode → ring buffer → signals  :8765  → Gemini timing call
commands and output      stdin   → CandidateMoment                        → content call (if speaking)
                                                                          → logs/<session>.jsonl
```

1. **Collect**: [extension.js](extension.js). A thin collector with no logic of its own. It records edit *sizes* (never document text), saves, file opens and focus changes, large cursor jumps, error diagnostics, and terminal commands with their output (via the shell integration API). Each observation goes as one JSON line to the engine's stdin.
2. **Capture**: [extention.py](extention.py). The spelling is intentional, because `extension.js` launches it by that name. The pipeline:
   - Redacts secrets before anything is stored.
   - Fingerprints errors.
   - Splits activity into episodes. An episode ends after 45s idle, on a fail→pass test transition, on a commit, or on a context switch.
   - Detects signals: `repeated_error`, `file_thrash`, `stalled_with_error`, `test_loop`, and others.
   - Nominates a `CandidateMoment`. There is a gap of at least 60s between candidates, weak lone signals are dropped, and the same signal combination is suppressed for 3 minutes.

   Candidates are served on `http://127.0.0.1:8765`.
3. **Judge**: [judge/](judge/). For each candidate, it applies these checks in order:
   - If the developer snoozed it from the surface, it declines with `user_suppressed`.
   - If it spoke less than 240s ago, it declines with `too_soon`.
   - Otherwise it matches the terminal output against a playbook of known debugging patterns ([judge/knowledge.py](judge/knowledge.py)), such as an infinite loop stopped with Ctrl+C, exit codes, and common Python/JS/pytest errors. It then asks Gemini to classify the trajectory (converging / exploring / thrashing / blocked) and whether to speak.
   - Only when the answer is yes, a second call writes the message.

   If anything fails, the judge stays silent: a broken judge must never interrupt anyone.
4. **Surface**: Person 3. [p3_server.py](p3_server.py) is a small FastAPI "mailbox" on `http://localhost:8766`:
   - The live judge sends every `DecisionRecord` to it through [p3_send.py](p3_send.py).
   - The judge posts `considering` before each model call.
   - The judge checks the snooze state first; while snoozed it declines with `user_suppressed`.
   - The mailbox serves:
     - the intervention surface (`/surface`), shown as the **Big Brother** view in the Big Brother sidebar through [surface_webview.js](surface_webview.js)
     - the restraint-log dashboard (`/`, or **Big Brother: Open Dashboard**)
   - Feedback from either page comes back as `FeedbackRecord`s. The mailbox persists everything to `logs/mailbox.jsonl`.

   If the mailbox is down, the judge still decides and logs, and `p3_send` parks records in `unsent.jsonl`. The **temporary test surface** ([testSurface.js](testSurface.js)) also still shows decisions as notifications. Turn it off with `bigBrother.testSurface.enabled`.

## The contract

[contract.py](contract.py) is the boundary between the four workstreams. It uses only the standard library and depends on nothing else in the repo.

| Owner | Produces | Consumes |
|---|---|---|
| Person 1: Capture | `Event`, `Episode`, `Signal`, `ActivitySnapshot`, `CandidateMoment` | none |
| Person 2: Judge | `InterventionDecision`, `DecisionRecord` | `CandidateMoment` |
| Person 3: Surface | `FeedbackRecord` | `InterventionDecision`, `DecisionRecord` |
| Person 4: Eval | `LabeledMoment`, `EvalResult` | everything |

After hour 3, the contract changes only with all four people present. Bump `CONTRACT_VERSION` (currently `1.0.0`) on any breaking change.

`FeedbackKind` separates `bad_timing` (right thing, wrong moment) from `bad_content` (right moment, useless thing). That split is how the product thesis gets tested.

## Setup

```bash
pip install -r requirements.txt          # google-genai for the judge; fastapi + uvicorn for the surface mailbox
echo 'GEMINI_API_KEY=...' > .env         # read by judge/env.py; real env vars win
# optional: GEMINI_MODEL (default gemini-2.5-flash)
```

VS Code 1.93 or later is required. The terminal events depend on the shell integration API.

Extension settings (`bigBrother.*`):

| Setting | Default | Meaning |
|---|---|---|
| `autoStart` | `true` | Start the capture engine and judge when VS Code starts |
| `python` | *(empty)* | Python interpreter to use. When empty, the extension auto-detects one, trying in order: `.venv` or `venv` in the repo, then `python3`, `python`, `py` |
| `prompt` | `judge-v8` | Prompt version passed to the live judge |
| `redaction.enabled` | `true` | Strip secrets before anything leaves the editor process |
| `port` | `8765` | Port the capture engine listens on for candidates |
| `verbose` | `false` | Log every raw editor event to the output channel |
| `testSurface.enabled` | `true` | TEMPORARY: show decisions as notifications |

## Running it live (one click)

Open this repo in VS Code and press **F5** ("Run Big Brother"). In the window that opens, open the project you want to observe.

The **Big Brother** icon in the activity bar opens a sidebar with two views:
- **Control** shows:
  - whether the engine, judge and surface server are running in this window
  - the engine's event and candidate counts
  - the number of spoken and silent decisions, and the last decision with its reasoning
  - the Python interpreter, prompt version and redaction state
  - any setup problems

  It has **Start in this window**, **Stop** and **Restart** buttons.
- **Big Brother** is Person 3's intervention surface.

The extension then starts everything itself:
1. It finds a Python interpreter.
2. It checks that `google-genai` is installed and that `GEMINI_API_KEY` is set.
3. It starts the surface mailbox (`p3_server.py` on :8766), if fastapi and uvicorn are installed, and the capture engine (`extention.py`).
4. Once the engine is up, it starts the live judge (`judge.live`).

A notification confirms that Big Brother is running. The item at the left of the status bar always shows the current state:

| Status bar | Meaning |
|---|---|
| `⟳ Big Brother: starting…` | Checking setup and launching the processes |
| `👁 Big Brother · 1 spoke · 4 silent` | Running. The counts are decisions seen in this window |
| `⚠ Big Brother: capture only` (yellow) | The engine runs but the judge couldn't start. A notification offers **Install requirements** or **Open .env** |
| `✖ Big Brother: failed` (red) | A process died, or the port is already in use (Big Brother may be running in another window). The notification offers **Restart** |
| `⊘ Big Brother: off` | Stopped |

Click the status bar item for a menu with Start/Stop, Restart, Show log, Open decision log, Test notification and Settings. The same actions are available as **Big Brother: …** commands in the Command Palette.

- **Decisions:** when the judge speaks, you get a notification. Its **Why now?** button shows the reasoning, trajectory and signals. Silent decisions go only to the output channel (**Big Brother: Show Log**). Use **Test notification** to check that notifications appear. If none appear, turn off Do Not Disturb in the notifications bell.
- **Decision log:** each live session writes to `logs/live_<date>_<time>.jsonl`.
- **Trace files:** the engine writes these to the repo root, all gitignored:
  - `trace.raw.jsonl`: redacted raw observations, replayable
  - `trace.events.jsonl`: normalized `Event`s
  - `trace.candidates.jsonl`: nominated `CandidateMoment`s

  **They are appended to, so move them aside between recording sessions.**
- **Replays:** the notifications also fire for replays. While the extension is running, `python -m judge.replay traces/thrash.candidates.jsonl` makes its decisions appear in the editor, tagged `(replay: …)`. This is the fastest way to see the surface without real coding.

To run the pieces by hand instead, set `bigBrother.autoStart` to false and run:
- `python -m uvicorn p3_server:app --port 8766`
- `python extention.py --port 8765`
- `python -m judge.live --session NAME`

Pass `--no-mailbox` to `judge.live` to skip the surface. Replays send nothing to the mailbox unless you pass `--mailbox`. Panda's `p3_seed_demo.py` and `p3_live_demo.py` fill the surface with fake data without the judge.

Engine endpoints: `GET /health`, `/candidates?since=<ts>`, `/candidates/latest`, `/events`, `/redaction`, and `POST /budget`.

## Replay and evaluation

Everything is designed to replay deterministically. Time always comes from the trace (`candidate.ts`), never from the wall clock. The budget and cooldown therefore behave in a replay exactly as they did live.

```bash
# replay a trace through the judge -> logs/<name>__<prompt>.jsonl + readable view
python -m judge.replay traces/refactor.candidates.jsonl [--prompt judge-v6] [--no-content]
python -m judge.replay traces/stuck.raw.jsonl           # raw trace: runs through extention.py first

# capture engine only
python extention.py --replay traces/stuck.raw.jsonl --out candidates.jsonl

# read a decision log
python -m judge.log logs/<file>.jsonl

# score prompt versions against labeled traces
python -m judge.calibrate judge-v5 judge-v6   # appends calibration/results.jsonl, rebuilds RESULTS.md
python -m judge.calibrate --table             # rebuild the table only

# latency report -> calibration/LATENCY.md
python -m judge.latency [--prompt judge-v5] [--repeats 2]
```

### Trace formats ([traces/](traces/))

| File | Contents |
|---|---|
| `<name>.raw.jsonl` | Raw editor observations (`{"k": "<EventKind>", "ts": ..., ...}`), as produced by `extension.js` |
| `<name>.candidates.jsonl` | `CandidateMoment`s, one per line |
| `<name>.labels.jsonl` | `LabeledMoment`s: `{ts, should_have_spoken, rationale, tolerance_ms}` |

**Labels must cover every candidate.** Calibration matches each candidate to a label within `tolerance_ms` and crashes if a candidate has none.

### Metrics

These are computed in [judge/calibrate.py](judge/calibrate.py). Each prompt version is replayed 3 times with content generation off. Versions v1–v7 are scored with the old 3/hour budget, and v8+ without one.

- **False-positive rate (productive)**: how often it spoke on traces where the developer should be left alone. This is the headline number.
- **Hit rate**: how many of the labeled should-speak moments it spoke on. A `too_soon` hold right after a hit in the same episode counts as covered.
- **p50 / p95 latency**: from candidate to timing decision, counting model calls only.
- **Interventions per hour**: computed into `EvalResult`, and shown by `judge.eval` (below).

The latest results are in [calibration/RESULTS.md](calibration/RESULTS.md). Compare rows only when they share the same `traces` hash.

**Latency** (from [calibration/LATENCY.md](calibration/LATENCY.md)):
- Decision: p95 is 1.8s, under the 3s target.
- End-to-end including message generation: p95 is 2.4s.

### Current traces are mostly synthetic

The calibration set comes from [judge/synth.py](judge/synth.py) (`python -m judge.synth` regenerates it). These are placeholders until real recorded sessions exist.

Productive traces, where every label is false:
- `refactor`
- `tdd`
- `exploring`
- `parser`

Stuck traces:
- `thrash`
- `blocked`
- `p1_stuck`. This one is built from Person 1's `stuck.raw.jsonl` and is the only trace with real editor event shapes.

`budget30` is used only for the budget check. The 0% false positives and 100% hit rate in RESULTS.md apply to the synthetic set and should not be quoted as real-world accuracy.

### End-to-end evaluation ([traces/eval/](traces/eval/))

Calibration scores the judge alone on hand-built candidates. `judge.eval` scores **capture and judge together**. It runs raw editor traces through `extention.py` and then the judge, and matches the decisions to hand-labelled *moments*: points where a nudge would have helped, or would have been infuriating. A should-speak moment with no candidate near it counts as a capture miss.

```bash
python -m judge.eval --capture-only   # free, offline: which should-speak moments get a candidate at all
python -m judge.eval                  # + the judge (content off); decision logs cached in logs/eval_*.jsonl
python -m judge.eval dogfood --fresh  # one session, re-run the judge
python -m judge.scenarios             # regenerate the scripted sessions
```

Output goes to [calibration/EVAL.md](calibration/EVAL.md): per-session capture recall, hit rate, infuriating rate, FP on productive sessions, interventions per hour, p95 latency, and lag, followed by every miss attributed to capture or judge.

- The sessions are one **real** capture (`dogfood`, 41 min) and six **scripted** raw traces, 16–26 min each, from [judge/scenarios.py](judge/scenarios.py): 62 labels in total.
- [traces/eval/SCENARIOS.md](traces/eval/SCENARIOS.md) is the catalogue of should-speak (S1–S28) and stay-silent (Q1–Q18) moments to label against.
- The scripted sessions are realistic in shape but written by us. Real recordings replace them.

To add a real session, use [recording_kit/](recording_kit/):
- [BRIEF.md](recording_kit/BRIEF.md): operator checklist and the volunteer tasks
- `broken_shop/`: four planted bugs plus a feature task
- `save_session.sh`: moves the engine output into `traces/eval/<name>.raw.jsonl` and prints the `echo SYNC` timestamp used to line the video up with the trace

Then label the session following SCENARIOS.md and add it to `traces/eval/manifest.json`.

## Tests and checks

```bash
python test_capture.py                  # capture engine unit tests (synthetic sessions, memory bounds)
python test_capture.py --write-traces   # regenerate traces/*.raw.jsonl
python -m judge.smoke                   # 10 structured Gemini calls on a fixture; non-zero exit on any invalid response
python -m judge.checks ambiguous|budget|refactor [--prompt ...]   # judge acceptance checks
python extention.py --demo-redact 'api_key = "sk-abc123..."'      # show which redaction rules fire
```

## Prompt versions

The prompts are defined in [judge/prompts.py](judge/prompts.py). Each version adds to the one before it.

| Version | Change |
|---|---|
| `judge-v1` | Cost-benefit framing; stays silent by default |
| `judge-v2` | Adds awareness of the budget running out, and memory of the last interruption |
| `judge-v3` | Adds a two-stage classification of converging vs thrashing |
| `judge-v4` | Counts attempts; fewer than 4 attempts with the same failure is never thrashing |
| `judge-v5` | Makes the counts schema fields the trajectory rule depends on |
| `judge-v6` | Adds guidance for real editor data, which records edit sizes only |
| `judge-v7` | Adds known-pattern hints from `judge/knowledge.py`. Fixes the judge reading an infinite loop stopped with Ctrl+C as "a KeyboardInterrupt error" |
| `judge-v8` | Removes the interruption budget. Judges each moment on its merits and keeps memory of past interruptions |

`LATEST` is `judge-v8`. The content generator (`content-v4`) always gets the known-pattern hints.

A 240s cooldown after speaking is still in place (`too_soon`), so a stuck episode doesn't produce a message every minute. Pass `cooldown_s=0` to `Judge` to remove it. `--per-hour N` on `judge.live`/`judge.replay` brings the old budget back. `judge.checks budget` only makes sense with v1–v7, e.g. `--prompt judge-v7`.

## Privacy

- Document text never leaves the editor. Edits are sent as character counts only.
- Paths outside the workspace are reduced to their basename.
- Redaction runs before an `Event` is constructed, and before the raw trace is written to disk. It covers:
  - key/token/password literals
  - common token formats (`sk-`, `AIza`, `ghp_`, AWS keys, JWTs, and others)
  - Bearer headers
  - credentials in URLs
  - `.env` file contents
- Terminal output is capped at 1500 characters per event.

## Known limitations

- **Telling exploration from being stuck is hard on real traces.** The judge is calibrated at 100% hit rate on synthetic candidates. End to end on realistic raw traces ([calibration/EVAL.md](calibration/EVAL.md)) it interrupts none of 41 leave-alone moments, but it catches only about 29% of should-speak ones:
  - about half of the misses are capture: rapid re-runs are merged, the same signal is suppressed for 3 minutes, and there is no stall detector for terminal errors
  - the other half are the judge calling clear thrash `still_converging`
- **The surface only shows the most recent session.** A live session and a replay sent with `--mailbox` share one mailbox. Pass `?session=<id>` to pick a session.
- **The judge doesn't read feedback yet.** `FeedbackRecord`s (`bad_timing`, `wanted_help`, …) are stored for evaluation but don't change the judge's behaviour.
- **No personalization.** The thresholds (cooldown, signal gates) are the same for everyone.
- **Single-language scope.** Error fingerprinting and test-count parsing are built around Python and pytest output.
- **Terminals without shell integration produce no events.** An example is `cmd.exe`.
- **Frequent interventions risk learned helplessness.** With the budget removed, only the judge's cost-benefit bar and the cooldown prevent this.

## Repo layout

```
contract.py            shared data contract (stdlib only)
extension.js           VS Code collector + command wiring
runner.js              one-click lifecycle: Python detection, setup checks, engine + judge processes, status bar
testSurface.js         TEMPORARY decision notifications (still on alongside the real surface)
surface_webview.js     hosts the surface page in the Big Brother sidebar
controlView.js         sidebar Control view: start/stop in this window + status
p3_server.py           surface mailbox: /log, /records, /considering, /snooze, /state, pages
p3_send.py             stdlib client the judge uses to reach the mailbox
dashboard.html         restraint-log timeline (served at /)
surface.html           intervention surface (served at /surface)
p3_seed_demo.py, p3_live_demo.py   fake data for the surface
extention.py           capture engine: redaction, episodes, signals, HTTP endpoint
package.json           extension manifest
test_capture.py        capture engine tests + synthetic raw traces
judge/
  core.py              Judge.decide(): gates → timing call → content call → log
  prompts.py           prompt versions + JSON schemas
  render.py            CandidateMoment → compact prompt text
  gemini.py            the single place model calls happen (timed)
  content.py           what to say, once the judge decides to speak
  budget.py            token bucket, N/hour, trace-clock driven (per_hour=None: counts only)
  knowledge.py         baseline debugging knowledge: known-pattern hints
  log.py               decision log writer/reader
  schema.py            model verdict validation
  codec.py             JSON → contract objects
  live.py              live loop against the capture engine
  replay.py            trace → decision log
  calibrate.py         scoring across prompt versions
  eval.py              end-to-end scoring: raw trace → capture → judge vs labelled moments
  scenarios.py         scripted raw sessions + moment labels for eval
  latency.py           latency report
  checks.py, smoke.py  acceptance checks
  synth.py, fixtures.py  synthetic traces and fixtures
traces/                recorded + synthetic traces and labels
  eval/                eval sessions: raw traces, moment labels, manifest, SCENARIOS.md
recording_kit/         broken_shop (planted bugs), recording brief, save_session.sh
calibration/           scoring history (results.jsonl, RESULTS.md, LATENCY.md, EVAL.md)
logs/                  decision logs (gitignored, regenerated)
```
