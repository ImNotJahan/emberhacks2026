"""Step 1 check: one structured Gemini call on a hardcoded snapshot, N times.

    python -m judge.smoke          # 10 runs, exits non-zero on any invalid response
"""

from __future__ import annotations

import json
import sys

from contract import (
    DeclineReason,
    InterventionDecision,
    SignalKind,
    Trajectory,
    now_ms,
)
from judge import gemini
from judge.fixtures import parser_repeated_error
from judge.render import render_candidate
from judge.schema import JUDGE_SCHEMA, validate_verdict

SMOKE_SYSTEM = (
    "You decide whether to interrupt a developer. Fill every field of the "
    "schema. Default to should_speak=false."
)


def run(n: int = 10) -> bool:
    cand = parser_repeated_error(now_ms())
    user = render_candidate(cand)
    ok = 0
    for i in range(1, n + 1):
        try:
            res = gemini.call(SMOKE_SYSTEM, user, schema=JUDGE_SCHEMA)
        except json.JSONDecodeError as e:
            print(f"[{i:2}] FAIL  unparseable JSON: {e}")
            continue
        errs = validate_verdict(res.data)
        if not errs:
            v = res.data
            try:
                # Round-trip through the contract type. Content is produced by a
                # separate call later; stand in for it here.
                InterventionDecision.create(
                    cand.candidate_id,
                    should_speak=v["should_speak"],
                    trajectory=Trajectory(v["trajectory"]),
                    confidence=float(v["confidence"]),
                    reasoning=v["reasoning"],
                    signals_cited=[SignalKind(s) for s in v["signals_cited"]],
                    decline_reason=DeclineReason(v["decline_reason"]),
                    content="(smoke test)" if v["should_speak"] else None,
                    latency_ms=res.latency_ms,
                    model=res.model,
                    prompt_version="smoke",
                )
            except (ValueError, TypeError) as e:
                errs = [f"contract rejected it: {e}"]
        if errs:
            print(f"[{i:2}] FAIL  {res.latency_ms}ms  {errs}\n      raw={res.raw[:300]}")
            continue
        ok += 1
        print(f"[{i:2}] ok    {res.latency_ms}ms  speak={v['should_speak']} "
              f"traj={v['trajectory']} conf={v['confidence']:.2f}")
    print(f"\n{ok}/{n} valid")
    return ok == n


if __name__ == "__main__":
    sys.exit(0 if run(int(sys.argv[1]) if len(sys.argv) > 1 else 10) else 1)
