"""Overview snapshot — global request stats over standard windows."""

from __future__ import annotations

import time

from ..state import store as _store

WINDOWS = (1, 5, 15, 60)
CHART_METRICS = ("count", "mean_ms", "p95_ms", "bytes", "err_pct")


class OverviewSnapshot:
    name = "overview"

    @staticmethod
    def table(store):
        headers = ["window_min", "reqs", "mean_ms", "p50_ms", "p95_ms",
                   "max_ms", "total_bytes", "errors", "err_pct"]
        rows = []
        for m in WINDOWS:
            b = store.global_window(m)
            rows.append([
                m, b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.max_ms, 3),
                b.bytes, b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, rows

    @staticmethod
    def chart(store, *, metric: str = "mean_ms", minutes: int = 0) -> dict:
        """Per-minute global series for the requested metric.

        ``metric`` is one of ``CHART_METRICS``. ``minutes=0`` falls back
        to the configured retention window (``HISTORY_MINUTES``).
        """
        if metric not in CHART_METRICS:
            metric = "mean_ms"
        if minutes <= 0:
            minutes = _store.HISTORY_MINUTES
        # Per-minute series, last-bucket-first oldest-leftmost. last_ts
        # is the start-of-current-minute Unix epoch; the client uses
        # (last_ts - (n-1-i)*step) to derive each point's timestamp
        # for the hover tooltip.
        now_min = int(time.time() // 60)
        return {
            "metric": metric,
            "minutes": minutes,
            "values": list(store.global_series(minutes, metric)),
            "last_ts": float(now_min * 60),
            "step_seconds": 60.0,
        }
