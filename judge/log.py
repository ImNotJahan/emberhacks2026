"""Decision log: one JSONL line per candidate, spoken or not.

Each line is a contract DecisionRecord (what Person 3 renders and Person 4
scores) plus one additive key, "judge", holding the judge's private working:
stage-one trajectory evidence, the raw model verdict, and any override the
budget applied. Readers that only know DecisionRecord can ignore it.

    python -m judge.log logs/<file>.jsonl    # human-readable view
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from contract import CandidateMoment, DecisionRecord, InterventionDecision, _Encoder, read_jsonl

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class DecisionLog:
    def __init__(self, session_id: str, path: Optional[Path] = None) -> None:
        self.session_id = session_id
        self.path = Path(path) if path else LOG_DIR / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()          # an empty log is a valid result: no candidates

    def append(self, candidate: CandidateMoment, decision: InterventionDecision,
               extra: Optional[dict[str, Any]] = None) -> DecisionRecord:
        rec = DecisionRecord.create(self.session_id, candidate, decision)
        line = asdict(rec)
        if extra:
            line["judge"] = extra
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, cls=_Encoder, separators=(",", ":")) + "\n")
        return rec


def summarize(line: dict) -> dict:
    """The fields a human reads first."""
    c, d = line["candidate"], line["decision"]
    return {
        "timestamp": d["ts"],
        "episode_id": c["snapshot"]["episode"]["episode_id"],
        "signals": [s["kind"] for s in c["signals"]],
        "reasoning": d["reasoning"],
        "decision": "SPEAK" if d["should_speak"] else f"silent ({d['decline_reason']})",
        "confidence": d["confidence"],
    }


def pretty(path: str) -> str:
    out = []
    for line in read_jsonl(path):
        s = summarize(line)
        d, j = line["decision"], line.get("judge", {})
        t = time.strftime("%H:%M:%S", time.localtime(s["timestamp"] / 1000))
        out.append(
            f"{t}  {s['episode_id']:<16} {s['decision']:<32} conf={s['confidence']:.2f} "
            f"traj={d['trajectory']:<10} budget={line['candidate']['budget']['remaining']} "
            f"{d.get('latency_ms') or '-'}ms\n"
            f"          signals: {', '.join(s['signals'])}  cited: {', '.join(d['signals_cited'])}\n"
            f"          why: {s['reasoning']}"
            + (f"\n          evidence: {j['trajectory_evidence']}" if j.get("trajectory_evidence") else "")
            + (f"\n          override: {j['override']}" if j.get("override") else "")
            + (f"\n          SAYS: {d['content']}" if d.get("content") else "")
        )
    return "\n\n".join(out)


if __name__ == "__main__":
    print(pretty(sys.argv[1]))
