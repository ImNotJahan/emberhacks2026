"""CandidateMoment -> compact text for the prompt.

Compact on purpose: this text is the context window and most of the latency
budget. Times are rendered relative to the candidate (so replayed traces read
the same as live ones), and empty fields are dropped.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Optional

from contract import CandidateMoment, Event, EventKind
from judge.knowledge import clean_lines, collapse_repeats, render_hints

MAX_EVENTS = 40
MAX_TEXT = 160


def _rel(ms: int, now: int) -> str:
    return f"-{max(0, (now - ms) // 1000)}s"


def _collapse_edits(events: list[Event]) -> list[tuple[Event, int, int, int]]:
    """The editor sends one EDIT per keystroke. Merge consecutive edits to the
    same file into one row: (last edit, count, chars added, chars removed)."""
    rows: list[tuple[Event, int, int, int]] = []
    for e in events:
        add, rem = int(e.meta.get("added") or 0), int(e.meta.get("removed") or 0)
        if (e.kind is EventKind.EDIT and rows and rows[-1][0].kind is EventKind.EDIT
                and rows[-1][0].path == e.path):
            last, n, a, r = rows[-1]
            rows[-1] = (e if e.text or not last.text else last, n + 1, a + add, r + rem)
            # keep the newest timestamp even when the older row had the text
            rows[-1][0].ts = max(rows[-1][0].ts, e.ts)
        else:
            rows.append((e, 1, add, rem))
    return rows


def _event_line(e: Event, now: int, n: int = 1, added: int = 0, removed: int = 0) -> str:
    parts = [_rel(e.ts, now), e.kind.value]
    if e.path:
        parts.append(e.path)
    if e.kind is EventKind.EDIT and (n > 1 or added or removed):
        parts.append(f"x{n} +{added}/-{removed} chars" if n > 1 else f"+{added}/-{removed} chars")
    if e.meta.get("cmd"):
        parts.append(f"$ {str(e.meta['cmd'])[:60]}")
    if e.kind is EventKind.DIAGNOSTIC and e.meta.get("count") is not None:
        parts.append(f"count={e.meta['count']}")
    if e.exit_code is not None:
        parts.append(f"exit={e.exit_code}")
    if e.tests_passed is not None or e.tests_failed is not None:
        parts.append(f"pass={e.tests_passed or 0} fail={e.tests_failed or 0}")
    if e.error_fingerprint:
        parts.append(f"err={e.error_fingerprint}")
    if e.text and e.kind is EventKind.TERMINAL_OUT:
        # Output: repeats collapsed, and the TAIL, where the error/summary is.
        t = " | ".join(collapse_repeats(e.text).splitlines())
        if len(t) > MAX_TEXT:   # the dominant repeated line, if any, + the tail
            top = Counter(clean_lines(e.text)).most_common(1)
            head = f"{top[0][0][:50]} [x{top[0][1]} in total]" if top and top[0][1] >= 3 else ""
            t = f"{head} … {t[-(MAX_TEXT - len(head)):]}"
        parts.append(f'"{t}"')
    elif e.text:
        t = " ".join(e.text.split())
        parts.append(f'"{t[:MAX_TEXT]}{"…" if len(t) > MAX_TEXT else ""}"')
    return " ".join(parts)


@dataclass
class LastSpoken:
    """The judge's memory of its own last interruption."""
    episode_id: str
    ts: int
    content: Optional[str]


def render_candidate(c: CandidateMoment, max_events: int = MAX_EVENTS,
                     last_spoken: Optional[LastSpoken] = None, hints: bool = False) -> str:
    """hints=True appends judge.knowledge's matched debugging patterns."""
    now = c.ts
    s = c.snapshot
    ep = s.episode
    lines: list[str] = []

    lines.append("## Episode")
    lines.append(
        f"id={ep.episode_id} primary={ep.primary_path or '-'} "
        f"running {((ep.ended_ms or now) - ep.started_ms) // 1000}s, "
        f"{ep.event_count} events, session {s.session_elapsed_ms // 60000}min in, "
        f"{s.seconds_since_last_edit:.0f}s since last edit"
    )
    if ep.summary:
        lines.append(f"summary: {ep.summary}")
    if s.open_paths:
        lines.append(f"open: {', '.join(s.open_paths)}")

    lines.append("\n## Signals (cheap heuristics that nominated this moment)")
    for sig in c.signals:
        lines.append(
            f"- {sig.kind.value} strength={sig.strength:.2f} x{sig.occurrences}: {sig.detail}"
        )

    rows = _collapse_edits([replace(e) for e in s.recent_events])
    shown = rows[-max_events:]
    dropped = len(rows) - len(shown)
    lines.append(f"\n## Episode history (oldest first{f', {dropped} older omitted' if dropped else ''})")
    lines.extend(_event_line(*row[:1], now, *row[1:]) for row in shown)

    if s.last_error_text:
        # Tail, not head: test runners put the failure and summary at the end.
        err = collapse_repeats(s.last_error_text).strip()
        lines.append(f"\n## Last error\n{'…' if len(err) > 600 else ''}{err[-600:]}")
    if s.last_test_summary:
        lines.append(f"\n## Last test summary\n{s.last_test_summary[:400]}")

    b = c.budget
    since = (
        f"{(now - b.last_intervention_ms) / 60000:.1f}min ago"
        if b.last_intervention_ms is not None else "never this session"
    )
    if b.per_hour:
        lines.append("\n## Interruption budget")
        lines.append(
            f"{b.remaining} of {b.per_hour} interruptions left this hour; "
            f"last interruption {since}; {b.interventions_this_session} so far this session"
        )
    else:   # no budget (v8+): only the history, so the judge can avoid repeating itself
        lines.append("\n## Your previous interruptions")
        lines.append(f"last interruption {since}; {b.interventions_this_session} so far this session")
    if last_spoken is not None:
        where = "THIS episode" if last_spoken.episode_id == ep.episode_id else "an earlier episode"
        lines.append(f"Last interruption was in {where}: \"{(last_spoken.content or '')[:200]}\"")
    if hints:
        h = render_hints(c)
        if h:
            lines.append(h)
    return "\n".join(lines)
