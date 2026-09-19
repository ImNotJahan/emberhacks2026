"""
p3_send.py — one-line logging to the Surface mailbox (p3_server.py).

    from p3_send import log, considering, snoozed

    if snoozed():                 # dev hit snooze: decline with USER_SUPPRESSED
        ...
    considering(candidate)        # optional: shows "considering…" in the surface
    decision = ...                # the Gemini call
    log(DecisionRecord.create(session_id, candidate, decision))

Standard library only, so the judge doesn't need FastAPI installed.
None of these raise. If the mailbox is down, log() appends the record to
unsent.jsonl instead and returns False.

Point it at another machine with the MAILBOX_URL env var, e.g.
    MAILBOX_URL=http://192.168.1.20:8766/log
"""

import json
import os
import urllib.request

from contract import now_ms, to_json, write_jsonl

MAILBOX_URL = os.environ.get("MAILBOX_URL", "http://localhost:8766/log")
BASE_URL = MAILBOX_URL.rsplit("/log", 1)[0]
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
        return bool(_call("/log", to_json(record)).get("ok"))
    except Exception:
        write_jsonl(FALLBACK_PATH, [record])
        return False


def considering(candidate) -> None:
    """Tell the surface the judge is weighing this CandidateMoment. Call it
    right before the Gemini call; the next log() of its decision clears it."""
    try:
        _call("/considering", {"candidate_id": candidate.candidate_id})
    except Exception:
        pass


def snoozed() -> bool:
    """True while the developer has snoozed the assistant from the surface."""
    try:
        return _call("/state")["snoozed_until"] > now_ms()
    except Exception:
        return False
