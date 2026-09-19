"""Replay a recorded trace through the judge.

    python -m judge.replay traces/refactor.candidates.jsonl [--prompt judge-v5]
        [--per-hour 3] [--no-content] [--session NAME]
    python -m judge.replay traces/stuck.raw.jsonl      # raw editor trace
    python -m judge.replay traces/thrash.candidates.jsonl --mailbox   # also send to the surface

A *.candidates.jsonl trace is CandidateMoments (contract.to_json per line).
A *.raw.jsonl trace is Person 1's raw editor observations; it is run through
their capture engine (extention.py) first, exactly as live, and the
candidates it nominates go to the judge.

Writes logs/<session>.jsonl and prints the readable view.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from contract import CandidateMoment, InterventionDecision, read_jsonl
from judge.codec import candidate_from_dict
from judge.core import Judge
from judge.log import LOG_DIR, DecisionLog, pretty
from judge.prompts import LATEST


def load_candidates(trace: Path) -> list[CandidateMoment]:
    if trace.name.endswith(".raw.jsonl"):
        import extention
        with open(trace, "r", encoding="utf-8") as fh:
            return extention.replay(extention.Engine(extention.Redactor()), fh)
    return [candidate_from_dict(d) for d in read_jsonl(str(trace))]


def replay(trace: str | Path, prompt_version: str = LATEST, per_hour: Optional[int] = None,
           with_content: bool = True, session_id: Optional[str] = None,
           fresh: bool = True, mailbox: bool = False) -> tuple[Path, list[InterventionDecision]]:
    trace = Path(trace)
    session_id = session_id or f"{trace.name.split('.')[0]}__{prompt_version}"
    path = LOG_DIR / f"{session_id}.jsonl"
    if fresh and path.exists():
        path.unlink()
    log = DecisionLog(session_id, path)
    judge = Judge(session_id, prompt_version, per_hour, log=log, with_content=with_content,
                  mailbox=mailbox)
    decisions = [judge.decide(c) for c in load_candidates(trace)]
    return log.path, decisions


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--prompt", default=LATEST)
    ap.add_argument("--per-hour", type=int, default=None,
                    help="interruption budget; default none (v8+). Use 3 for v1-v7")
    ap.add_argument("--no-content", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--mailbox", action="store_true",
                    help="also send each DecisionRecord to p3_server.py (surface + dashboard)")
    a = ap.parse_args()
    path, ds = replay(a.trace, a.prompt, a.per_hour, not a.no_content, a.session,
                      mailbox=a.mailbox)
    print(pretty(str(path)))
    print(f"\n{sum(d.should_speak for d in ds)}/{len(ds)} spoke  ->  {path}")
