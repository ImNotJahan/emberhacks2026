"""
contract.py — the shared data contract.

This file is the boundary between all four workstreams. Nothing in it should
depend on anything else in the repo, and it must stay importable with the
standard library alone.

    Person 1 (Capture)     PRODUCES  Event, Episode, Signal, ActivitySnapshot,
                                     CandidateMoment
    Person 2 (Judge)       CONSUMES  CandidateMoment
                           PRODUCES  InterventionDecision, DecisionRecord
    Person 3 (Surface)     CONSUMES  InterventionDecision, DecisionRecord
                           PRODUCES  FeedbackRecord
    Person 4 (Eval)        CONSUMES  everything, replays it, scores it

RULE: after hour 3, this file changes only with all four people present.
Bump CONTRACT_VERSION on any breaking change so replayed traces don't
silently mismatch.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict, is_dataclass
from enum import Enum
from typing import Any, Iterable, Iterator, Optional

CONTRACT_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def now_ms() -> int:
    """Wall-clock milliseconds. One clock for the whole system, please."""
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Enum):
            return o.value
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        return super().default(o)


def to_json(obj: Any) -> str:
    """Serialize any contract object to a single JSON line (no newlines)."""
    return json.dumps(obj, cls=_Encoder, separators=(",", ":"))


def write_jsonl(path: str, records: Iterable[Any]) -> int:
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(to_json(r) + "\n")
            n += 1
    return n


def read_jsonl(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# ---------------------------------------------------------------------------
# 1. raw activity  —  owned by Person 1
# ---------------------------------------------------------------------------

class EventKind(str, Enum):
    EDIT = "edit"                    # buffer changed
    SAVE = "save"
    FILE_OPEN = "file_open"
    FILE_FOCUS = "file_focus"        # switched to an already-open file
    CURSOR_JUMP = "cursor_jump"      # large in-file navigation
    DIAGNOSTIC = "diagnostic"        # linter / compiler / type error appeared
    TERMINAL_CMD = "terminal_cmd"
    TERMINAL_OUT = "terminal_out"
    TEST_RUN = "test_run"
    GIT = "git"                      # commit, branch, stash
    IDLE_START = "idle_start"
    IDLE_END = "idle_end"


@dataclass
class Event:
    """One observed thing. Deliberately flat — keep the payload small."""
    kind: EventKind
    ts: int                                  # ms, from now_ms()
    episode_id: Optional[str] = None         # assigned by segmentation
    path: Optional[str] = None               # relative path, never absolute
    lang: Optional[str] = None               # "python", "typescript", ...
    text: Optional[str] = None               # POST-REDACTION excerpt only
    exit_code: Optional[int] = None          # TERMINAL_CMD / TEST_RUN
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None
    error_fingerprint: Optional[str] = None  # normalized error identity
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Redaction is Person 1's job and it happens BEFORE construction.
        # This is only a tripwire for absolute paths leaking in.
        if self.path and (self.path.startswith("/") or ":\\" in self.path):
            raise ValueError(f"path must be workspace-relative, got {self.path!r}")


class EpisodeEnd(str, Enum):
    """Why an episode boundary was drawn. Useful for eval."""
    IDLE = "idle"                    # >45s of nothing
    TEST_PASSED = "test_passed"      # fail -> pass transition
    COMMIT = "commit"
    CONTEXT_SWITCH = "context_switch"
    STILL_OPEN = "still_open"        # current episode, not yet closed


@dataclass
class Episode:
    """A contiguous chunk of work. The unit the judge reasons over."""
    episode_id: str
    started_ms: int
    ended_ms: Optional[int] = None
    ended_by: EpisodeEnd = EpisodeEnd.STILL_OPEN
    primary_path: Optional[str] = None
    event_count: int = 0
    summary: Optional[str] = None    # optional one-line gist, may be model-written

    @property
    def duration_ms(self) -> int:
        return (self.ended_ms or now_ms()) - self.started_ms


# ---------------------------------------------------------------------------
# 2. cheap local signals  —  owned by Person 1
# ---------------------------------------------------------------------------

class SignalKind(str, Enum):
    """The heuristics that nominate a candidate moment. NO model calls here."""
    REPEATED_ERROR = "repeated_error"          # same fingerprint >= 3x
    FILE_THRASH = "file_thrash"                # same file revisited >= 4x / 2min
    STALLED_WITH_ERROR = "stalled_with_error"  # >30s idle, diagnostic unresolved
    TEST_LOOP = "test_loop"                    # >= 3 runs, no passing delta
    REVERT_CHURN = "revert_churn"              # edits undone and redone
    LONG_IDLE = "long_idle"
    EPISODE_BOUNDARY = "episode_boundary"      # a coarse breakpoint — cheap to interrupt


@dataclass
class Signal:
    kind: SignalKind
    strength: float                  # 0.0-1.0, heuristic confidence
    detail: str                      # human-readable, shown in the restraint log
    first_seen_ms: int
    occurrences: int = 1

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("strength must be in [0,1]")


# ---------------------------------------------------------------------------
# 3. what the judge receives  —  the Person 1 / Person 2 boundary
# ---------------------------------------------------------------------------

@dataclass
class ActivitySnapshot:
    """Everything the judge is allowed to see. Keep it trimmed — this is the
    context window and the latency budget."""
    snapshot_id: str
    ts: int
    episode: Episode
    recent_events: list[Event]               # newest last; cap at ~80
    open_paths: list[str] = field(default_factory=list)
    last_error_text: Optional[str] = None    # redacted
    last_test_summary: Optional[str] = None
    seconds_since_last_edit: float = 0.0
    session_elapsed_ms: int = 0

    @staticmethod
    def create(episode: Episode, recent_events: list[Event], **kw) -> "ActivitySnapshot":
        return ActivitySnapshot(
            snapshot_id=new_id("snap"),
            ts=now_ms(),
            episode=episode,
            recent_events=recent_events,
            **kw,
        )


@dataclass
class BudgetState:
    """The interruption budget, visible to the judge so it can reason about
    scarcity rather than answering each moment in isolation."""
    per_hour: int = 3
    remaining: int = 3
    last_intervention_ms: Optional[int] = None
    interventions_this_session: int = 0

    @property
    def minutes_since_last(self) -> Optional[float]:
        if self.last_intervention_ms is None:
            return None
        return (now_ms() - self.last_intervention_ms) / 60000.0


@dataclass
class CandidateMoment:
    """Person 1 hands this to Person 2. One Gemini call per candidate, max."""
    candidate_id: str
    ts: int
    snapshot: ActivitySnapshot
    signals: list[Signal]
    budget: BudgetState

    @staticmethod
    def create(snapshot: ActivitySnapshot, signals: list[Signal],
               budget: BudgetState) -> "CandidateMoment":
        return CandidateMoment(
            candidate_id=new_id("cand"),
            ts=now_ms(),
            snapshot=snapshot,
            signals=signals,
            budget=budget,
        )


# ---------------------------------------------------------------------------
# 4. what the judge returns  —  the Person 2 / Person 3 boundary
# ---------------------------------------------------------------------------

class Trajectory(str, Enum):
    """Stage one of the judge's reasoning: what is this person actually doing?
    The hard distinction is CONVERGING vs THRASHING — real traces are full of
    exploratory churn that looks like being stuck but isn't."""
    CONVERGING = "converging"        # making progress, leave them alone
    EXPLORING = "exploring"          # deliberate wandering, leave them alone
    THRASHING = "thrashing"          # repeating without progress
    BLOCKED = "blocked"              # stopped entirely
    UNKNOWN = "unknown"


