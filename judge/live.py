"""Live judge: poll the capture engine, decide, log, report budget back.

    python -m judge.live [--port 8765] [--session NAME] [--prompt judge-v5]

Talks to extention.py's local endpoint (Person 1):
  GET  /candidates?since=<ts>   new CandidateMoments
  POST /budget                  the judge's bucket state, so the engine's next
                                candidates carry the real budget
Every decision goes to logs/<session>.jsonl (Person 3 renders it) and to
stdout. Standard library only, besides the judge itself.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

from contract import new_id, now_ms, to_json
from judge.codec import candidate_from_dict
from judge.core import Judge
from judge.prompts import LATEST


def _get(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read() or b"[]")


def _post(url: str, body: str) -> None:
    req = urllib.request.Request(url, data=body.encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def run(port: int, session_id: str, prompt: str, per_hour: int, poll_s: float) -> None:
    base = f"http://127.0.0.1:{port}"
    judge = Judge(session_id, prompt, per_hour)
    since = now_ms()          # only judge what happens from now on
    print(f"judge live: {base}  prompt={prompt}  log={judge.log.path}", flush=True)
    while True:
        try:
            batch = _get(f"{base}/candidates?since={since}")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            print(f"engine unreachable ({e}); retrying", flush=True)
            time.sleep(max(poll_s, 2.0))
            continue
        for d in batch:
            since = max(since, d["ts"])
            decision = judge.decide(candidate_from_dict(d))
            verdict = f"SPEAK: {decision.content}" if decision.should_speak else \
                f"silent ({decision.decline_reason.value})"
            print(f"[{decision.latency_ms}ms] {verdict}\n    why: {decision.reasoning}", flush=True)
            try:
                _post(f"{base}/budget", to_json(judge.bucket.state(d["ts"])))
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
        time.sleep(poll_s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--session", default=None)
    ap.add_argument("--prompt", default=LATEST)
    ap.add_argument("--per-hour", type=int, default=3)
    ap.add_argument("--poll", type=float, default=1.0)
    a = ap.parse_args()
    run(a.port, a.session or new_id("sess"), a.prompt, a.per_hour, a.poll)
