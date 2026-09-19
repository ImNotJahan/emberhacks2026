"""Interruption budget: a token bucket, N per hour, refilling continuously.

Time is always passed in (candidate.ts), never read from the wall clock, so a
replayed trace spends and refills exactly as it did live.
"""

from __future__ import annotations

import math
from typing import Optional

from contract import BudgetState

HOUR_MS = 3_600_000


class TokenBucket:
    def __init__(self, per_hour: int = 3, start_full: bool = True) -> None:
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
        if self.remaining(ts) < 1:
            return False
        self.tokens -= 1.0
        self.spent += 1
        self.last_intervention_ms = ts
        return True
