"""Score prompt versions against labeled traces; keep every result.

    python -m judge.calibrate judge-v1 judge-v2 ...    # score, append, rebuild table
    python -m judge.calibrate --table                  # just rebuild the table

Each version is replayed over every calibration trace with a fresh judge
(budget 3/hour, content generation off — content can't change timing).
Metrics, per contract.EvalResult:
  false_positive_rate  spoke where the label says leave them alone.
                       Headline number, restricted to PRODUCTIVE traces.
  hit_rate             labeled-speak moments where the judge spoke. A moment
                       held as too_soon right after a hit in the same episode
                       counts as covered: the developer already heard it.
  p95_latency_ms       candidate -> timing decision, model calls only.

Results append to calibration/results.jsonl; calibration/RESULTS.md is
regenerated from it, so the table always covers every version ever scored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from contract import EvalResult, read_jsonl, to_json
from judge import gemini
from judge.codec import label_from_dict
from judge.prompts import PROMPTS
from judge.replay import replay
from judge.synth import CALIBRATION, PRODUCTIVE, TRACE_DIR

CAL_DIR = Path(__file__).resolve().parent.parent / "calibration"
RESULTS = CAL_DIR / "results.jsonl"
TABLE = CAL_DIR / "RESULTS.md"


def p95(xs: list[int]) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return float(xs[min(len(xs) - 1, math.ceil(0.95 * len(xs)) - 1)])


def score(version: str, notes: str = "", repeats: int = 3) -> dict:
    """Replay every trace `repeats` times and pool the rows: even at
    temperature 0 single runs flip on borderline candidates."""
    rows = []
    for rep, name in [(r, n) for r in range(repeats) for n in CALIBRATION]:
        labels = [label_from_dict(d) for d in read_jsonl(str(TRACE_DIR / f"{name}.labels.jsonl"))]
        path, ds = replay(TRACE_DIR / f"{name}.candidates.jsonl", version, per_hour=3,
                          with_content=False, session_id=f"cal_{name}__{version}__r{rep}")
        lines = list(read_jsonl(str(path)))
        hit_eps: set[str] = set()
        for d, line in zip(ds, lines):
            ts = line["candidate"]["ts"]
            lab = next(l for l in labels if abs(l.ts - ts) <= l.tolerance_ms)
            ep = line["candidate"]["snapshot"]["episode"]["episode_id"]
            covered = (not d.should_speak and d.decline_reason.value == "too_soon"
                       and ep in hit_eps)
            if d.should_speak and lab.should_have_spoken:
                hit_eps.add(ep)
            j = line.get("judge", {})
            rows.append({
                "trace": name, "candidate": d.candidate_id, "label": lab.should_have_spoken,
                "spoke": d.should_speak, "covered": covered,
                "trajectory": d.trajectory.value, "decline": d.decline_reason.value,
                "latency_ms": j.get("judge_latency_ms"), "reasoning": d.reasoning,
            })

    neg = [r for r in rows if not r["label"]]
    neg_prod = [r for r in neg if r["trace"] in PRODUCTIVE]
    pos = [r for r in rows if r["label"]]
    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    spoke = sum(r["spoke"] for r in rows)
    span_h = sum(_span_ms(n) for n in CALIBRATION) / 3_600_000
    ev = EvalResult(
        session_id="calibration:" + "+".join(CALIBRATION),
        candidates=len(rows), spoke=spoke, declined=len(rows) - spoke,
        interventions_per_hour=spoke / (span_h * repeats) if span_h else 0.0,
        hit_rate=sum(r["spoke"] or r["covered"] for r in pos) / len(pos) if pos else 0.0,
        false_positive_rate=sum(r["spoke"] for r in neg_prod) / len(neg_prod) if neg_prod else 0.0,
        p95_latency_ms=p95(lat),
        prompt_version=version,
    )
    result = {
        "scored_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": gemini.DEFAULT_MODEL,
        "prompt_sha": hashlib.sha1(PROMPTS[version].encode()).hexdigest()[:8],
        "prompt_chars": len(PROMPTS[version]),
        "trace_sha": trace_sha(),
        "repeats": repeats,
        "eval": json.loads(to_json(ev)),
        "fp_all_negatives": sum(r["spoke"] for r in neg) / len(neg) if neg else 0.0,
        "p50_latency_ms": sorted(lat)[len(lat) // 2] if lat else 0,
        "rows": rows,
        "notes": notes,
    }
    CAL_DIR.mkdir(exist_ok=True)
    with open(RESULTS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result) + "\n")
    return result


def trace_sha() -> str:
    """Identity of the benchmark. Rows are only comparable within one sha."""
    h = hashlib.sha1()
    for name in CALIBRATION:
        for kind in ("candidates", "labels"):
            h.update((TRACE_DIR / f"{name}.{kind}.jsonl").read_bytes())
    return h.hexdigest()[:8]


def _span_ms(name: str) -> int:
    ts = [d["ts"] for d in read_jsonl(str(TRACE_DIR / f"{name}.candidates.jsonl"))]
    return max(ts) - min(ts) if ts else 0


def build_table() -> str:
    results = list(read_jsonl(str(RESULTS))) if RESULTS.exists() else []
    out = [
        "# Judge calibration results",
        "",
        "Generated by `python -m judge.calibrate`. One row per scoring run; never edited by hand.",
        "",
        f"Traces: {', '.join(CALIBRATION)} (synthetic placeholders — see judge/synth.py). "
        f"Productive: {', '.join(sorted(PRODUCTIVE))}.",
        "",
        "`traces` is a hash of the benchmark; compare rows only within one hash.",
        "",
        "| scored | prompt | sha | traces | runs | FP (productive) | FP (all neg) | hit rate "
        "| spoke/cands | p50 ms | p95 ms | notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        e = r["eval"]
        out.append(
            f"| {r['scored_at']} | {e['prompt_version']} | {r['prompt_sha']} "
            f"| {r.get('trace_sha', 'pre-fix')} | {r.get('repeats', 1)} "
            f"| {e['false_positive_rate']:.0%} | {r['fp_all_negatives']:.0%} | {e['hit_rate']:.0%} "
            f"| {e['spoke']}/{e['candidates']} | {r['p50_latency_ms']} | {e['p95_latency_ms']:.0f} "
            f"| {r['notes']} |"
        )
    if results:
        last = results[-1]
        out += ["", f"## Misses and false positives, latest run ({last['eval']['prompt_version']})", ""]
        for row in last["rows"]:
            if row["spoke"] != row["label"] and not row["covered"]:
                kind = "FALSE POSITIVE" if row["spoke"] else "MISS"
                out.append(f"- **{kind}** `{row['candidate']}` ({row['trajectory']}, "
                           f"{row['decline']}): {row['reasoning']}")
    text = "\n".join(out) + "\n"
    TABLE.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*")
    ap.add_argument("--notes", default="")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--table", action="store_true")
    a = ap.parse_args()
    for v in a.versions:
        r = score(v, a.notes, a.repeats)
        print(EvalResult(**r["eval"]).table(), "\n")
        for row in r["rows"]:
            flag = "" if row["spoke"] == row["label"] or row["covered"] else \
                ("  <-- FP" if row["spoke"] else "  <-- MISS")
            print(f"  {row['trace']:<10} label={'speak' if row['label'] else 'quiet':<5} "
                  f"spoke={str(row['spoke']):<5} {row['trajectory']:<11} {row['decline']:<22}"
                  f"{row['latency_ms'] or '-':>6}ms{flag}")
        print()
    print(build_table())
