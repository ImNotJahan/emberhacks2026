# Judge latency

Measured 2026-09-19 11:35 · model `gemini-2.5-flash` (thinking off) · prompt `judge-v5` · 2x over refactor, tdd, exploring, parser, thrash, blocked, p1_stuck.

- **candidate → decision: p50 1472 ms · p95 1819 ms · max 2067 ms · n=34** (under the 3000 ms target)
- content generation (only when speaking): p50 696 ms · p95 801 ms · max 801 ms · n=6
- end-to-end incl. content: p50 1473 ms · p95 2412 ms · max 2412 ms · n=34
- context: system prompt 5835 chars; candidate render 943–2458 chars (events capped at 40, text excerpts at 160)

Pitch line: *the judge decides in 1.8s at p95; when it does speak, the message is ready in 2.4s at p95.*
