"""The judge: CandidateMoment -> InterventionDecision, logged every time.

    judge = Judge(session_id="sess_x")            # no budget
    judge = Judge("sess_x", "judge-v5", per_hour=3)   # v1-v7 era: 3/hour
    decision = judge.decide(candidate)

Order of operations per candidate:
  1. budget: the bucket (not the caller) is authoritative; its state replaces
     candidate.budget so the log shows exactly what the model saw. With
     per_hour=None (the default since v8) there is no budget: the bucket only
     counts interruptions and each moment is judged on its own merits
  2. gates: empty bucket (only if budgeted) -> budget_exhausted; spoke less
     than cooldown ago -> too_soon. Both skip the model call entirely
  3. judge call (timing), with the judge's memory of its last interruption
  4. content call (what to say) — only on should_speak=True
  5. spend a token, append to the decision log
With mailbox=True the judge also talks to Person 3's surface (p3_server.py via
p3_send): a snooze there declines with user_suppressed before anything else,
"considering" is posted before the model call, and every DecisionRecord is
forwarded so the surface and dashboard see it.
Any failure fails SILENT: a broken judge must never interrupt anyone.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Optional

from contract import (
    CandidateMoment,
    DeclineReason,
    InterventionDecision,
    SignalKind,
    Trajectory,
)
from judge import content as content_gen
from judge import gemini
from judge.budget import TokenBucket
from judge.log import DecisionLog
from judge.prompts import LATEST, PROMPTS, WITH_HINTS, schema_for
from judge.render import LastSpoken, render_candidate
from judge.schema import validate_verdict


class Judge:
    def __init__(self, session_id: str, prompt_version: str = LATEST, per_hour: Optional[int] = None,
                 log: Optional[DecisionLog] = None, with_content: bool = True,
                 model: str = gemini.DEFAULT_MODEL, cooldown_s: float = 240.0,
                 mailbox: bool = False) -> None:
        self.session_id = session_id
        self.prompt_version = prompt_version
        self.system = PROMPTS[prompt_version]
        self.schema = schema_for(prompt_version)
        self.bucket = TokenBucket(per_hour)
        self.log = log or DecisionLog(session_id)
        self.with_content = with_content
        self.model = model
        self.cooldown_ms = int(cooldown_s * 1000)
        self.last_spoken: Optional[LastSpoken] = None
        # Imported only when used: replays and calibration never touch the network.
        self.mailbox = None
        if mailbox:
            import p3_send
            self.mailbox = p3_send

    def decide(self, cand: CandidateMoment) -> InterventionDecision:
        t0 = time.perf_counter()
        cand.budget = self.bucket.state(cand.ts)
        extra: dict[str, Any] = {"prompt_version": self.prompt_version}
        signal_kinds = [s.kind for s in cand.signals]

        if self.mailbox and self.mailbox.snoozed():
            decision = self._decline(
                cand, DeclineReason.USER_SUPPRESSED, Trajectory.UNKNOWN, 1.0,
                "You snoozed me, so I'm staying quiet whatever the signals say.",
                signal_kinds, t0)
            extra["override"] = "snoozed from the surface; judge not called"
            self._record(cand, decision, extra)
            return decision

        if not self.bucket.unlimited and cand.budget.remaining < 1:
            decision = self._decline(
                cand, DeclineReason.BUDGET_EXHAUSTED, Trajectory.UNKNOWN, 1.0,
                f"You've had all {cand.budget.per_hour} interruptions this hour, so "
                "I'm holding this one regardless of the signals.",
                signal_kinds, t0)
            extra["override"] = "budget exhausted; judge not called"
            self._record(cand, decision, extra)
            return decision

        if self.last_spoken and cand.ts - self.last_spoken.ts < self.cooldown_ms:
            mins = (cand.ts - self.last_spoken.ts) / 60000
            decision = self._decline(
                cand, DeclineReason.TOO_SOON, Trajectory.UNKNOWN, 1.0,
                (f"I interrupted you {mins:.1f} min ago, so I'm giving you time to act on "
                 "that rather than stacking another message.") if self.bucket.unlimited else
                (f"I interrupted you {mins:.1f} min ago, so I'm saving the remaining "
                 f"{cand.budget.remaining} interruption(s) rather than stacking another."),
                signal_kinds, t0)
            extra["override"] = f"cooldown ({self.cooldown_ms // 1000}s); judge not called"
            self._record(cand, decision, extra)
            return decision

        if self.mailbox:
            self.mailbox.considering(cand)
        try:
            res = gemini.call(self.system, render_candidate(cand, last_spoken=self.last_spoken,
                                                           hints=self.prompt_version in WITH_HINTS),
                              schema=self.schema,
                              temperature=0.0,
                              model=self.model)
        except Exception as e:  # network, quota, malformed JSON — all fail silent
            decision = self._decline(
                cand, DeclineReason.LOW_CONFIDENCE, Trajectory.UNKNOWN, 0.0,
                "The judge was unavailable, so I stayed quiet.", signal_kinds, t0)
            extra["error"] = f"{type(e).__name__}: {e}"
            self._record(cand, decision, extra)
            return decision

        v = dict(res.data) if isinstance(res.data, dict) else {}
        judge_ms = res.latency_ms
        extra.update(judge_latency_ms=judge_ms,
                     trajectory_evidence=v.get("trajectory_evidence"), raw=v)

        # Normalize the one inconsistency that's safe to repair; anything else
        # invalid is a silent decline.
        if v.get("should_speak") is True:
            v["decline_reason"] = DeclineReason.NOT_APPLICABLE.value
        elif v.get("should_speak") is False and v.get("decline_reason") == "not_applicable":
            v["decline_reason"] = DeclineReason.NOTHING_USEFUL_TO_SAY.value
        errs = validate_verdict(v)
        if errs:
            decision = self._decline(
                cand, DeclineReason.LOW_CONFIDENCE, Trajectory.UNKNOWN, 0.0,
                "The judge returned an unusable answer, so I stayed quiet.",
                signal_kinds, t0)
            extra["error"] = f"invalid verdict: {errs}"
            self._record(cand, decision, extra)
            return decision

        speak = v["should_speak"]
        text: Optional[str] = None
        decline = DeclineReason(v["decline_reason"])
        if speak and self.with_content:
            try:
                c = content_gen.generate(cand, v["reasoning"], v["trajectory"])
                text = c.data or None
                extra["content_latency_ms"] = c.latency_ms
                extra["content_version"] = content_gen.CONTENT_VERSION
            except Exception as e:
                extra["error"] = f"content failed: {type(e).__name__}: {e}"
            if not text:
                speak, decline = False, DeclineReason.NOTHING_USEFUL_TO_SAY
                extra["override"] = "judge said speak but content generation produced nothing"
        elif speak:
            text = "(content generation disabled)"
        if speak and not self.bucket.try_spend(cand.ts):
            speak, text, decline = False, None, DeclineReason.BUDGET_EXHAUSTED
            extra["override"] = "judge said speak but bucket was empty"
        if speak:
            self.last_spoken = LastSpoken(cand.snapshot.episode.episode_id, cand.ts, text)

        extra["total_latency_ms"] = int((time.perf_counter() - t0) * 1000)
        decision = InterventionDecision.create(
            cand.candidate_id,
            should_speak=speak,
            trajectory=Trajectory(v["trajectory"]),
            confidence=float(v["confidence"]),
            reasoning=v["reasoning"],
            signals_cited=[SignalKind(s) for s in v["signals_cited"]],
            decline_reason=DeclineReason.NOT_APPLICABLE if speak else decline,
            content=text if speak else None,
            latency_ms=judge_ms,   # candidate -> timing decision; content timed separately
            model=res.model,
            prompt_version=self.prompt_version,
        )
        self._record(cand, decision, extra)
        return decision

    def _record(self, cand: CandidateMoment, decision: InterventionDecision,
                extra: dict[str, Any]) -> None:
        rec = self.log.append(cand, decision, extra)
        if self.mailbox:
            # Same shape as the local log line: the DecisionRecord plus the judge's
            # extras, so the dashboard can show evidence, overrides and latencies.
            self.mailbox.log({**asdict(rec), "judge": extra})   # never raises

    def _decline(self, cand: CandidateMoment, reason: DeclineReason, traj: Trajectory,
                 conf: float, why: str, cited: list[SignalKind], t0: float
                 ) -> InterventionDecision:
        return InterventionDecision.create(
            cand.candidate_id, should_speak=False, trajectory=traj, confidence=conf,
            reasoning=why, signals_cited=cited, decline_reason=reason,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            model=self.model, prompt_version=self.prompt_version,
        )
