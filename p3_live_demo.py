"""
p3_live_demo.py — play a few moments in real time so you can watch the surface
react: considering -> stays quiet, considering -> stays quiet, considering -> speaks.

    py p3_live_demo.py            (with /surface open)
"""

import time

from contract import (Episode, ActivitySnapshot, Signal, SignalKind, BudgetState,
    CandidateMoment, InterventionDecision, Trajectory, DeclineReason,
    DecisionRecord, new_id, now_ms)
from p3_send import log, considering, snoozed

SESSION = "sess_live_" + time.strftime("%H%M%S")
THINK_SECONDS = 3

MOMENTS = [
    ([(SignalKind.FILE_THRASH, .45, "utils.py revisited 4x in 2 min")],
     Trajectory.EXPLORING, .76, "Bouncing around utils.py looks like reading, not being lost.",
     DeclineReason.STILL_CONVERGING, None),
    ([(SignalKind.REPEATED_ERROR, .6, "ImportError: cannot import name 'Config' 2x")],
     Trajectory.CONVERGING, .64, "Second time on this ImportError, but they just opened the module it's about.",
     DeclineReason.STILL_CONVERGING, None),
    ([(SignalKind.REPEATED_ERROR, .85, "ImportError: cannot import name 'Config' 4x"),
      (SignalKind.STALLED_WITH_ERROR, .8, "idle 38s, ImportError unresolved")],
     Trajectory.BLOCKED, .84, "Same ImportError four times and they've stopped typing, so this is a cheap moment to speak.",
     None, "`config.py` imports from `app.py`, and `app.py` imports `Config` from `config.py`. "
           "It's a circular import. Move the `from app import ...` inside the function that needs it."),
]


def main() -> None:
    ep = Episode(new_id("ep"), now_ms())
    budget = BudgetState()
    print(f"session {SESSION}")
    for sigs, traj, conf, why, decline, content in MOMENTS:
        snap = ActivitySnapshot.create(ep, [])
        cand = CandidateMoment.create(
            snap, [Signal(k, s, d, now_ms()) for k, s, d in sigs], budget)

        if snoozed():
            dec = InterventionDecision.create(
                cand.candidate_id, should_speak=False, trajectory=traj, confidence=conf,
                reasoning="The developer snoozed me.", decline_reason=DeclineReason.USER_SUPPRESSED)
        else:
            considering(cand)
            print("  considering...")
            time.sleep(THINK_SECONDS)          # stands in for the Gemini call
            dec = InterventionDecision.create(
                cand.candidate_id, should_speak=content is not None, trajectory=traj,
                confidence=conf, reasoning=why, signals_cited=[k for k, _, _ in sigs],
                decline_reason=decline or DeclineReason.NOT_APPLICABLE, content=content,
                latency_ms=THINK_SECONDS * 1000, prompt_version="judge-v3")

        log(DecisionRecord.create(SESSION, cand, dec))
        print("  spoke" if dec.should_speak else f"  stayed quiet ({dec.decline_reason.value})")
        if dec.should_speak:
            budget.remaining -= 1
        time.sleep(2)


if __name__ == "__main__":
    main()
