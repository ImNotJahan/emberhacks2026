"""Interruption budget: a token bucket, N per hour, refilling continuously.

per_hour=None (or 0) means NO budget: the bucket only keeps count, never
refuses, and reports BudgetState(per_hour=0, remaining=0). The live judge runs
this way since v8 (each moment judged on its merits); calibration of v1-v7
still uses 3/hour so their rows stay comparable.

Time is always passed in (candidate.ts), never read from the wall clock, so a
replayed trace spends and refills exactly as it did live.
"""

from __future__ import annotations

import math
from typing import Optional

from contract import BudgetState

HOUR_MS = 3_600_000


class TokenBucket:
    def __init__(self, per_hour: Optional[int] = 3, start_full: bool = True) -> None:
        self.unlimited = not per_hour
        per_hour = per_hour or 0
        self.per_hour = per_hour
        self.tokens = float(per_hour) if start_full else 0.0
        self._last_ms: Optional[int] = None
        self.last_intervention_ms: Optional[int] = None
        self.spent = 0

    def _refill(self, ts: int) -> None:
        if self._last_ms is not None and ts > self._last_ms:
            self.tokens = min(float(self.per_hour),
                              self.tokens + (ts - self._last_ms) * self.per_hour / HOUR_MS)
        if self._last_ms is None or ts > self._last_ms:
            self._last_ms = ts

    def remaining(self, ts: int) -> int:
        if self.unlimited:
            return 0          # "no budget", not "exhausted": check .unlimited
        self._refill(ts)
        return math.floor(self.tokens + 1e-9)

    def state(self, ts: int) -> BudgetState:
        """The contract view the judge sees."""
        return BudgetState(
            per_hour=self.per_hour,
            remaining=self.remaining(ts),
            last_intervention_ms=self.last_intervention_ms,
            interventions_this_session=self.spent,
        )

    def try_spend(self, ts: int) -> bool:
        if self.unlimited:
            self.spent += 1
            self.last_intervention_ms = ts
            return True
        if self.remaining(ts) < 1:
            return False
        self.tokens -= 1.0
        self.spent += 1
        self.last_intervention_ms = ts
        return True
