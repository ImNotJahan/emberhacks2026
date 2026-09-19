# Recording brief

Three sessions of at least 15 minutes each, with a different volunteer for each if possible:

| Session | Task file | What you expect |
|---|---|---|
| **stuck** | `TASK.md` | Someone less familiar with Python. The circular import and the loop usually cause long struggles. |
| **productive** | `FEATURES.md` | A confident Python developer. Clear spec, tests already written: TDD rhythm, few real problems. |
| **mixed** | `TASK.md`, then `FEATURES.md` | A confident developer. They fly through some bugs and get stuck on others. |

## Before each session (5 min)

1. Give them a fresh copy **outside** this repo:
   `rm -rf ~/broken_shop && cp -r recording_kit/broken_shop ~/broken_shop`
2. Make sure `pytest` is installed for the Python they'll use (`python3 -m pip install pytest`).
3. Clear old engine output: `recording_kit/save_session.sh` (no name) moves `trace.*.jsonl` into `traces/_old/`.
4. In this repo press **F5**. In the new window, open `~/broken_shop`. Check the Big Brother Control view shows the engine running.
   Recording works even if the judge is off. For a clean baseline, set `bigBrother.testSurface.enabled` to false so they don't get nudged.
5. Start the screen recording (macOS: **Cmd+Shift+5** → Record Entire Screen).
6. In the VS Code terminal, type `echo SYNC` and press Enter. Say "go".

## During

- Don't help, don't hint, don't react.
- Jot rough times: "~6:30 looks lost", "~11:00 on a roll". They make labelling much faster.
- They must use the **VS Code integrated terminal**. External terminals are invisible to capture.

## After

1. Stop the recording. Save it as `<name>.mov` on the team drive, **not** in git.
2. `recording_kit/save_session.sh <name>`, e.g. `stuck1`, `productive1`, `mixed1`.
3. Label it: follow [traces/eval/SCENARIOS.md](../traces/eval/SCENARIOS.md#how-to-label-a-recording).
4. Add it to `traces/eval/manifest.json` with `"source": "real"`, then run `python -m judge.eval --capture-only <name>`.
