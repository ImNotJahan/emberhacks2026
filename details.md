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

- **Telling exploration from being stuck is hard on real traces.** The judge has only been calibrated on synthetic traces.
- **The surface only shows the most recent session.** A live session and a replay sent with `--mailbox` share one mailbox. Pass `?session=<id>` to pick a session.
- **The judge doesn't read feedback yet.** `FeedbackRecord`s (`bad_timing`, `wanted_help`, …) are stored for evaluation but don't change the judge's behaviour.
- **No personalization.** The thresholds (cooldown, signal gates) are the same for everyone.
- **Single-language scope.** Error fingerprinting and test-count parsing are built around Python and pytest output.
- **Terminals without shell integration produce no events.** An example is `cmd.exe`.
- **Frequent interventions risk learned helplessness.** With the budget removed, only the judge's cost-benefit bar and the cooldown prevent this.
- **No `npm` scripts.** Outside the F5 flow, every command is a Python module invocation.

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
  latency.py           latency report
  checks.py, smoke.py  acceptance checks
  synth.py, fixtures.py  synthetic traces and fixtures
traces/                recorded + synthetic traces and labels
calibration/           scoring history (results.jsonl, RESULTS.md, LATENCY.md)
logs/                  decision logs (gitignored, regenerated)
```