class DeclineReason(str, Enum):
    """Why the judge stayed quiet. This is the restraint log's vocabulary and
    the most important field in the whole system for the demo."""
    STILL_CONVERGING = "still_converging"
    MID_FLOW = "mid_flow"                    # would land inside a chunk
    NOTHING_USEFUL_TO_SAY = "nothing_useful_to_say"
    LOW_CONFIDENCE = "low_confidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TOO_SOON = "too_soon"                    # spoke recently
    USER_SUPPRESSED = "user_suppressed"      # snoozed / focus mode
    NOT_APPLICABLE = "not_applicable"        # spoke instead


@dataclass
class InterventionDecision:
    """The judge's verdict. `should_speak` is false most of the time, and that
    is the product working correctly."""
    decision_id: str
    candidate_id: str
    ts: int
    should_speak: bool
    trajectory: Trajectory
    confidence: float                        # 0.0-1.0
    reasoning: str                           # 1-2 sentences, SHOWN TO THE USER
    signals_cited: list[SignalKind] = field(default_factory=list)
    decline_reason: DeclineReason = DeclineReason.NOT_APPLICABLE
    content: Optional[str] = None            # only when should_speak is True
    latency_ms: Optional[int] = None
    model: str = "gemini"
    prompt_version: Optional[str] = None     # Person 2: bump this every revision

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0,1]")
        if self.should_speak and not self.content:
            raise ValueError("should_speak=True requires content")
        if not self.should_speak and self.decline_reason is DeclineReason.NOT_APPLICABLE:
            raise ValueError("a declined decision needs a decline_reason")

    @staticmethod
    def create(candidate_id: str, **kw) -> "InterventionDecision":
        return InterventionDecision(
            decision_id=new_id("dec"),
            candidate_id=candidate_id,
            ts=now_ms(),
            **kw,
        )


