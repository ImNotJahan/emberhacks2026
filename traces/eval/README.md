# Evaluation sessions

Raw editor traces with hand-labelled moments, scored end to end (capture → judge) by:

```bash
python -m judge.eval --capture-only   # free and offline: did capture nominate the moments that matter?
python -m judge.eval                  # + judge (Gemini, content off), cached in logs/eval_<name>__<prompt>.jsonl
python -m judge.eval dogfood --fresh  # one session, force a new judge run
python -m judge.replay traces/eval/dogfood.raw.jsonl   # full decision log for one session, readable
```

Results go to [calibration/EVAL.md](../../calibration/EVAL.md) and `calibration/eval_results.jsonl`.

## Files

| File | What |
|---|---|
| `<name>.raw.jsonl` | raw observations, exactly as `extension.js` sends them (the live engine writes `trace.raw.jsonl`) |
| `<name>.labels.jsonl` | `LabeledMoment`s: `{ts, should_have_spoken, rationale, tolerance_ms}` |
| `manifest.json` | per session: `kind` (`stuck`/`productive`/`mixed`), `source` (`real`/`scripted`), description. Only sessions listed here are scored. |
| [SCENARIOS.md](SCENARIOS.md) | the catalogue of should-speak (S1–S28) and should-stay-silent (Q1–Q18) moments |

## Sessions

| Session | Kind | Source | Minutes | Labels (speak) |
|---|---|---|---|---|
| `dogfood` | mixed | **real**: a staged FizzBuzz session, labelled from the trace (no video) | 41 | 18 (7) |
| `stuck_import` | stuck | scripted | 20 | 8 (4) |
| `productive_tdd` | productive | scripted | 22 | 8 (0) |
| `mixed_pagination` | mixed | scripted | 26 | 10 (4) |
| `explore_codebase` | productive | scripted | 18 | 6 (0) |
| `env_hell` | stuck | scripted | 17 | 7 (3) |
| `stuck_jest` | stuck | scripted | 16 | 5 (3) |

Scripted sessions come from [judge/scenarios.py](../../judge/scenarios.py) (`python -m judge.scenarios` rewrites them). They are realistic in shape but written by us, so **never quote their numbers as real-world accuracy.**

## Adding a recorded session

1. Before recording, move old engine output aside. The files are appended to, not overwritten:
   `recording_kit/save_session.sh` does this.
2. Press F5 in this repo, open the project in the new window, start a screen recording, and type `echo SYNC` in the terminal.
3. After the session, run `recording_kit/save_session.sh <name>`. It moves `trace.raw.jsonl` to `traces/eval/<name>.raw.jsonl` and prints the SYNC timestamp.
4. Label it following [SCENARIOS.md](SCENARIOS.md#how-to-label-a-recording) into `<name>.labels.jsonl`.
5. Add it to `manifest.json` with `"source": "real"`.
6. Run `python -m judge.eval --capture-only <name>` first. It is free and shows which labels have no candidate at all.

Keep screen recordings out of git (`*.mov`/`*.mp4` are ignored here); share them on the team drive.
