"""Exponential-bin latency histogram with bounded memory."""

from __future__ import annotations

import math

# Base 1.5, bin 0 = 1ms. Bin k covers [1.5^k, 1.5^(k+1)) ms.
# 40 bins reach ~11_000_000 ms (~3 hours) — far beyond any realistic request.
BASE = 1.5
_LOG_BASE = math.log(BASE)
BINS = 40


def _bucket(ms: float) -> int:
    if ms <= 1.0:
        return 0
    b = int(math.log(ms) / _LOG_BASE)
    if b < 0:
        return 0
    if b >= BINS:
        return BINS - 1
    return b


class Histogram:
    __slots__ = ("buckets", "count", "total", "max_ms")

    def __init__(self) -> None:
        self.buckets = [0] * BINS
        self.count = 0
        self.total = 0.0
        self.max_ms = 0.0

    def add(self, ms: float) -> None:
        self.count += 1
        self.total += ms
        if ms > self.max_ms:
            self.max_ms = ms
        self.buckets[_bucket(ms)] += 1

    def merge(self, other: "Histogram") -> None:
        for i in range(BINS):
            self.buckets[i] += other.buckets[i]
        self.count += other.count
        self.total += other.total
        if other.max_ms > self.max_ms:
            self.max_ms = other.max_ms

    def mean(self) -> float:
        return self.total / self.count if self.count else 0.0

    def percentile(self, p: float) -> float:
        if self.count == 0:
            return 0.0
        target = self.count * p
        cum = 0
        for i, c in enumerate(self.buckets):
            cum += c
            if cum >= target:
                # return bin midpoint (geometric mean of bin bounds)
                if i == 0:
                    return 0.5  # <1ms
                lo = BASE ** i
                hi = BASE ** (i + 1)
                return math.sqrt(lo * hi)
        return self.max_ms

    def p50(self) -> float:
        return self.percentile(0.50)

    def p95(self) -> float:
        return self.percentile(0.95)

    def p99(self) -> float:
        return self.percentile(0.99)