# The JSON schema handed to Gemini for structured output. Person 2 owns this;
# it must stay in sync with InterventionDecision above.
GEMINI_DECISION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "trajectory": {
            "type": "string",
            "enum": [t.value for t in Trajectory],
            "description": "What the developer is actually doing right now.",
        },
        "should_speak": {
            "type": "boolean",
            "description": (
                "Whether breaking their concentration is worth it RIGHT NOW. "
                "Default to false. Silence is the correct answer most of the time."
            ),
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {
            "type": "string",
            "description": "One or two sentences, addressed to the developer, "
                           "explaining the call. Shown verbatim in the restraint log.",
        },
        "signals_cited": {
            "type": "array",
            "items": {"type": "string", "enum": [s.value for s in SignalKind]},
        },
        "decline_reason": {
            "type": "string",
            "enum": [d.value for d in DeclineReason],
        },
        "content": {
            "type": ["string", "null"],
            "description": "The actual thing to say. Null when should_speak is false.",
        },
    },
    "required": ["trajectory", "should_speak", "confidence", "reasoning",
                 "signals_cited", "decline_reason"],
}


# ---------------------------------------------------------------------------
# 5. the log  —  Person 2 writes, Person 3 renders, Person 4 scores
# ---------------------------------------------------------------------------

@dataclass
class DecisionRecord:
    """One line of restraint-log JSONL. EVERY candidate produces one of these,
    including — especially — the declines."""
    contract_version: str
    session_id: str
    candidate: CandidateMoment
    decision: InterventionDecision

    @staticmethod
    def create(session_id: str, candidate: CandidateMoment,
               decision: InterventionDecision) -> "DecisionRecord":
        return DecisionRecord(CONTRACT_VERSION, session_id, candidate, decision)


class FeedbackKind(str, Enum):
    ACCEPTED = "accepted"            # acted on it
    DISMISSED = "dismissed"          # closed without acting
    IGNORED = "ignored"              # never opened, expired
    BAD_TIMING = "bad_timing"        # right thing, wrong moment
    BAD_CONTENT = "bad_content"      # right moment, useless thing
    WANTED_HELP = "wanted_help"      # user asked while system was silent — a MISS


@dataclass
class FeedbackRecord:
    """Person 3 writes these to the same JSONL. BAD_TIMING vs BAD_CONTENT is
    the single most valuable distinction in the dataset — it separates a
    timing failure from a content failure, which is the whole thesis."""
    contract_version: str
    session_id: str
    ts: int
    decision_id: Optional[str]       # null for WANTED_HELP (no decision existed)
    kind: FeedbackKind
    note: Optional[str] = None

    @staticmethod
    def create(session_id: str, kind: FeedbackKind,
               decision_id: Optional[str] = None, note: Optional[str] = None
               ) -> "FeedbackRecord":
        return FeedbackRecord(CONTRACT_VERSION, session_id, now_ms(),
                              decision_id, kind, note)


