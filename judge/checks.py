"""Acceptance checks for the judge's "done when" criteria.

    python -m judge.checks ambiguous [--prompt judge-v1] [-n 10]
    python -m judge.checks budget    [--prompt judge-v2]
    python -m judge.checks refactor  [--prompt judge-v3]
"""

from __future__ import annotations

import argparse
import re
import sys

from contract import read_jsonl
from judge.prompts import LATEST
from judge.replay import replay
from judge.synth import TRACE_DIR

SCARCITY = re.compile(
    r"budget|interruptions? (left|remaining)|remaining|last (one|interruption)|scarc|"
    r"only (one|\d)|one of (the|your)|worth (one|an interruption|spending)|save|spend",
    re.I)


def ambiguous(prompt: str, n: int) -> bool:
    spoke = 0
    for i in range(n):
        path, [d] = replay(TRACE_DIR / "parser.candidates.jsonl", prompt, with_content=False,
                           session_id=f"check_ambiguous__{prompt}", fresh=(i == 0))
        spoke += d.should_speak
        print(f"[{i+1:2}] speak={d.should_speak} {d.decline_reason.value:<22} {d.reasoning}")
    print(f"\nspoke {spoke}/{n} on the ambiguous case  ->  {path}")
    return spoke == 0


def budget(prompt: str) -> bool:
    path, ds = replay(TRACE_DIR / "budget30.candidates.jsonl", prompt, per_hour=3,
                      with_content=False, session_id=f"check_budget__{prompt}")
    lines = list(read_jsonl(str(path)))
    fired = [d for d in ds if d.should_speak]
    ok_scarcity = True
    for i, (d, line) in enumerate(zip(ds, lines)):
        rem = line["candidate"]["budget"]["remaining"]
        mentions = bool(SCARCITY.search(d.reasoning))
        judged = "override" not in line.get("judge", {})
        tag = "SPEAK " if d.should_speak else "silent"
        print(f"{i:2} budget={rem} {tag} {d.decline_reason.value:<18} "
              f"{'scarcity' if mentions else '        '} {'model' if judged else 'gate '} "
              f"| {d.reasoning[:120]}")
        if d.should_speak and not mentions:
            ok_scarcity = False
    exhausted = [d for d in ds if d.decline_reason.value == "budget_exhausted"]
    print(f"\nfired {len(fired)}/30 (limit 3); spoken reasoning all references scarcity: "
          f"{ok_scarcity}; {len(exhausted)} held for exhausted budget  ->  {path}")
    return len(fired) <= 3 and ok_scarcity


def refactor(prompt: str) -> bool:
    path, ds = replay(TRACE_DIR / "refactor.candidates.jsonl", prompt, with_content=False,
                      session_id=f"check_refactor__{prompt}")
    for line in read_jsonl(str(path)):
        d, j = line["decision"], line.get("judge", {})
        print(f"{d['trajectory']:<11} speak={d['should_speak']}  evidence: {j.get('trajectory_evidence')}")
    ok = all(d.trajectory.value == "converging" and not d.should_speak for d in ds)
    print(f"\nall labeled converging and silent: {ok}  ->  {path}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("check", choices=["ambiguous", "budget", "refactor"])
    ap.add_argument("--prompt", default=LATEST)
    ap.add_argument("-n", type=int, default=10)
    a = ap.parse_args()
    fn = {"ambiguous": lambda: ambiguous(a.prompt, a.n),
          "budget": lambda: budget(a.prompt),
          "refactor": lambda: refactor(a.prompt)}[a.check]
    sys.exit(0 if fn() else 1)
