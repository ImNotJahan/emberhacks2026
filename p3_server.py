import json
import os
import threading
import time
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
# it's ephemeral UI state, never replayed or scored, so it isn't saved either.
state = {"considering": None, "snoozed_until": 0}

def _now_ms() -> int:
    return int(time.time() * 1000)

def _store(record: dict) -> bool:
    """Sort a record into memory by shape. False if it's neither type."""
    if "decision" in record:
        decisions.append(record)
    elif "kind" in record and "decision_id" in record:   # Signal/Event also have "kind"
        feedback.append(record)
    else:
        return False
    return True

def _load() -> None:
    if not LOG_PATH.exists():
        return
    with open(LOG_PATH, encoding="utf-8") as fh:
        for line in fh:
            try:
                _store(json.loads(line))
            except (ValueError, TypeError):
                pass   # a line torn by a crash mid-write; skip it
    # end a torn tail with a newline so the next append starts a clean line
    with open(LOG_PATH, "rb+") as fh:
        if fh.seek(0, os.SEEK_END) > 0:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                fh.write(b"\n")

_load()

@app.post("/log")
def log(record: dict):
    with _lock:
        if not _store(record):
            return {"ok": False, "error": "not a DecisionRecord or FeedbackRecord"}
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    if "decision" in record:
        c = state["considering"]
        if c and c["candidate_id"] == record.get("candidate", {}).get("candidate_id"):
            state["considering"] = None
    return {"ok": True, "decisions": len(decisions), "feedback": len(feedback)}

@app.get("/records")
def records():
    return {"decisions": decisions, "feedback": feedback}

# --- presence -------------------------------------------------------------

@app.post("/considering")
def considering(body: dict):
    """The judge is weighing a candidate right now. Cleared when its DecisionRecord arrives."""
    state["considering"] = {"candidate_id": body.get("candidate_id"), "since": _now_ms()}
    return {"ok": True}

@app.post("/snooze")
def snooze(body: dict):
    """Body: {"until": ms}. Send 0 to resume."""
    state["snoozed_until"] = int(body.get("until") or 0)
    return {"ok": True, **state}

@app.get("/state")
def get_state():
    return {**state, "now": _now_ms()}

# --- pages ----------------------------------------------------------------

@app.get("/")
def dashboard():
    return FileResponse(HERE / "dashboard.html")

@app.get("/surface")
def surface():
    return FileResponse(HERE / "surface.html")
