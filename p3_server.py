import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

HERE = Path(__file__).parent

# Every accepted record is appended here and reloaded on startup, so a restart
# doesn't wipe the timeline. Delete the file (or point MAILBOX_LOG elsewhere)
# for a clean slate. logs/ is gitignored.
LOG_PATH = Path(os.environ.get("MAILBOX_LOG", HERE / "logs" / "mailbox.jsonl"))

app = FastAPI()
decisions = []   # DecisionRecords from the judge
feedback = []    # FeedbackRecords from the UI
_lock = threading.Lock()   # sync endpoints run in a threadpool

# Live presence state for the surface. Not part of contract.py on purpose:
# it's UI state, never replayed or scored. Only the pause switch is saved, so
# a restarted server doesn't quietly turn the assistant back on.
state = {"considering": None, "snoozed_until": 0, "paused": False}
STATE_PATH = LOG_PATH.parent / "p3_state.json"

def _load_state() -> None:
    try:
        state["paused"] = json.loads(STATE_PATH.read_text(encoding="utf-8"))["paused"] is True
    except (OSError, ValueError, KeyError, TypeError):
        pass

_load_state()

CONSIDER_STALE_MS = 30_000   # a judge that died mid-call shouldn't look busy forever

def _now_ms() -> int:
    return int(time.time() * 1000)

_seen = {}     # identity -> stored record, so re-sends are harmless
_rev_of = {}   # identity -> revision it last changed at
_rev = 0       # bumps on every new or upgraded record; pages poll for changes since
BOOT = uuid.uuid4().hex   # a restart means pages must reload everything

def _identity(record: dict):
    """What makes a record unique, by shape. None if it's neither type."""
    d = record.get("decision")
    if isinstance(d, dict) and d.get("decision_id"):
        return ("decision", d["decision_id"])
    if "kind" in record and "decision_id" in record:   # Signal/Event also have "kind"
        return ("feedback", record["decision_id"], record["kind"], record.get("ts"))
    return None

def _store(record: dict, identity) -> str:
    """Keep a record in memory. Returns "new", "upgraded" or "duplicate".
    The live judge sends plain DecisionRecords; its log file (via p3_import)
    has the same record plus a "judge" key with its evidence. Whichever
    arrives second, the stored record ends up with the evidence."""
    global _rev
    old = _seen.get(identity)
    if old is None:
        _seen[identity] = record
        (decisions if identity[0] == "decision" else feedback).append(record)
        outcome = "new"
    elif "judge" in record and "judge" not in old:
        old["judge"] = record["judge"]
        outcome = "upgraded"
    else:
        return "duplicate"
    _rev += 1
    _rev_of[identity] = _rev
    return outcome

def _load() -> None:
    if not LOG_PATH.exists():
        return
    with open(LOG_PATH, encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
                identity = _identity(record)
            except (ValueError, TypeError, AttributeError):
                continue   # a line torn by a crash mid-write; skip it
            if identity:
                _store(record, identity)
    # end a torn tail with a newline so the next append starts a clean line
    with open(LOG_PATH, "rb+") as fh:
        if fh.seek(0, os.SEEK_END) > 0:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")

_load()

@app.post("/log")
def log(record: dict):
    identity = _identity(record)
    if identity is None:
        return {"ok": False, "error": "not a DecisionRecord or FeedbackRecord"}
    with _lock:
        outcome = _store(record, identity)
        if outcome == "duplicate":
            return {"ok": True, "duplicate": True}
        # an upgrade is saved too, so a restart reloads the evidence
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    c = state["considering"]
    cand = record.get("candidate")
    if identity[0] == "decision" and c and isinstance(cand, dict) and cand.get("candidate_id") == c["candidate_id"]:
        state["considering"] = None
    return {"ok": True, "upgraded": outcome == "upgraded",
            "decisions": len(decisions), "feedback": len(feedback)}

@app.get("/records")
def records(since: int = 0, boot: str = ""):
    """Everything, or with ?since=<rev>&boot=<boot> only what changed after
    that revision. "full" tells the page to replace what it has, e.g. after
    a server restart or a first load."""
    with _lock:
        full = since <= 0 or boot != BOOT or since > _rev
        pick = (lambda rs: list(rs)) if full else \
               (lambda rs: [r for r in rs if _rev_of[_identity(r)] > since])
        return {"boot": BOOT, "rev": _rev, "full": full,
                "decisions": pick(decisions), "feedback": pick(feedback)}

# --- presence -------------------------------------------------------------

@app.post("/considering")
def considering(body: dict):
    """The judge is weighing a candidate right now. Cleared when its DecisionRecord arrives."""
    state["considering"] = {"candidate_id": body.get("candidate_id"), "since": _now_ms()}
    return {"ok": True}

@app.post("/snooze")
def snooze(body: dict):
    """Body: {"until": ms}. Send 0 to resume."""
    try:
        state["snoozed_until"] = max(0, int(body.get("until") or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "until must be a timestamp in ms"}
    return {"ok": True, **get_state()}

@app.post("/pause")
def pause(body: dict):
    """Body: {"paused": true|false}. Off until turned back on, unlike a snooze."""
    paused = body.get("paused")
    if not isinstance(paused, bool):
        return {"ok": False, "error": "paused must be true or false"}
    state["paused"] = paused
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"paused": paused}), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, **get_state()}

@app.get("/state")
def get_state():
    now = _now_ms()
    c = state["considering"]
    if c and now - c["since"] > CONSIDER_STALE_MS:
        state["considering"] = c = None
    return {"considering": c, "snoozed_until": state["snoozed_until"], "paused": state["paused"], "now": now}

# --- pages ----------------------------------------------------------------

@app.get("/")
def dashboard():
    return FileResponse(HERE / "dashboard.html")

@app.get("/surface")
def surface():
    return FileResponse(HERE / "surface.html")
