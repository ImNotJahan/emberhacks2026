"""Latency report: candidate -> decision, with content generation ON.

    python -m judge.latency [--prompt judge-v5] [--repeats 2]

Replays the calibration traces and writes calibration/LATENCY.md. Three
numbers, because timing and content are separate calls:
  decision   candidate in -> should_speak known (render + judge call)
  content    the content call alone (only on should_speak=True)
  end-to-end candidate in -> InterventionDecision ready, incl. content
Gated candidates (budget / cooldown) never call the model and are excluded;
they return in well under 1 ms.
"""

from __future__ import annotations

import argparse
import time

from contract import read_jsonl
from judge import gemini
from judge.calibrate import CAL_DIR, p95
from judge.prompts import LATEST, PROMPTS
from judge.render import render_candidate
from judge.codec import candidate_from_dict
from judge.replay import replay
from judge.synth import CALIBRATION, TRACE_DIR

TARGET_MS = 3000


def pct(xs: list[int]) -> str:
    if not xs:
        return "n/a"
    s = sorted(xs)
    return f"p50 {s[len(s) // 2]} ms · p95 {p95(s):.0f} ms · max {s[-1]} ms · n={len(s)}"


def main(prompt: str, repeats: int) -> str:
    decision, content, e2e = [], [], []
    for rep in range(repeats):
        for name in CALIBRATION:
            path, _ = replay(TRACE_DIR / f"{name}.candidates.jsonl", prompt,
                             session_id=f"lat_{name}__{prompt}__r{rep}")
            for line in read_jsonl(str(path)):
                j = line.get("judge", {})
                if "judge_latency_ms" not in j:
                    continue   # gated, no model call
                decision.append(j["judge_latency_ms"])
                e2e.append(j["total_latency_ms"])
                if "content_latency_ms" in j:
                    content.append(j["content_latency_ms"])

    sizes = [len(render_candidate(candidate_from_dict(d)))
             for n in CALIBRATION for d in read_jsonl(str(TRACE_DIR / f"{n}.candidates.jsonl"))]
    ok = p95(decision) < TARGET_MS
    report = "\n".join([
        "# Judge latency",
        "",
        f"Measured {time.strftime('%Y-%m-%d %H:%M')} · model `{gemini.DEFAULT_MODEL}` "
        f"(thinking off) · prompt `{prompt}` · {repeats}x over {', '.join(CALIBRATION)}.",
        "",
        f"- **candidate → decision: {pct(decision)}** "
        f"({'under' if ok else 'OVER'} the {TARGET_MS} ms target)",
        f"- content generation (only when speaking): {pct(content)}",
        f"- end-to-end incl. content: {pct(e2e)}",
        f"- context: system prompt {len(PROMPTS[prompt])} chars; candidate render "
        f"{min(sizes)}–{max(sizes)} chars (events capped at 40, text excerpts at 160)",
        "",
        f"Pitch line: *the judge decides in {p95(decision) / 1000:.1f}s at p95; when it does "
        f"speak, the message is ready in {p95(e2e) / 1000:.1f}s at p95.*",
        "",
    ])
    CAL_DIR.mkdir(exist_ok=True)
    (CAL_DIR / "LATENCY.md").write_text(report, encoding="utf-8")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=LATEST)
    ap.add_argument("--repeats", type=int, default=2)
    a = ap.parse_args()
    print(main(a.prompt, a.repeats))
