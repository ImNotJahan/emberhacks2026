# When should Big Brother speak?

A catalogue of moments, for labelling recordings and for writing new scripted sessions in [judge/scenarios.py](../../judge/scenarios.py).

Every entry says what the moment looks like **in the data we actually capture**. The capture only sees edit sizes (never text), saves, focus changes, cursor jumps, diagnostics, and terminal commands with their output and exit code. A moment you can only recognise by watching someone's face doesn't belong here.

"Capture today" says whether [extention.py](../../extention.py) nominates a candidate for this pattern at the moment:
- **yes**: a detector fires.
- **late**: fires, but a debounce or suppression window delays it.
- **no**: nothing fires.

"In" lists the traces in this folder that contain the pattern.

## Should speak (label `true`)

| # | Moment | What it looks like in the data | Capture today | In |
|---|---|---|---|---|
| S1 | **Same error, 4th time, with small edits between runs** | ≥4 terminal/test runs with the same error fingerprint; 1–10 char edits between them | yes (`repeated_error` at 3) | stuck_import, stuck_jest, mixed_pagination, dogfood |
| S2 | **Edits that undo each other** | the same failure while the edits flip, e.g. +1/−1 then −1/+1, or +6/0 then 0/−6 | late: only as S1 | mixed_pagination, stuck_jest |
| S3 | **Rage re-run** | the same command ≥3 times in 10s, no edit between, same output | **no**: `_gapped_count` merges repeats less than 10s apart | stuck_import, dogfood (9 runs in 5s) |
| S4 | **Infinite loop, again** | ≥2–3 runs of the same command ending in exit 130 (Ctrl+C), with tiny or no edits between | late, and for the wrong reason: fingerprinted as `error:%` (the zsh prompt marker) | mixed_pagination, dogfood |
| S5 | **Stalled after repeated terminal failures** | no events for ≥90s right after ≥3 identical failures | **no**: `stalled_with_error` only looks at editor diagnostics, not terminal errors | stuck_import, mixed_pagination, env_hell, dogfood |
| S6 | **Stalled with a red squiggle** | no edits for >30s while the focused file has open diagnostics | yes (`stalled_with_error`) | — |
| S7 | **Test pass count flat** | ≥3 test runs, all failing, and the passed count doesn't move | yes (`test_loop`) | stuck_import, mixed_pagination |
| S8 | **Pass count falling** | tests that passed before now fail after edits: the passed count drops run to run | no | — |
| S9 | **Editing the wrong file** | the traceback names file A, but every edit since the error is in file B | no (needs the traceback path parsed) | stuck_import (traceback → pricing.py, cause is the cycle) |
| S10 | **File thrash after an error** | ≥4 focuses across the same 2–3 files in 2 min, <3 edits, an error still standing | yes (`file_thrash`), if ≥4 visits to one file | stuck_import, stuck_jest |
| S11 | **Environment loop** | install/build command (pip, npm, brew, docker) failing the same way ≥3 times with different flags | late: fingerprint is `error:%` because pip's error line has no `*Error` class name | env_hell |
| S12 | **Escalation** | after repeated failures, the next command adds `sudo`, `rm -rf`, `--force`, `git reset --hard` | no | env_hell (`sudo pip`) |
| S13 | **Another language's syntax, repeated** | the same SyntaxError on a foreign idiom (`else if`, `true`, `===`, `;`, `{}`) run ≥3 times | yes | dogfood (`else if` → `elif`) |
| S14 | **Circular import, repeated** | `ImportError: cannot import name … partially initialized module` ≥3 times | yes | stuck_import |
| S15 | **Typo'd command, repeated** | `command not found: pytets` / `No such file or directory` ≥2 times | yes (fingerprint on the last line) | — |
| S16 | **Port in use** | `EADDRINUSE` / `Address already in use` on every server start | yes | — |
| S17 | **Wrong working directory** | `can't open file` / `ModuleNotFoundError` for the project's own package while cwd is a sibling directory | yes, as S1 | — |
| S18 | **Flaky test** | the same test alternates pass/fail across runs with **no** edits between | no | — |
| S19 | **Debug-print loop** | add print → run → remove → run, same failure, ≥4 cycles (large +/− edits that cancel) | yes, as S1 | mixed_pagination (one cycle, then solved: `false`) |
| S20 | **Undo storm** | large removals (≥200 chars) right after a failure: throwing away work | no | — |
| S21 | **Nudge ignored, still stuck** | spoke once; ≥3 more identical failures after the cooldown | yes, once the 240s cooldown ends | — |
| S22 | **Back from a break into red** | an idle gap of ≥5 min whose last run failed; they come back and rerun it unchanged | no | dogfood (11-min gap, then new code: not this) |
| S23 | **Diagnostics rising** | the diagnostic count grows across saves in one file (3 → 5 → 8) with no test runs | no | — |
| S24 | **Same failing test across a refactor** | after a rename, the same `NameError`/`AttributeError` appears in several test files, run after run | yes | — |
| S25 | **Hung test run** | a test command cancelled with exit 130 ≥2 times | late (as S4) | — |
| S26 | **Git stuck** | `git merge`/`rebase`/`push` failing the same way ≥3 times (`non-fast-forward`, `conflict`) | yes (fingerprint on `error:` line) | — |
| S27 | **Reading the same traceback for minutes** | after one error, 2+ min of cursor jumps in the same file, no edits, no runs | no | mixed_pagination (after 3 hangs) |
| S28 | **Copy-pasting blindly** | a large single `added` edit (a pasted block) followed by a new error, twice in a row | no | — |

