# Restraint

A VS Code extension that watches how you work and decides **when**, and almost never, to interrupt you with help.

Most AI coding tools work on *what* to say. Restraint works on *when* to say it. It tells a developer who is converging on a fix or exploring on purpose (leave them alone) apart from one who is thrashing or blocked (a short nudge helps). It is capped at 3 interruptions per hour. Every decision is logged, including every time it stayed silent and why. We call that log the restraint log.

Built for EmberHacks 2026.

## How it works

```
VS Code (extension.js)          capture engine (extention.py)            judge (judge/)
───────────────────────         ────────────────────────────────         ───────────────────────
edits, saves, focus,     ─raw─▶ redact → fingerprint → Event      ─HTTP─▶ budget + cooldown gates
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
   - If the token bucket is empty, it declines with `budget_exhausted`.
   - If it spoke less than 240s ago, it declines with `too_soon`.
   - Otherwise it asks Gemini to classify the trajectory (converging / exploring / thrashing / blocked) and whether to speak.
   - Only when the answer is yes, a second call writes the message.

   If anything fails, the judge stays silent: a broken judge must never interrupt anyone.
4. **Surface**: *not built yet* (Person 3). This part will read `logs/<session>.jsonl`, show interventions in the editor, render the timeline, and write `FeedbackRecord`s.

   Until then, a **temporary test surface** ([testSurface.js](testSurface.js)) watches `logs/*.jsonl` for new lines and shows them in the editor. See [Running it live](#running-it-live-one-click).

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
pip install -r requirements.txt          # google-genai; everything else is stdlib
echo 'GEMINI_API_KEY=...' > .env         # read by judge/env.py; real env vars win
# optional: GEMINI_MODEL (default gemini-2.5-flash)
```

VS Code 1.93 or later is required. The terminal events depend on the shell integration API.

Extension settings (`restraint.*`):

| Setting | Default | Meaning |
|---|---|---|
| `autoStart` | `true` | Start the capture engine and judge when VS Code starts |
| `python` | *(empty)* | Python interpreter to use. When empty, the extension auto-detects one, trying in order: `.venv` or `venv` in the repo, then `python3`, `python`, `py` |
| `prompt` | `judge-v6` | Prompt version passed to the live judge |
| `perHour` | `3` | Interruption budget (maximum interventions per hour) |
| `redaction.enabled` | `true` | Strip secrets before anything leaves the editor process |
| `port` | `8765` | Port the capture engine listens on for candidates |
| `verbose` | `false` | Log every raw editor event to the output channel |
| `testSurface.enabled` | `true` | TEMPORARY: show decisions as notifications |

## Running it live (one click)

Open this repo in VS Code and press **F5** ("Run Restraint"). In the window that opens, open the project you want to observe.

The extension then starts everything itself:
1. It finds a Python interpreter.
2. It checks that `google-genai` is installed and that `GEMINI_API_KEY` is set.
3. It starts the capture engine (`extention.py`).
4. Once the engine is up, it starts the live judge (`judge.live`).

A notification confirms that Restraint is running. The item at the left of the status bar always shows the current state:

| Status bar | Meaning |
|---|---|
| `⟳ Restraint: starting…` | Checking setup and launching the processes |
| `👁 Restraint · 1 spoke · 4 silent` | Running. The counts are decisions seen in this window |
| `⚠ Restraint: capture only` (yellow) | The engine runs but the judge couldn't start. A notification offers **Install requirements** or **Open .env** |
| `✖ Restraint: failed` (red) | A process died, or the port is already in use (Restraint may be running in another window). The notification offers **Restart** |
| `⊘ Restraint: off` | Stopped |

Click the status bar item for a menu with Start/Stop, Restart, Show log, Open decision log, Test notification and Settings. The same actions are available as **Restraint: …** commands in the Command Palette.

- **Decisions:** when the judge speaks, you get a notification. Its **Why now?** button shows the reasoning, trajectory and signals. Silent decisions go only to the output channel (**Restraint: Show Log**). Use **Test notification** to check that notifications appear. If none appear, turn off Do Not Disturb in the notifications bell.
- **Decision log:** each live session writes to `logs/live_<date>_<time>.jsonl`.
- **Trace files:** the engine writes these to the repo root, all gitignored:
  - `trace.raw.jsonl`: redacted raw observations, replayable
  - `trace.events.jsonl`: normalized `Event`s
  - `trace.candidates.jsonl`: nominated `CandidateMoment`s

  **They are appended to, so move them aside between recording sessions.**
- **Replays:** the notifications also fire for replays. While the extension is running, `python -m judge.replay traces/thrash.candidates.jsonl` makes its decisions appear in the editor, tagged `(replay: …)`. This is the fastest way to see the surface without real coding.

To run the pieces by hand instead, set `restraint.autoStart` to false and run `python extention.py --port 8765` and `python -m judge.live --session NAME`.

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

These are computed in [judge/calibrate.py](judge/calibrate.py). Each prompt version is replayed 3 times, with the budget at 3/hour and content generation off.

- **False-positive rate (productive)**: how often it spoke on traces where the developer should be left alone. This is the headline number.
- **Hit rate**: how many of the labeled should-speak moments it spoke on. A `too_soon` hold right after a hit in the same episode counts as covered.
- **p50 / p95 latency**: from candidate to timing decision, counting model calls only.
- **Interventions per hour**: computed into `EvalResult` but not yet shown in the table.

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

To add a real session:
1. Record it live, as described in [Running it live](#running-it-live-one-click).
2. Copy it to `traces/<name>.raw.jsonl`.
3. Produce `<name>.candidates.jsonl` with `extention.py --replay ... --out`.
4. Hand-label it into `<name>.labels.jsonl`. Label timestamps are epoch ms: note the wall-clock time the screen recording starts, then add the video offset to it.
5. Add the name to `CALIBRATION`, and to `PRODUCTIVE` if applicable, in [judge/synth.py](judge/synth.py).

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

`LATEST` is still `judge-v5`, so the CLIs use it by default. Pass `--prompt judge-v6` to use the newest version.

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

- **Telling exploration from being stuck is hard on real traces.** The judge has only been calibrated on synthetic traces.
- **The surface layer and timeline don't exist yet.** Decisions only appear in the terminal and in the JSONL logs.
- **No personalization.** The thresholds (budget, cooldown, signal gates) are the same for everyone.
- **Single-language scope.** Error fingerprinting and test-count parsing are built around Python and pytest output.
- **Terminals without shell integration produce no events.** An example is `cmd.exe`.
- **Frequent interventions risk learned helplessness.** This is why the budget exists.
- **No `npm` scripts.** Outside the F5 flow, every command is a Python module invocation.

## Repo layout

```
contract.py            shared data contract (stdlib only)
extension.js           VS Code collector + command wiring
runner.js              one-click lifecycle: Python detection, setup checks, engine + judge processes, status bar
testSurface.js         TEMPORARY decision notifications (until the real surface lands)
extention.py           capture engine: redaction, episodes, signals, HTTP endpoint
package.json           extension manifest
test_capture.py        capture engine tests + synthetic raw traces
judge/
  core.py              Judge.decide(): gates → timing call → content call → log
  prompts.py           prompt versions + JSON schemas
  render.py            CandidateMoment → compact prompt text
  gemini.py            the single place model calls happen (timed)
  content.py           what to say, once the judge decides to speak
  budget.py            token bucket, N/hour, trace-clock driven
  log.py               decision log writer/reader
  schema.py            model verdict validation
  codec.py             JSON → contract objects
  live.py              live loop against the capture engine
  replay.py            trace → decision log
  calibrate.py         scoring across prompt versions
  latency.py           latency report
  checks.py, smoke.py  acceptance checks
  synth.py, fixtures.py  synthetic traces and fixtures
traces/                recorded + synthetic traces and labels
calibration/           scoring history (results.jsonl, RESULTS.md, LATENCY.md)
logs/                  decision logs (gitignored, regenerated)
```
