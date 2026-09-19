import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

HERE = Path(__file__).parent

app = FastAPI()
decisions = []   # DecisionRecords from the judge
feedback = []    # FeedbackRecords from the UI

# Live presence state for the surface. Not part of contract.py on purpose:
# it's ephemeral UI state, never replayed or scored.
state = {"considering": None, "snoozed_until": 0}

def _now_ms() -> int:
    return int(time.time() * 1000)

@app.post("/log")
def log(record: dict):
    if "decision" in record:
        decisions.append(record)
        c = state["considering"]
        if c and c["candidate_id"] == record.get("candidate", {}).get("candidate_id"):
            state["considering"] = None
    elif "kind" in record and "decision_id" in record:   # Signal/Event also have "kind"
        feedback.append(record)
    else:
        return {"ok": False, "error": "not a DecisionRecord or FeedbackRecord"}
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
