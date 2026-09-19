"""
p3_seed_demo.py — send a fake 30-minute session to the mailbox so the dashboard
has something to show before the real judge is wired up.

    py p3_seed_demo.py
"""

import random

from contract import (Episode, ActivitySnapshot, Signal, SignalKind, BudgetState,
    CandidateMoment, InterventionDecision, Trajectory, DeclineReason,
    DecisionRecord, FeedbackRecord, FeedbackKind, new_id, now_ms)
from p3_send import BASE_URL, _call, log

S, T, D = SignalKind, Trajectory, DeclineReason
SESSION = "sess_demo"

# (minute, signals, trajectory, confidence, reasoning, decline_reason or None,
#  content if spoke, (feedback kind, note) or None)
MOMENTS = [
    (1.5, [(S.EPISODE_BOUNDARY, .3, "opened src/parser.py after a commit")],
     T.EXPLORING, .82, "Just started a new chunk of work and is reading around.",
     D.NOTHING_USEFUL_TO_SAY, None, None),
    (3.2, [(S.FILE_THRASH, .4, "parser.py <-> lexer.py 4x in 2 min")],
     T.EXPLORING, .74, "Flipping between parser and lexer looks like tracing a token path, not being lost.",
     D.STILL_CONVERGING, None, None),
    (5.0, [(S.REPEATED_ERROR, .5, "KeyError: 'span' 2x")],
     T.CONVERGING, .68, "The error moved to a different line between runs, so each edit is making progress.",
     D.STILL_CONVERGING, None, None),
    (6.4, [(S.TEST_LOOP, .45, "3 runs, pass count 4 -> 6 -> 7")],
     T.CONVERGING, .88, "Pass count is climbing every run. Leave them alone.",
     D.STILL_CONVERGING, None, None),
    (8.1, [(S.REPEATED_ERROR, .8, "AttributeError: NoneType.children 3x"),
           (S.TEST_LOOP, .6, "3 runs, pass count flat at 7/9")],
     T.THRASHING, .61, "Looks stuck, but they're mid-edit in the same function. Speaking now would land inside the chunk.",
     D.MID_FLOW, None, None),
    (9.3, [(S.STALLED_WITH_ERROR, .85, "idle 42s, AttributeError unresolved"),
           (S.REPEATED_ERROR, .85, "same fingerprint 4x")],
     T.BLOCKED, .86, "Same AttributeError four times and they've stopped typing. A natural pause, so it's cheap to interrupt.",
     None, "`parse_block()` returns None when the token list is empty, and line 88 calls `.children` on it. "
           "Guard the empty case or return an empty Block.",
     (FeedbackKind.ACCEPTED, None)),
    (11.0, [(S.EPISODE_BOUNDARY, .3, "tests 7/9 -> 9/9")],
     T.CONVERGING, .93, "Tests just went green. Nothing to add.",
     D.NOTHING_USEFUL_TO_SAY, None, None),
    (12.6, [(S.FILE_THRASH, .5, "4 config files opened in 90s")],
     T.EXPLORING, .70, "Opening config files one after another reads as a deliberate search.",
     D.STILL_CONVERGING, None, None),
    (13.8, [(S.LONG_IDLE, .4, "idle 2m 10s")],
     T.UNKNOWN, .40, "Long idle but no error on screen. They may just be away.",
     D.LOW_CONFIDENCE, None, None),
    (15.2, [(S.REVERT_CHURN, .55, "same 6 lines added and removed 3x")],
     T.THRASHING, .58, "Some churn, but I can't tell what they're trying to do yet.",
     D.LOW_CONFIDENCE, None, None),
    (16.5, [(S.REVERT_CHURN, .75, "same 6 lines added and removed 5x"),
            (S.TEST_LOOP, .6, "4 runs, still 1 failing")],
     T.THRASHING, .77, "Five undo/redo cycles on the same lines with the same failing test.",
     None, "The failing test expects `retry=3` but the default in `Client.__init__` is 2. "
           "Your edits keep changing the loop, not the default.",
     (FeedbackKind.BAD_TIMING, "was literally about to try that")),
    (17.4, [(S.REPEATED_ERROR, .6, "TimeoutError 2x")],
     T.CONVERGING, .66, "Spoke a minute ago. Even if this is real, it can wait.",
     D.TOO_SOON, None, None),
    (19.0, [(S.EPISODE_BOUNDARY, .3, "commit: fix retry default")],
     T.CONVERGING, .90, "Clean commit, moving on.",
     D.NOTHING_USEFUL_TO_SAY, None, None),
    (20.7, [(S.FILE_THRASH, .45, "router.py revisited 4x")],
     T.EXPLORING, .72, "Revisiting the router while writing a new handler is normal.",
     D.STILL_CONVERGING, None, None),
    (22.3, [(S.STALLED_WITH_ERROR, .7, "idle 35s, TypeError unresolved")],
     T.CONVERGING, .63, "Paused on a TypeError, but the cursor is on the exact line. Probably thinking.",
     D.STILL_CONVERGING, None, None),
    (23.5, [(S.REPEATED_ERROR, .8, "TypeError: expected str 3x"),
            (S.STALLED_WITH_ERROR, .7, "idle 40s")],
     T.BLOCKED, .80, "Same TypeError three times and a long pause.",
     None, "`request.json()` is a coroutine here. Add `await` on line 41.",
     (FeedbackKind.BAD_CONTENT, "wrong framework")),
    (25.0, [(S.TEST_LOOP, .5, "3 runs, pass count flat")],
     T.THRASHING, .70, "Worth speaking, but the budget is spent for this hour.",
     D.BUDGET_EXHAUSTED, None, None),
    (26.4, [(S.REPEATED_ERROR, .7, "TypeError 4x")],
     T.BLOCKED, .74, "Still stuck on the TypeError, but there's no budget left.",
     D.BUDGET_EXHAUSTED, None, None),
    (28.0, [(S.EPISODE_BOUNDARY, .3, "tests 11/12 -> 12/12")],
     T.CONVERGING, .92, "Green again. Nothing useful to add.",
     D.NOTHING_USEFUL_TO_SAY, None, None),
]
MISS = (26.9, "wanted a hint on the TypeError")


