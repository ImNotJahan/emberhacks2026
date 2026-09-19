"""Score capture + judge end to end against hand-labeled moments.

    python -m judge.eval                          # every session in traces/eval/manifest.json
    python -m judge.eval dogfood stuck_import     # just these
    python -m judge.eval --capture-only           # no model calls: capture recall only (free, offline)
    python -m judge.eval --fresh                  # re-run the judge even if a decision log exists
    python -m judge.eval --prompt judge-v7

Each session is traces/eval/<name>.raw.jsonl (raw editor observations) plus
<name>.labels.jsonl (LabeledMoments). The raw trace goes through the capture
engine exactly as live, the candidates through the judge (content off), and
the decisions are matched to the labels by time. Decision logs are cached in
logs/eval_<name>__<prompt>.jsonl and reused unless the candidates changed.

Unlike judge/calibrate.py, labels here are MOMENTS, not candidates: a label
with no candidate near it is a capture miss, not a crash.

Metrics (per session, then pooled):
  per hour     spoken decisions / trace span
  capture      true labels with a candidate within tolerance (Person 1)
  hit rate     true labels the judge spoke on: within tolerance, or covered by
               a nudge given less than one cooldown earlier (it already helped)
  infuriating  false labels the judge spoke within tolerance of
  FP           productive sessions: spoke / candidates (the headline number);
               other sessions: spoken decisions near no true label / spoken
  p95          candidate -> decision latency, model calls only
  lag          median seconds from a true label to the nudge that covered it

Writes calibration/EVAL.md and appends calibration/eval_results.jsonl.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from contract import EvalResult, LabeledMoment, read_jsonl
from judge.calibrate import p95
from judge.codec import label_from_dict
from judge.log import LOG_DIR
from judge.prompts import LATEST
from judge.replay import load_candidates, replay

EVAL_DIR = Path(__file__).resolve().parent.parent / "traces" / "eval"
CAL_DIR = Path(__file__).resolve().parent.parent / "calibration"
COOLDOWN_MS = 240_000          # judge.core default: a nudge covers this long after it


def manifest() -> dict:
    return json.loads((EVAL_DIR / "manifest.json").read_text())


def _raw_span_ms(raw: Path) -> int:
    ts = [d["ts"] for d in read_jsonl(str(raw)) if d.get("ts")]
    return max(ts) - min(ts) if ts else 0


def decisions_for(name: str, prompt: str, fresh: bool) -> list[dict]:
    """Decision-log lines for a session, replaying through the judge only when
    there is no cached log or the capture engine now nominates different moments."""
    raw = EVAL_DIR / f"{name}.raw.jsonl"
    session_id = f"eval_{name}__{prompt}"
    cached = LOG_DIR / f"{session_id}.jsonl"
    cand_ts = [c.ts for c in load_candidates(raw)]
    if not fresh and cached.exists():
        lines = list(read_jsonl(str(cached)))
        if [l["candidate"]["ts"] for l in lines] == cand_ts:
            return lines
        print(f"  {name}: candidates changed since the cached log; replaying")
    path, _ = replay(raw, prompt, None, with_content=False, session_id=session_id)
    return list(read_jsonl(str(path)))


def _near(ts: int, lab: LabeledMoment) -> bool:
    return abs(ts - lab.ts) <= lab.tolerance_ms


def score_session(name: str, meta: dict, prompt: str, capture_only: bool, fresh: bool) -> dict:
    raw = EVAL_DIR / f"{name}.raw.jsonl"
    labels = [label_from_dict(d) for d in read_jsonl(str(EVAL_DIR / f"{name}.labels.jsonl"))]
    t0 = min(d["ts"] for d in read_jsonl(str(raw)) if d.get("ts"))
    span_h = _raw_span_ms(raw) / 3_600_000
    pos = [l for l in labels if l.should_have_spoken]
    neg = [l for l in labels if not l.should_have_spoken]

    if capture_only:
        cands = [{"candidate": {"ts": c.ts, "signals": [{"kind": s.kind.value} for s in c.signals]},
                  "decision": None} for c in load_candidates(raw)]
    else:
        cands = decisions_for(name, prompt, fresh)
    cand_ts = [l["candidate"]["ts"] for l in cands]
    spoken = [l for l in cands if l["decision"] and l["decision"]["should_speak"]]
    spoke_ts = [l["candidate"]["ts"] for l in spoken]

    captured = [l for l in pos if any(_near(t, l) for t in cand_ts)]
    hits, lags, misses = [], [], []
    for lab in pos:
        cover = [t for t in spoke_ts if _near(t, lab) or 0 <= lab.ts - t < COOLDOWN_MS]
        if cover:
            hits.append(lab)
            lags.append((min(cover, key=lambda t: abs(t - lab.ts)) - lab.ts) / 1000)
        else:
            near = [l for l in cands if _near(l["candidate"]["ts"], lab)]
            why = ("capture: no candidate" if not near else
                   "judge: " + ", ".join(sorted({l["decision"]["decline_reason"] for l in near
                                                   if l["decision"]})) if not capture_only else "judge: not run")
            misses.append((lab, why))
    infuriating = [l for l in neg if any(_near(t, l) for t in spoke_ts)]
    false_alarms = [t for t in spoke_ts if not any(_near(t, l) for l in pos)]
    lat = [l["decision"]["latency_ms"] for l in cands
           if l["decision"] and l["decision"].get("latency_ms")]

    productive = meta.get("kind") == "productive"
    fp = (len(spoken) / len(cands) if productive else len(false_alarms) / len(spoken)) if (
        cands if productive else spoken) else 0.0
    res = EvalResult(session_id=name, candidates=len(cands), spoke=len(spoken),
                     declined=len(cands) - len(spoken),
                     interventions_per_hour=len(spoken) / span_h if span_h else 0.0,
                     hit_rate=len(hits) / len(pos) if pos else float("nan"),
                     false_positive_rate=fp, p95_latency_ms=p95(lat),
                     prompt_version=None if capture_only else prompt)
    fmt = lambda t: f"{(t - t0) // 60000:02d}:{(t - t0) // 1000 % 60:02d}"
    return {
        "result": res, "meta": meta, "span_min": span_h * 60, "pos": len(pos), "neg": len(neg),
        "captured": len(captured), "hits": len(hits), "infuriating": len(infuriating),
        "false_alarms": len(false_alarms), "lat": lat, "lags": lags,
        "misses": [(fmt(l.ts), why, l.rationale) for l, why in misses],
        "bad": [(fmt(l.ts), l.rationale) for l in infuriating],
        "alarms": [fmt(t) for t in false_alarms],
    }


def _pct(n: int, d: int) -> str:
    return f"{n / d:.0%} ({n}/{d})" if d else "-"


def table(rows: dict[str, dict], capture_only: bool) -> str:
    head = ("| session | kind | src | min | cands | spoke | /hour | capture | hit rate | infuriating "
            "| FP | p95 ms | lag s |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
    out = []
    dash = lambda v: "-" if capture_only else v
    for name, r in rows.items():
        res: EvalResult = r["result"]
        m = r["meta"]
        prod = m.get("kind") == "productive"
        fp = _pct(res.spoke, res.candidates) if prod else _pct(r["false_alarms"], res.spoke)
        p95s = f"{res.p95_latency_ms:.0f}" if r["lat"] else "-"
        lag = f"{statistics.median(r['lags']):+.0f}" if r["lags"] else "-"
        out.append(" | ".join([
            f"| {name}", m.get("kind", "?"), m.get("source", "?"), f"{r['span_min']:.0f}",
            str(res.candidates), dash(str(res.spoke)), dash(f"{res.interventions_per_hour:.1f}"),
            _pct(r["captured"], r["pos"]), dash(_pct(r["hits"], r["pos"])),
            dash(_pct(r["infuriating"], r["neg"])), dash(fp), p95s, lag + " |"]))
    # pooled
    R = list(rows.values())
    s = lambda k: sum(r[k] for r in R)
    prod_rows = [r for r in R if r["meta"].get("kind") == "productive"]
    spoke = sum(r["result"].spoke for r in R)
    hours = sum(r["span_min"] for r in R) / 60
    lat = [x for r in R for x in r["lat"]]
    lags = [x for r in R for x in r["lags"]]
    prod_fp = _pct(sum(r["result"].spoke for r in prod_rows), sum(r["result"].candidates for r in prod_rows))
    out.append(" | ".join([
        "| **all**", "", "", f"{hours * 60:.0f}", str(sum(r["result"].candidates for r in R)),
        dash(str(spoke)), dash(f"{spoke / hours:.1f}" if hours else "-"),
        _pct(s("captured"), s("pos")), dash(_pct(s("hits"), s("pos"))),
        dash(_pct(s("infuriating"), s("neg"))), dash("productive " + prod_fp),
        f"{p95(lat):.0f}" if lat else "-",
        (f"{statistics.median(lags):+.0f}" if lags else "-") + " |"]))
    return head + "\n".join(out)


def details(rows: dict[str, dict]) -> str:
    out = []
    for name, r in rows.items():
        items = ([f"- MISSED {t} ({why}): {why_l}" for t, why, why_l in r["misses"]] +
                 [f"- SPOKE AT A LEAVE-ALONE MOMENT {t}: {why}" for t, why in r["bad"]] +
                 [f"- SPOKE, NO TRUE LABEL NEARBY {t}" for t in r["alarms"]])
        if items:
            out.append(f"**{name}**\n" + "\n".join(items))
    return "\n\n".join(out) or "_none_"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*")
    ap.add_argument("--prompt", default=LATEST)
    ap.add_argument("--capture-only", action="store_true", help="no model calls; capture recall only")
    ap.add_argument("--fresh", action="store_true", help="re-run the judge even if a log exists")
    ap.add_argument("--no-write", action="store_true", help="print only; don't touch calibration/")
    a = ap.parse_args()

    man = manifest()
    names = a.sessions or list(man)
    rows = {}
    for n in names:
        print(f"scoring {n} ...", flush=True)
        rows[n] = score_session(n, man.get(n, {}), a.prompt, a.capture_only, a.fresh)

    mode = "capture only (no judge)" if a.capture_only else f"prompt {a.prompt}, content off"
    report = (f"# End-to-end evaluation\n\nGenerated by `python -m judge.eval` on "
              f"{time.strftime('%Y-%m-%d %H:%M')} · {mode}.\n\n"
              f"`src`: **real** = recorded editor session, **scripted** = realistic raw trace from "
              f"judge/scenarios.py (not real-world accuracy). Labels are hand-placed moments; see "
              f"traces/eval/README.md.\n\n{table(rows, a.capture_only)}\n\n"
              f"## Misses and bad moments\n\n{details(rows)}\n")
    print("\n" + report)
    if a.no_write:
        return
    CAL_DIR.mkdir(exist_ok=True)
    (CAL_DIR / ("EVAL_CAPTURE.md" if a.capture_only else "EVAL.md")).write_text(report)
    if not a.capture_only:
        with open(CAL_DIR / "eval_results.jsonl", "a", encoding="utf-8") as fh:
            for n, r in rows.items():
                fh.write(json.dumps({"scored": time.strftime("%Y-%m-%d %H:%M:%S"), **asdict(r["result"]),
                                     "captured": r["captured"], "pos": r["pos"], "neg": r["neg"],
                                     "infuriating": r["infuriating"]}) + "\n")


if __name__ == "__main__":
    main()
