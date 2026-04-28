"""Overview snapshot — global request stats over standard windows."""

from __future__ import annotations

WINDOWS = (1, 5, 15, 60)


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