def main() -> None:
    # Loading it twice would double every moment (new ids each run).
    try:
        loaded = any(r["session_id"] == SESSION for r in _call("/records")["decisions"])
    except Exception:
        print(f"can't reach the P3 server at {BASE_URL}; start it first")
        return
    if loaded:
        print(f"{SESSION} is already loaded. For a fresh copy, stop the server, "
              "delete logs/mailbox.jsonl and start it again.")
        return

    start = now_ms() - 30 * 60_000
    at = lambda minute: start + int(minute * 60_000)
    ep = Episode(new_id("ep"), start)
    budget = BudgetState(per_hour=3, remaining=3)
    sent = 0

    for minute, sigs, traj, conf, why, decline, content, fb in MOMENTS:
        ts = at(minute)
        snap = ActivitySnapshot.create(ep, [], session_elapsed_ms=ts - start)
        snap.ts = ts
        signals = [Signal(k, s, detail, ts - 60_000, occurrences=random.randint(1, 4))
                   for k, s, detail in sigs]
        cand = CandidateMoment.create(snap, signals, BudgetState(**vars(budget)))
        cand.ts = ts

        spoke = content is not None
        dec = InterventionDecision.create(
            cand.candidate_id,
            should_speak=spoke,
            trajectory=traj,
            confidence=conf,
            reasoning=why,
            signals_cited=[k for k, _, _ in sigs],
            decline_reason=decline or D.NOT_APPLICABLE,
            content=content,
            latency_ms=random.randint(900, 2600),
            prompt_version="judge-v3",
        )
        dec.ts = ts
        sent += log(DecisionRecord.create(SESSION, cand, dec))

        if spoke:
            budget.remaining -= 1
            budget.interventions_this_session += 1
            budget.last_intervention_ms = ts
        if fb:
            f = FeedbackRecord.create(SESSION, fb[0], dec.decision_id, note=fb[1])
            f.ts = ts + random.randint(15_000, 40_000)
            sent += log(f)

    miss = FeedbackRecord.create(SESSION, FeedbackKind.WANTED_HELP, note=MISS[1])
    miss.ts = at(MISS[0])
    sent += log(miss)
    print(f"sent {sent} records to the mailbox")


if __name__ == "__main__":
    main()
