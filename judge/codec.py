"""dict -> contract objects, for replaying JSONL written with contract.to_json.
The contract only serializes; the judge needs to read traces back."""

from __future__ import annotations

from contract import (
    ActivitySnapshot,
    BudgetState,
    CandidateMoment,
    DeclineReason,
    Episode,
    EpisodeEnd,
    Event,
    EventKind,
    InterventionDecision,
    LabeledMoment,
    Signal,
    SignalKind,
    Trajectory,
)


def event_from_dict(d: dict) -> Event:
    return Event(**{**d, "kind": EventKind(d["kind"])})


def episode_from_dict(d: dict) -> Episode:
    return Episode(**{**d, "ended_by": EpisodeEnd(d.get("ended_by", "still_open"))})


def snapshot_from_dict(d: dict) -> ActivitySnapshot:
    return ActivitySnapshot(**{
        **d,
        "episode": episode_from_dict(d["episode"]),
        "recent_events": [event_from_dict(e) for e in d["recent_events"]],
    })


def signal_from_dict(d: dict) -> Signal:
    return Signal(**{**d, "kind": SignalKind(d["kind"])})


def candidate_from_dict(d: dict) -> CandidateMoment:
    return CandidateMoment(
        candidate_id=d["candidate_id"],
        ts=d["ts"],
        snapshot=snapshot_from_dict(d["snapshot"]),
        signals=[signal_from_dict(s) for s in d["signals"]],
        budget=BudgetState(**d["budget"]),
    )


def decision_from_dict(d: dict) -> InterventionDecision:
    return InterventionDecision(**{
        **d,
        "trajectory": Trajectory(d["trajectory"]),
        "signals_cited": [SignalKind(s) for s in d["signals_cited"]],
        "decline_reason": DeclineReason(d["decline_reason"]),
    })


def label_from_dict(d: dict) -> LabeledMoment:
    return LabeledMoment(**d)