# ---------------------------------------------------------------------------
# 6. eval  —  owned by Person 4
# ---------------------------------------------------------------------------

@dataclass
class LabeledMoment:
    """Hand-labeled ground truth from a recorded trace."""
    ts: int
    should_have_spoken: bool
    rationale: str
    tolerance_ms: int = 30000        # a hit within this window counts


@dataclass
class EvalResult:
    session_id: str
    candidates: int
    spoke: int
    declined: int
    interventions_per_hour: float
    hit_rate: float                  # labeled-true moments correctly spoken on
    false_positive_rate: float       # spoke where label says leave them alone
    p95_latency_ms: float
    prompt_version: Optional[str] = None

    def table(self) -> str:
        return (
            f"session            {self.session_id}\n"
            f"prompt             {self.prompt_version or '-'}\n"
            f"candidates         {self.candidates}\n"
            f"spoke / declined   {self.spoke} / {self.declined}\n"
            f"per hour           {self.interventions_per_hour:.2f}\n"
            f"hit rate           {self.hit_rate:.1%}\n"
            f"false positives    {self.false_positive_rate:.1%}\n"
            f"p95 latency        {self.p95_latency_ms:.0f} ms"
        )


# ---------------------------------------------------------------------------
# smoke test:  python contract.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ep = Episode(episode_id=new_id("ep"), started_ms=now_ms() - 240_000,
                 primary_path="src/parser.py")

    events = [
        Event(EventKind.TEST_RUN, now_ms() - 90_000, ep.episode_id,
              path="tests/test_parser.py", exit_code=1, tests_failed=2,
              error_fingerprint="AttributeError:NoneType.children"),
        Event(EventKind.EDIT, now_ms() - 60_000, ep.episode_id, path="src/parser.py"),
        Event(EventKind.TEST_RUN, now_ms() - 20_000, ep.episode_id,
              path="tests/test_parser.py", exit_code=1, tests_failed=2,
              error_fingerprint="AttributeError:NoneType.children"),
    ]
    for e in events:
        ep.event_count += 1

    snap = ActivitySnapshot.create(
        ep, events,
        open_paths=["src/parser.py", "tests/test_parser.py"],
        last_error_text="AttributeError: 'NoneType' object has no attribute 'children'",
        seconds_since_last_edit=18.0,
        session_elapsed_ms=1_800_000,
    )

    signals = [
        Signal(SignalKind.REPEATED_ERROR, 0.8,
               "same AttributeError fingerprint 3x in 4 minutes",
               now_ms() - 240_000, occurrences=3),
        Signal(SignalKind.TEST_LOOP, 0.6,
               "3 test runs, no change in pass count", now_ms() - 200_000, 3),
    ]

    cand = CandidateMoment.create(snap, signals, BudgetState(per_hour=3, remaining=2))

    declined = InterventionDecision.create(
        cand.candidate_id,
        should_speak=False,
        trajectory=Trajectory.CONVERGING,
        confidence=0.71,
        reasoning="They're still editing between runs, so the repeats look like "
                  "deliberate narrowing rather than being stuck.",
        signals_cited=[SignalKind.REPEATED_ERROR, SignalKind.TEST_LOOP],
        decline_reason=DeclineReason.STILL_CONVERGING,
        latency_ms=1840,
        prompt_version="judge-v3",
    )

    rec = DecisionRecord.create("sess_demo", cand, declined)
    print(to_json(rec)[:400], "...\n")

    fb = FeedbackRecord.create("sess_demo", FeedbackKind.BAD_TIMING,
                               declined.decision_id, note="was mid-thought")
    print(to_json(fb), "\n")

    print(EvalResult("sess_demo", 41, 3, 38, 2.4, 0.66, 0.07, 2310, "judge-v3").table())
    print(f"\ncontract {CONTRACT_VERSION} — ok")