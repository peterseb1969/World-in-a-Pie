"""Per-stage timing statistics in bounded memory.

The validation and creation timers recorded one float per stage per call
and kept them for the life of the process: about 300 MB per million
documents per worker. Each stage now keeps running aggregates (count, total,
min, max) over every call, and the last PERCENTILE_WINDOW samples for the
percentiles, so memory is fixed however long the pod runs.
"""

from collections import deque
from typing import Any

# How many recent samples per stage the percentiles are computed over.
PERCENTILE_WINDOW = 1000


class _Stage:
    __slots__ = ("count", "maximum", "minimum", "recent", "total")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.minimum = float("inf")
        self.maximum = float("-inf")
        self.recent: deque[float] = deque(maxlen=PERCENTILE_WINDOW)

    def add(self, ms: float) -> None:
        self.count += 1
        self.total += ms
        self.minimum = min(self.minimum, ms)
        self.maximum = max(self.maximum, ms)
        self.recent.append(ms)

    def summary(self) -> dict[str, Any]:
        window = sorted(self.recent)
        n = len(window)
        return {
            "count": self.count,
            "avg_ms": self.total / self.count,
            "min_ms": self.minimum,
            "max_ms": self.maximum,
            "p50_ms": window[n // 2],
            "p95_ms": window[int(n * 0.95)] if n >= 20 else window[-1],
            "p99_ms": window[int(n * 0.99)] if n >= 100 else window[-1],
            "percentile_window": n,
        }


class TimingStats:
    """Timing per named stage. ``count`` counts every recorded run."""

    def __init__(self) -> None:
        self.runs = 0
        self._stages: dict[str, _Stage] = {}

    def record(self, timing: dict[str, float]) -> None:
        self.runs += 1
        for stage, ms in timing.items():
            self._stages.setdefault(stage, _Stage()).add(ms)

    def stages(self) -> dict[str, dict[str, Any]]:
        return {name: stage.summary() for name, stage in self._stages.items() if stage.count}
