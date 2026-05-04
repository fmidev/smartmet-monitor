"""Heap (allocator) snapshot — per-host jemalloc/mimalloc stats.

Drives smwebmon's Heap section in the Proc panel. The data is
already structured by the polling loop in
``smartmet_top.sources.adminapi.poll_admin``; this module just
reshapes the per-host MallocSample objects + their bounded history
into the JSON the browser expects.
"""

from __future__ import annotations

from typing import Dict, List


class HeapSnapshot:
    name = "heap"

    @staticmethod
    def table(store):
        # CSV/table export — one row per host, latest values only.
        # The historical sparkline is only useful through the JSON
        # endpoint; CSV consumers tend to want the snapshot.
        headers = ["host", "allocator", "version",
                   "allocated", "active", "metadata", "resident",
                   "mapped", "retained", "narenas",
                   "fragmentation_pct"]
        rows = []
        for host in sorted(store.mallocstats_latest):
            sample = store.mallocstats_latest.get(host)
            if sample is None:
                continue
            rows.append([
                host, sample.allocator, sample.version,
                sample.allocated, sample.active, sample.metadata,
                sample.resident, sample.mapped, sample.retained,
                sample.narenas,
                round(sample.fragmentation_pct, 2),
            ])
        return headers, rows

    @staticmethod
    def detail(store) -> Dict:
        """JSON shape for the Heap section.

        ``series`` only carries the three fields the dashboard
        actually plots, not the full sample (the front-end doesn't
        need metadata / mapped / retained on every sparkline tick).
        """
        out: List[Dict] = []
        for host in sorted(store.mallocstats_history):
            history = list(store.mallocstats_history[host])
            if not history:
                continue
            latest = history[-1]
            error = store.mallocstats_error.get(host, "")
            fetched_at = store.mallocstats_fetched_at.get(host, 0.0)
            out.append({
                "host": host,
                "allocator": latest.allocator,
                "version": latest.version,
                "fetched_at": fetched_at,
                "error": error,
                "latest": {
                    "allocated": latest.allocated,
                    "active": latest.active,
                    "metadata": latest.metadata,
                    "resident": latest.resident,
                    "mapped": latest.mapped,
                    "retained": latest.retained,
                    "narenas": latest.narenas,
                    "fragmentation_pct": round(
                        latest.fragmentation_pct, 2),
                    "resident_overhead_pct": round(
                        latest.resident_overhead_pct, 2),
                },
                "series": [
                    {"ts": s.ts, "allocated": s.allocated,
                     "active": s.active, "resident": s.resident}
                    for s in history
                ],
            })
        return {"hosts": out}