## Should stay silent (label `false`)

Speaking at any of these is the infuriating failure. Put them in every recording.

| # | Moment | What it looks like | Bait for |
|---|---|---|---|
| Q1 | **First occurrence of any error** | one failure, new fingerprint | everything |
| Q2 | **The error changed** | a different fingerprint from the last run: progress | `repeated_error` |
| Q3 | **Pass count climbing** | same `AssertionError` 3 runs in a row, but passed goes 12 → 13 → 14 | `repeated_error` (numbers normalise to `N`) |
| Q4 | **TDD red phase** | a new test file, then an immediate failing run (`AttributeError: no attribute`) | `repeated_error`, `test_loop` |
| Q5 | **Long edit burst** | 50–200 edits over several minutes, no runs | anything that fires on idle-after |
| Q6 | **Reading / onboarding** | many opens and cursor jumps, few or no edits, no errors | `file_thrash` |
| Q7 | **Idle after green or after a commit** | quiet after `N passed` or `git commit` | `stalled_*` |
| Q8 | **Planned multi-file rename** | 5+ files in 2 min, diagnostics spike then fall | `file_thrash`, `repeated_error` on diagnostics |
| Q9 | **Just solved it** | the first green after a long red streak | the judge's memory of the stuck streak |
| Q10 | **One Ctrl+C** | a single exit 130: they already know it hung | S4 |
| Q11 | **Stopping a server on purpose** | exit 130 on `serve`/`runserver`/`npm run dev` after it ran for a while | S4 |
| Q12 | **The message is the answer** | `Did you mean: 'True'?`, `KeyError: 'DATABASE_URL'`, first time | Q1 |
| Q13 | **Waiting on a long command** | `brew`/`pip`/`docker build` running for 30s+ | stall detectors |
| Q14 | **Mid-line pause with a squiggle** | a half-typed line flagged by Pylance, then a pause to look something up | `stalled_with_error` |
| Q15 | **Deliberate bisect** | `git bisect` / commenting blocks out, where the outcome changes each run | S1, S19 |
| Q16 | **Right after a nudge** | inside the 240s cooldown | S21 |
| Q17 | **REPL exploration** | many short `python -c` runs with *different* errors | S1 |
| Q18 | **Snoozed / focus mode** | the user snoozed from the surface | everything |

## How to label a recording

1. At the start of the recording, type `echo SYNC` in the VS Code terminal. It lands in the trace as a `terminal_cmd` with an exact timestamp.
2. Watch at 1.5x. Note the video time of every S-moment and Q-moment above, plus anything else that made you think "help them now" or "leave them alone".
3. Convert: `ts = ts(echo SYNC) + (video_s − SYNC_video_s) × 1000`.
4. Write one line per moment: `{"ts": …, "should_have_spoken": true|false, "rationale": "<S#/Q#>: what happened", "tolerance_ms": 60000}`. Use 20–45s tolerance where moments are close together. Nearest label wins.
5. Put a `true` label at the **first** moment help would be worth it, not at the end of the struggle. The `lag` column in the eval rewards being early.
