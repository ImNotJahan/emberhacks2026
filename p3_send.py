"""
p3_send.py — one-line logging to the Surface mailbox (p3_server.py).

    from p3_send import log, considering, snoozed

    if snoozed():                 # dev hit snooze or pause: decline with USER_SUPPRESSED
        ...
    considering(candidate)        # optional: shows "considering…" in the surface
    decision = ...                # the Gemini call
    log(DecisionRecord.create(session_id, candidate, decision))

Standard library only, so the judge doesn't need FastAPI installed.
None of these raise. If the mailbox is down, log() appends the record to
unsent.jsonl instead and returns False; the next log() that gets through
sends those first.

Point it at another machine with the MAILBOX_URL env var, e.g.
    MAILBOX_URL=http://192.168.1.20:8766/log
"""

import json
import os
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

from contract import to_json, write_jsonl

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and every
# request waits ~1-2 s before falling back, since the server only listens on IPv4.
MAILBOX_URL = os.environ.get("MAILBOX_URL", "http://127.0.0.1:8766/log")
# accept ".../log", ".../log/", "http://host:8766" or "http://host:8766/"
BASE_URL = MAILBOX_URL.rstrip("/").removesuffix("/log").rstrip("/")
FALLBACK_PATH = "unsent.jsonl"


def _call(path: str, body=None) -> dict:
    """GET when body is None, otherwise POST it (a JSON string or a dict)."""
    data = None
    if body is not None:
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
    req = urllib.request.Request(BASE_URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1) as resp:
        return json.loads(resp.read())


def log(record) -> bool:
    """POST a DecisionRecord or FeedbackRecord to the mailbox. True if it was stored."""
    try:
        # the mailbox answers 200 with ok=false for records it doesn't recognise
        ok = bool(_call("/log", to_json(record)).get("ok"))
    except HTTPError:
        return False          # the server is up but refused it; saving it won't help
    except Exception:
        try:
            write_jsonl(FALLBACK_PATH, [record])
        except OSError:
            pass
        return False
    _flush_unsent()
    return ok


def _flush_unsent() -> None:
    """Send what piled up in unsent.jsonl while the mailbox was down. The
    server ignores records it already has, so a partial flush is safe to redo."""
    path = Path(FALLBACK_PATH)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            _call("/log", line)
        except HTTPError:
            continue          # refused: drop it
        except Exception:
            try:
                path.write_text("\n".join(lines[i:]) + "\n", encoding="utf-8")
            except OSError:
                pass
            return            # down again: keep the rest for next time
    try:
        path.unlink()
    except OSError:
        pass


def considering(candidate) -> None:
    """Tell the surface the judge is weighing this CandidateMoment. Call it
    right before the Gemini call; the next log() of its decision clears it."""
    try:
        _call("/considering", {"candidate_id": candidate.candidate_id})
    except Exception:
        pass


def snoozed() -> bool:
    """True while the developer has snoozed or paused the assistant from the surface."""
    try:
        s = _call("/state")
        return s.get("paused") is True or s["snoozed_until"] > s["now"]   # the server's clock, not this machine's
    except Exception:
        return False
