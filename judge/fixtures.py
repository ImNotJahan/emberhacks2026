"""Hardcoded candidates for exercising the judge before real traces exist."""

from __future__ import annotations

from contract import (
    ActivitySnapshot,
    BudgetState,
    CandidateMoment,
    Episode,
    Event,
    EventKind,
    Signal,
    SignalKind,
)


def parser_repeated_error(now: int) -> CandidateMoment:
    """Same AttributeError three runs in a row, with edits in between — the
    contract's own example, and a genuinely ambiguous moment."""
    ep = Episode(episode_id="ep_parser01", started_ms=now - 240_000,
                 primary_path="src/parser.py", event_count=7)
    fp = "AttributeError:NoneType.children"
    events = [
        Event(EventKind.FILE_OPEN, now - 240_000, ep.episode_id, path="src/parser.py", lang="python"),
        Event(EventKind.TEST_RUN, now - 200_000, ep.episode_id, path="tests/test_parser.py",
              exit_code=1, tests_passed=14, tests_failed=2, error_fingerprint=fp),
        Event(EventKind.EDIT, now - 150_000, ep.episode_id, path="src/parser.py",
              text="node = self._parse_block(tokens)"),
        Event(EventKind.TEST_RUN, now - 110_000, ep.episode_id, path="tests/test_parser.py",
              exit_code=1, tests_passed=14, tests_failed=2, error_fingerprint=fp),
        Event(EventKind.EDIT, now - 60_000, ep.episode_id, path="src/parser.py",
              text="if node is None: continue"),
        Event(EventKind.SAVE, now - 55_000, ep.episode_id, path="src/parser.py"),
        Event(EventKind.TEST_RUN, now - 20_000, ep.episode_id, path="tests/test_parser.py",
              exit_code=1, tests_passed=14, tests_failed=2, error_fingerprint=fp),
    ]
    snap = ActivitySnapshot(
        snapshot_id="snap_parser01", ts=now, episode=ep, recent_events=events,
        open_paths=["src/parser.py", "tests/test_parser.py"],
        last_error_text="AttributeError: 'NoneType' object has no attribute 'children'\n"
                        "  File \"src/parser.py\", line 88, in _walk\n"
                        "    for child in node.children:",
        last_test_summary="14 passed, 2 failed: test_nested_block, test_empty_block",
        seconds_since_last_edit=18.0,
        session_elapsed_ms=1_800_000,
    )
    signals = [
        Signal(SignalKind.REPEATED_ERROR, 0.8,
               "same AttributeError fingerprint 3x in 4 minutes", now - 200_000, occurrences=3),
        Signal(SignalKind.TEST_LOOP, 0.6,
               "3 test runs, no change in pass count", now - 200_000, occurrences=3),
    ]
    return CandidateMoment(candidate_id="cand_parser01", ts=now, snapshot=snap,
                           signals=signals, budget=BudgetState(per_hour=3, remaining=2))
