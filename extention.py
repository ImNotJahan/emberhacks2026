"""
extention.py — Person 1 capture engine.

Reads raw observations (one JSON object per line on stdin, produced by
extension.js), and turns them into contract objects:

    raw line -> redact -> fingerprint -> Event -> episode tag -> ring buffer
             -> signals -> CandidateMoment -> GET /candidates (localhost)

Uses contract.py as the only interface. Standard library only.

    python extention.py --port 8765 [--record trace.raw.jsonl] [--no-redact]
    python extention.py --replay trace.raw.jsonl --out candidates.jsonl
    python extention.py --demo-redact 'api_key = "sk-abc123def456ghi789jkl"'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from contract import (
    CONTRACT_VERSION, ActivitySnapshot, BudgetState, CandidateMoment, Episode,
    EpisodeEnd, Event, EventKind, Signal, SignalKind, new_id, now_ms, to_json, write_jsonl,
)

# ===========================================================================
# 1. redaction  —  everything below runs BEFORE an Event is constructed
# ===========================================================================

_SECRET_WORDS = (r"api[_-]?key|secret|token|passw(?:or)?d|passwd|pwd|credential|"
                 r"private[_-]?key|access[_-]?key|auth(?:orization)?|bearer")
_KEYISH = rf"[\w.-]*(?:{_SECRET_WORDS})[\w.-]*"

# (name, compiled regex, replacement). Ordered; each rule is one line you can
# point at in the demo. `python extention.py --demo-redact "<line>"` shows them.
REDACTION_RULES: list[tuple[str, re.Pattern, str]] = [
    ("private-key block",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED:private-key]"),
    ("key=\"string literal\"",           # password = "hunter2", "api_key": 'abc'
     re.compile(rf"(?i)(\b{_KEYISH}[\"']?\s*[:=]\s*)([\"'])(.*?)\2"),
     r"\1\2[REDACTED]\2"),
    ("KEY=bare value",                   # export TOKEN=abc123, SECRET: xyz
     re.compile(rf"(?i)(\b{_KEYISH}\s*[:=]\s*)(?![\"'\[])([^\s\"',;)]+)"),
     r"\1[REDACTED]"),
    ("literal containing secret word",   # "my-secret-value" (no spaces)
     re.compile(rf"([\"'])(?=[^\s\"']*(?:{_SECRET_WORDS}))[^\s\"']{{6,}}\1(?!\s*[:\]=])", re.I),
     "\"[REDACTED]\""),
    ("known token shapes",
     re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|"
                r"xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"),
     "[REDACTED:token]"),
    ("Bearer header",
     re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
     "Bearer [REDACTED]"),
    ("credentials in URL",
     re.compile(r"(://[^/\s:@]+:)[^/\s@]+(@)"),
     r"\1[REDACTED]\2"),
]

_ENV_FILE = re.compile(r"(^|[\\/])(\.env(\..*)?|.*\.env)$", re.I)


class Redactor:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.stats: Counter = Counter()

    def is_env_file(self, path: Optional[str]) -> bool:
        return bool(path and _ENV_FILE.search(path))

    def redact(self, text: Optional[str], path: Optional[str] = None) -> Optional[str]:
        if text is None or not self.enabled:
            return text
        if self.is_env_file(path):               # .env contents never pass through
            self.stats[".env file"] += 1
            return "[REDACTED:.env contents]"
        for name, rx, repl in REDACTION_RULES:
            text, n = rx.subn(repl, text)
            if n:
                self.stats[name] += n
        return text

    def explain(self, text: str) -> str:
        """Show which rule stripped what. Used for the demo."""
        lines = [f"in : {text}"]
        for name, rx, repl in REDACTION_RULES:
            new, n = rx.subn(repl, text)
            if n:
                lines.append(f"rule: {name}  ->  {new}")
                text = new
        lines.append(f"out: {text}")
        return "\n".join(lines)


# ===========================================================================
# 2. fingerprints and test summaries
# ===========================================================================

_ERR_TYPE = re.compile(r"\b([A-Z][A-Za-z0-9]*(?:Error|Exception|Failure)|error TS\d+|TS\d+|E\d{4})\b")
_PATHISH = re.compile(r"(?:[A-Za-z]:)?[\\/][\w .\\/-]+|(?:[\w-]+[\\/])+[\w.-]+")


def fingerprint(text: Optional[str]) -> Optional[str]:
    """Normalized error identity: same bug -> same string across runs."""
    if not text:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    line = next((l for l in lines if _ERR_TYPE.search(l)), None)
    m = _ERR_TYPE.search(line) if line else None
    if line is None:
        line = lines[-1] if lines else ""
    etype = m.group(1) if m else "error"
    msg = line[m.end():] if m else line
    msg = _PATHISH.sub("<path>", msg)
    msg = re.sub(r"0x[0-9a-fA-F]+", "<hex>", msg)
    msg = re.sub(r"\b\d+\b", "N", msg)
    msg = re.sub(r"\s+", " ", msg).strip(" :-")[:80].lower()
    return f"{etype}:{msg}"


def parse_test_counts(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """(passed, failed) from pytest / jest / mocha style summaries."""
    if not text:
        return None, None
    passed = re.findall(r"(\d+)\s+(?:passed|passing)", text)
    failed = re.findall(r"(\d+)\s+(?:failed|failing)", text)
    if not passed and not failed:
        return None, None
    return (int(passed[-1]) if passed else 0), (int(failed[-1]) if failed else 0)


# ===========================================================================
# 3. raw observation -> contract.Event
# ===========================================================================

def _safe_path(p: Optional[str]) -> Optional[str]:
    if not p:
        return None
    p = p.replace("\\", "/")
    return os.path.basename(p) if p.startswith("/") or re.match(r"^[A-Za-z]:", p) else p


def events_from_raw(raw: dict, redactor: Redactor) -> list[Event]:
    """One raw line may yield several Events (e.g. test run + git commit)."""
    kind = raw.get("k")
    ts = int(raw.get("ts") or 0)
    path = _safe_path(raw.get("path"))
    lang = raw.get("lang")
    meta = dict(raw.get("meta") or {})
    text = redactor.redact(raw.get("text"), path)
    if text is not None:
        text = text[-MAX_TEXT:]
    common = dict(ts=ts, path=path, lang=lang)

    if kind == "terminal_out":
        cmd = redactor.redact(meta.pop("cmd", ""), None) or ""
        meta["cmd"] = cmd[:200]
        code = raw.get("exit_code")
        events: list[Event] = []
        if meta.get("is_test"):
            passed, failed = parse_test_counts(text)
            events.append(Event(EventKind.TEST_RUN, exit_code=code, tests_passed=passed,
                                tests_failed=failed, text=text, meta=meta,
                                error_fingerprint=fingerprint(text) if code else None, **common))
        else:
            events.append(Event(EventKind.TERMINAL_OUT, exit_code=code, text=text, meta=meta,
                                error_fingerprint=fingerprint(text) if code else None, **common))
        if re.match(r"\s*git\s+commit\b", cmd) and code == 0:
            events.append(Event(EventKind.GIT, text=cmd[:200], meta={"op": "commit"}, **common))
        return events

    try:
        ek = EventKind(kind)
    except ValueError:
        return []
    fp = fingerprint(text) if ek is EventKind.DIAGNOSTIC else None
    return [Event(ek, exit_code=raw.get("exit_code"), text=text, error_fingerprint=fp,
                  meta=meta, **common)]


# ===========================================================================
# 4. ring buffer  —  bounded by time AND count
# ===========================================================================

WINDOW_MS = 5 * 60_000
MAX_EVENTS = 2000
MAX_TEXT = 1500


class RingBuffer:
    """Last ~5 minutes of events, never more than MAX_EVENTS. A burst of typing
    cannot grow it: the deque evicts the oldest entry at the count cap."""

    def __init__(self, window_ms: int = WINDOW_MS, max_events: int = MAX_EVENTS) -> None:
        self.window_ms = window_ms
        self._d: deque[Event] = deque(maxlen=max_events)

    def append(self, e: Event) -> None:
        self._d.append(e)
        self.prune(e.ts)

    def prune(self, now: int) -> None:
        while self._d and now - self._d[0].ts > self.window_ms:
            self._d.popleft()

    def __len__(self) -> int:
        return len(self._d)

    def events(self) -> list[Event]:
        return list(self._d)


# ===========================================================================
# 5. episode segmentation  —  idle >45s, test fail->pass, git commit
# ===========================================================================

IDLE_START_MS = 30_000     # emit IDLE_START after this much silence
IDLE_EPISODE_MS = 45_000   # ...and close the episode after this much


class Segmenter:
    """Tags every event with an episode_id. Time comes from event timestamps
    and tick(now), never from the wall clock, so replay is deterministic."""

    def __init__(self) -> None:
        self.current: Optional[Episode] = None
        self.closed: deque[Episode] = deque(maxlen=100)
        self.last_ts: Optional[int] = None
        self.idle_open = False
        self.last_test_failed = False
        self.hard_boundary_ts = 0          # last fail->pass or commit; detectors reset here
        self.boundary: Optional[EpisodeEnd] = None   # consumed by the engine
        self._paths: Counter = Counter()

    def _open(self, ts: int) -> None:
        self.current = Episode(episode_id=new_id("ep"), started_ms=ts)
        self._paths.clear()

    def _close(self, ended_ms: int, why: EpisodeEnd) -> None:
        ep = self.current
        if ep is None:
            return
        ep.ended_ms, ep.ended_by = ended_ms, why
        self.closed.append(ep)
        self.current = None
        if why in (EpisodeEnd.TEST_PASSED, EpisodeEnd.COMMIT):
            self.hard_boundary_ts = ended_ms
            self.boundary = why

    def tick(self, now: int) -> list[Event]:
        """Call periodically. May emit a synthetic IDLE_START, or close on idle."""
        out: list[Event] = []
        if self.current is None or self.last_ts is None:
            return out
        quiet = now - self.last_ts
        if quiet > IDLE_START_MS and not self.idle_open:
            self.idle_open = True
            out.append(Event(EventKind.IDLE_START, ts=self.last_ts + IDLE_START_MS,
                             episode_id=self.current.episode_id))
            self.current.event_count += 1
        if quiet > IDLE_EPISODE_MS:
            self._close(self.last_ts, EpisodeEnd.IDLE)
        return out

    def tag(self, e: Event) -> list[Event]:
        """Assign an episode to `e`; returns [e] plus any synthetic IDLE_END."""
        out: list[Event] = []
        if self.current is not None and self.last_ts is not None \
                and e.ts - self.last_ts > IDLE_EPISODE_MS:
            self._close(self.last_ts, EpisodeEnd.IDLE)
        if self.current is None:
            self._open(e.ts)
        if self.idle_open:
            self.idle_open = False
            out.append(Event(EventKind.IDLE_END, ts=e.ts, episode_id=self.current.episode_id))
            self.current.event_count += 1

        ep = self.current
        e.episode_id = ep.episode_id
        ep.event_count += 1
        self.last_ts = e.ts
        if e.kind is EventKind.EDIT and e.path:
            self._paths[e.path] += 1
        if e.path and (ep.primary_path is None or e.kind is EventKind.EDIT):
            ep.primary_path = self._paths.most_common(1)[0][0] if self._paths else e.path
        out.append(e)

        # boundaries: the event belongs to the episode it closes
        if e.kind is EventKind.TEST_RUN:
            failed = e.exit_code not in (0, None)
            if e.exit_code == 0 and self.last_test_failed:
                self._close(e.ts, EpisodeEnd.TEST_PASSED)
            self.last_test_failed = failed or (e.exit_code is None and self.last_test_failed)
        elif e.kind is EventKind.GIT and e.meta.get("op") == "commit":
            self._close(e.ts, EpisodeEnd.COMMIT)
        return out


# ===========================================================================
# 6. candidate heuristics  —  cheap, local, NO model calls
# ===========================================================================

def _gapped_count(times: list[int], gap_ms: int) -> tuple[int, int]:
    """Occurrences at least gap_ms apart (diagnostics re-fire on every keystroke).
    Returns (count, first_seen)."""
    n, last, first = 0, None, 0
    for t in sorted(times):
        if last is None or t - last >= gap_ms:
            n += 1
            first = first or t
            last = t
    return n, first


def detect_repeated_error(win: list[Event], now: int) -> Optional[Signal]:
    errs = [e for e in win if e.error_fingerprint]
    if not errs:
        return None
    latest = errs[-1].error_fingerprint          # error changed => progress, stay quiet
    n, first = _gapped_count([e.ts for e in errs if e.error_fingerprint == latest], 10_000)
    if n < 3:
        return None
    return Signal(SignalKind.REPEATED_ERROR, min(1.0, 0.5 + 0.1 * (n - 2)),
                  f"same error seen {n}x: {latest[:70]}", first, n)


def detect_file_thrash(win: list[Event], now: int) -> Optional[Signal]:
    recent = [e for e in win if now - e.ts <= 120_000]
    visits = Counter(e.path for e in recent
                     if e.kind in (EventKind.FILE_FOCUS, EventKind.FILE_OPEN) and e.path)
    if not visits:
        return None
    path, n = visits.most_common(1)[0]
    if n < 4:
        return None
    edits = sum(1 for e in recent if e.kind is EventKind.EDIT)
    strength = min(0.9, 0.4 + 0.1 * (n - 4))
    if edits >= 10:                              # revisiting while writing code = working
        strength *= 0.5
    first = min(e.ts for e in recent if e.path == path)
    return Signal(SignalKind.FILE_THRASH, strength,
                  f"{path} revisited {n}x in 2 min with {edits} edits", first, n)


def detect_stalled_with_error(win: list[Event], now: int) -> Optional[Signal]:
    last_diag: dict[str, Event] = {}
    for e in win:
        if e.kind is EventKind.DIAGNOSTIC and e.path:
            last_diag[e.path] = e
    open_diags = [e for e in last_diag.values() if (e.meta.get("count") or 0) > 0]
    edits = [e.ts for e in win if e.kind is EventKind.EDIT]
    if not open_diags or not edits:
        return None
    idle = (now - max(edits)) / 1000.0
    if idle <= 30:
        return None
    d = max(open_diags, key=lambda e: e.ts)
    return Signal(SignalKind.STALLED_WITH_ERROR, min(0.9, 0.5 + (idle - 30) / 150),
                  f"no edits for {idle:.0f}s with unresolved error in {d.path}",
                  d.ts, len(open_diags))


def detect_test_loop(win: list[Event], now: int) -> Optional[Signal]:
    runs = [e for e in win if e.kind is EventKind.TEST_RUN]
    if len(runs) < 3:
        return None
    tail = runs[-3:]
    if any(r.exit_code == 0 for r in tail):
        return None
    passed = {r.tests_passed or 0 for r in tail}
    if len(passed) > 1:                          # passing count moved => progress
        return None
    n = 0
    for r in reversed(runs):                     # length of the trailing no-delta streak
        if r.exit_code != 0 and (r.tests_passed or 0) in passed:
            n += 1
        else:
            break
    return Signal(SignalKind.TEST_LOOP, min(0.9, 0.5 + 0.1 * (n - 3)),
                  f"{n} test runs, no change in pass count", tail[0].ts, n)


DETECTORS: list[Callable[[list[Event], int], Optional[Signal]]] = [
    detect_repeated_error, detect_file_thrash, detect_stalled_with_error, detect_test_loop,
]


# ===========================================================================
# 7. engine  —  events in, CandidateMoments out
# ===========================================================================

MIN_CANDIDATE_GAP_MS = 60_000     # one Gemini call per candidate: don't spam the judge
GATE_STRENGTH = 0.55              # lone signals below this are not worth a call
REPEAT_SUPPRESS_MS = 180_000      # same signal kinds again within 3 min: skip


class Engine:
    def __init__(self, redactor: Redactor, min_gap_ms: int = MIN_CANDIDATE_GAP_MS,
                 events_out: Optional[str] = None, candidates_out: Optional[str] = None) -> None:
        self.redactor = redactor
        self.buf = RingBuffer()
        self.seg = Segmenter()
        self.candidates: deque[CandidateMoment] = deque(maxlen=50)
        self.budget = BudgetState()
        self.min_gap_ms = min_gap_ms
        self.events_out, self.candidates_out = events_out, candidates_out
        self.session_start: Optional[int] = None
        self.last_candidate_ts: Optional[int] = None
        self.last_kinds: set[SignalKind] = set()
        self.lock = threading.RLock()

    # -- input -------------------------------------------------------------
    def _store(self, events: list[Event]) -> None:
        for e in events:
            if self.session_start is None:
                self.session_start = e.ts
            self.buf.append(e)
        if self.events_out and events:
            write_jsonl(self.events_out, events)

    def ingest(self, raw: dict) -> Optional[CandidateMoment]:
        with self.lock:
            raw.setdefault("ts", now_ms())
            for ev in events_from_raw(raw, self.redactor):
                self._store(self.seg.tag(ev))
            return self._evaluate(int(raw["ts"]))

    def tick(self, now: int) -> Optional[CandidateMoment]:
        with self.lock:
            self._store(self.seg.tick(now))
            self.buf.prune(now)
            return self._evaluate(now)

    # -- candidate decision ------------------------------------------------
    def _evaluate(self, now: int) -> Optional[CandidateMoment]:
        win = [e for e in self.buf.events() if e.ts > self.seg.hard_boundary_ts]
        signals = [s for d in DETECTORS if (s := d(win, now))]
        boundary, self.seg.boundary = self.seg.boundary, None
        if not signals:
            return None
        if max(s.strength for s in signals) < GATE_STRENGTH and len(signals) < 2:
            return None
        if self.last_candidate_ts is not None and now - self.last_candidate_ts < self.min_gap_ms:
            return None
        kinds = {s.kind for s in signals}
        if (self.last_kinds and kinds <= self.last_kinds
                and now - (self.last_candidate_ts or 0) < REPEAT_SUPPRESS_MS):
            return None                # nothing new since the last candidate
        if boundary is not None:       # a coarse breakpoint: cheap to interrupt
            signals.append(Signal(SignalKind.EPISODE_BOUNDARY, 0.3,
                                  f"episode closed by {boundary.value}", now))
        cand = self._build(now, signals)
        self.last_candidate_ts = now
        self.last_kinds = kinds
        self.candidates.append(cand)
        if self.candidates_out:
            write_jsonl(self.candidates_out, [cand])
        return cand

    def _build(self, now: int, signals: list[Signal]) -> CandidateMoment:
        from dataclasses import replace
        events = self.buf.events()
        ep = self.seg.current or (self.seg.closed[-1] if self.seg.closed else None)
        ep = replace(ep) if ep else Episode(new_id("ep"), now)
        recent = events[-80:]
        edits = [e.ts for e in events if e.kind is EventKind.EDIT]
        start = self.session_start if self.session_start is not None else now

        paths: list[str] = []
        for e in reversed(events):
            if e.path and e.path not in paths and e.kind in (
                    EventKind.FILE_OPEN, EventKind.FILE_FOCUS, EventKind.EDIT, EventKind.SAVE):
                paths.append(e.path)
        last_err = next((e.text for e in reversed(events) if e.error_fingerprint and e.text), None)
        last_test = next((e for e in reversed(events) if e.kind is EventKind.TEST_RUN), None)
        test_summary = None
        if last_test is not None:
            test_summary = (f"{last_test.tests_passed or 0} passed, {last_test.tests_failed or 0} failed"
                            f" (exit {last_test.exit_code})")

        # Built directly (not via .create) so ts is the event clock, not the wall
        # clock: replayed traces then produce identical timestamps.
        snap = ActivitySnapshot(
            snapshot_id=new_id("snap"), ts=now, episode=ep, recent_events=recent,
            open_paths=paths[:10],
            last_error_text=last_err[-MAX_TEXT:] if last_err else None,
            last_test_summary=test_summary,
            seconds_since_last_edit=((now - max(edits)) / 1000.0) if edits else (now - start) / 1000.0,
            session_elapsed_ms=now - start,
        )
        return CandidateMoment(candidate_id=new_id("cand"), ts=now, snapshot=snap,
                               signals=signals, budget=replace(self.budget))

    # -- budget (owned by the judge/surface; we only carry it) ---------------
    def set_budget(self, fields: dict) -> BudgetState:
        with self.lock:
            for k, v in fields.items():
                if k in ("per_hour", "remaining", "last_intervention_ms", "interventions_this_session"):
                    setattr(self.budget, k, v)
            return self.budget


# ===========================================================================
# 8. local endpoint for Person 2's judge
# ===========================================================================

def make_handler(engine: Engine):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with engine.lock:
                if u.path == "/health":
                    return self._send(200, json.dumps({
                        "ok": True, "contract_version": CONTRACT_VERSION,
                        "redaction_enabled": engine.redactor.enabled,
                        "buffered_events": len(engine.buf),
                        "candidates": len(engine.candidates)}))
                if u.path == "/candidates":
                    since = int(q.get("since", 0))
                    limit = int(q.get("limit", 20))
                    items = [c for c in engine.candidates if c.ts > since][-limit:]
                    return self._send(200, "[" + ",".join(to_json(c) for c in items) + "]")
                if u.path == "/candidates/latest":
                    if not engine.candidates:
                        return self._send(204, "")
                    return self._send(200, to_json(engine.candidates[-1]))
                if u.path == "/events":
                    limit = int(q.get("limit", 100))
                    return self._send(200, "[" + ",".join(to_json(e) for e in engine.buf.events()[-limit:]) + "]")
                if u.path == "/redaction":
                    return self._send(200, json.dumps({
                        "enabled": engine.redactor.enabled,
                        "rules": [n for n, _, _ in REDACTION_RULES],
                        "stripped_counts": dict(engine.redactor.stats)}))
            self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, json.dumps({"error": "bad json"}))
            if self.path == "/budget":
                return self._send(200, to_json(engine.set_budget(body)))
            self._send(404, json.dumps({"error": "not found"}))

        def log_message(self, *a) -> None:      # keep stdout for engine logs
            pass
    return H


# ===========================================================================
# 9. entry points
# ===========================================================================

def describe(c: CandidateMoment) -> str:
    return "candidate %s  %s" % (c.candidate_id, "; ".join(
        f"{s.kind.value}({s.strength:.2f}): {s.detail}" for s in c.signals))


def replay(engine: Engine, lines, step_ms: int = 5000) -> list[CandidateMoment]:
    """Feed a recorded raw trace through the engine on the trace's own clock,
    ticking every step_ms across gaps so idle/stall logic fires as it did live."""
    out: list[CandidateMoment] = []
    last = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        ts = int(raw["ts"])
        if last is not None:
            t = last
            while t + step_ms < ts:
                t += step_ms
                c = engine.tick(t)
                if c:
                    out.append(c)
        c = engine.ingest(raw)
        if c:
            out.append(c)
        last = ts
    if last is not None:                        # let trailing stalls/idle resolve
        for i in range(1, 25):
            c = engine.tick(last + i * step_ms)
            if c:
                out.append(c)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Big Brother capture engine")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-redact", action="store_true", help="disable redaction (demo toggle)")
    ap.add_argument("--record", help="append raw stdin lines here (replayable trace)")
    ap.add_argument("--events-out", help="append normalized contract Events here (JSONL)")
    ap.add_argument("--out", help="append CandidateMoments here (JSONL)")
    ap.add_argument("--replay", help="run a raw trace file instead of reading stdin")
    ap.add_argument("--demo-redact", metavar="LINE", help="show what redaction strips, then exit")
    a = ap.parse_args()

    redactor = Redactor(enabled=not a.no_redact)
    if a.demo_redact is not None:
        print(redactor.explain(a.demo_redact))
        return 0

    engine = Engine(redactor, events_out=a.events_out, candidates_out=a.out)

    if a.replay:
        with open(a.replay, "r", encoding="utf-8") as fh:
            cands = replay(engine, fh)
        for c in cands:
            print(describe(c))
        print(f"{len(cands)} candidates from {len(engine.buf)} buffered events", file=sys.stderr)
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(engine))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"engine up: http://127.0.0.1:{a.port}/candidates  redaction={'on' if redactor.enabled else 'OFF'}",
          flush=True)

    def ticker() -> None:
        import time
        while True:
            time.sleep(2)
            c = engine.tick(int(time.time() * 1000))
            if c:
                print(describe(c), flush=True)
    threading.Thread(target=ticker, daemon=True).start()

    rec = open(a.record, "a", encoding="utf-8") if a.record else None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if rec:                                   # scrubbed first: secrets never hit disk
            rec.write(json.dumps(_scrub_raw(raw, redactor)) + "\n")
            rec.flush()
        c = engine.ingest(raw)
        if c:
            print(describe(c), flush=True)
    return 0


def _scrub_raw(raw: dict, redactor: Redactor) -> dict:
    """The recorded trace is redacted too: secrets must not land on disk either."""
    r = dict(raw)
    r["text"] = redactor.redact(raw.get("text"), raw.get("path"))
    if isinstance(r.get("meta"), dict) and "cmd" in r["meta"]:
        r["meta"] = {**r["meta"], "cmd": redactor.redact(r["meta"]["cmd"])}
    return r


if __name__ == "__main__":
    sys.exit(main())
