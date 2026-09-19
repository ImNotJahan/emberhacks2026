"""The judge's structured-output schema, derived from contract.py.

GEMINI_DECISION_SCHEMA in the contract is the source of truth for field names
and enums. The judge call uses a copy of it with two differences:

  * `trajectory_evidence` is added FIRST, so the model has to write down what
    the episode history shows before it classifies the trajectory and before
    it decides to speak (two-stage reasoning in one call — Gemini emits keys
    in schema order).
  * `content` is removed. Content is written by a separate call that only
    runs on should_speak=True, so timing and content debug independently.
"""

from __future__ import annotations

import copy
from typing import Any

from contract import (
    GEMINI_DECISION_SCHEMA,
    DeclineReason,
    SignalKind,
    Trajectory,
)

_base = copy.deepcopy(GEMINI_DECISION_SCHEMA)
_base["properties"].pop("content", None)

JUDGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trajectory_evidence": {
            "type": "string",
            "description": "Stage one. What the episode history shows, before any "
                           "decision: is each retry different from the last, are "
                           "errors changing, are tests moving?",
        },
        **_base["properties"],
    },
    "required": ["trajectory_evidence"] + [r for r in _base["required"] if r != "content"],
}

_TRAJECTORIES = {t.value for t in Trajectory}
_SIGNALS = {s.value for s in SignalKind}
_DECLINES = {d.value for d in DeclineReason}


def validate_verdict(v: Any) -> list[str]:
    """Return every way `v` fails the judge schema or the contract's
    cross-field rules. Empty list means valid."""
    if not isinstance(v, dict):
        return [f"not an object: {type(v).__name__}"]
    errs: list[str] = []
    for key in JUDGE_SCHEMA["required"]:
        if key not in v:
            errs.append(f"missing {key}")
    if errs:
        return errs

    if not isinstance(v["trajectory_evidence"], str) or not v["trajectory_evidence"].strip():
        errs.append("trajectory_evidence empty")
    if v["trajectory"] not in _TRAJECTORIES:
        errs.append(f"bad trajectory {v['trajectory']!r}")
    if not isinstance(v["should_speak"], bool):
        errs.append("should_speak not bool")
    c = v["confidence"]
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0.0 <= c <= 1.0:
        errs.append(f"confidence out of range: {c!r}")
    if not isinstance(v["reasoning"], str) or not v["reasoning"].strip():
        errs.append("reasoning empty")
    cited = v["signals_cited"]
    if not isinstance(cited, list) or any(s not in _SIGNALS for s in cited):
        errs.append(f"bad signals_cited {cited!r}")
    if v["decline_reason"] not in _DECLINES:
        errs.append(f"bad decline_reason {v['decline_reason']!r}")

    # Contract cross-field rules (InterventionDecision.__post_init__).
    if v["should_speak"] is True and v["decline_reason"] != DeclineReason.NOT_APPLICABLE.value:
        errs.append("should_speak=true but decline_reason set")
    if v["should_speak"] is False and v["decline_reason"] == DeclineReason.NOT_APPLICABLE.value:
        errs.append("should_speak=false needs a real decline_reason")
    return errs


# v5+: make stage one countable. The model must commit to how many attempts
# hit the same failure, and whether any edit went backwards, before it is
# allowed to name a trajectory.
JUDGE_SCHEMA_COUNTED: dict = {
    "type": "object",
    "properties": {
        "trajectory_evidence": JUDGE_SCHEMA["properties"]["trajectory_evidence"],
        "same_failure_attempts": {
            "type": "integer",
            "description": "Number of edit->run attempts in this episode that ended in the "
                           "SAME failure as the latest run. 0 if there is no repeated failure.",
        },
        "oscillating": {
            "type": "boolean",
            "description": "True only if an edit restored earlier text or retried an "
                           "identical change (going in circles).",
        },
        **{k: v for k, v in JUDGE_SCHEMA["properties"].items() if k != "trajectory_evidence"},
    },
    "required": ["trajectory_evidence", "same_failure_attempts", "oscillating"]
                + JUDGE_SCHEMA["required"][1:],
}
