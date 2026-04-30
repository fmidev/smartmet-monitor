"""URLs snapshot — per-URL latency / size / error stats."""

from __future__ import annotations

import time
from typing import List, Tuple

from ..state.store import MinuteBucket

WINDOWS: Tuple[int, ...] = (1, 5, 15, 60)

# Valid sort keys, paralleling the SORT_COLS table in panels/urls.py.
SORT_KEYS = (
    "count", "p95", "p50", "mean_ms", "max_ms",
    "avg_kb", "mb_tot", "err_pct", "url_asc",
)


def collect(store, *, window_min: int = 5, sort: str = "p95",
            reverse: bool = True, filter_str: str = ""
            ) -> List[Tuple[str, MinuteBucket]]:
    """Filter, sort, return URL rows for one window. Pure."""
    urls = store.snapshot_urls(window_min)
    if filter_str:
        f = filter_str.lower()
        urls = [(u, b) for (u, b) in urls if f in u.lower()]

    def keyfn(item):
        url, b = item
        if sort == "count":
            return b.count
        if sort == "p95":
            return b.hist.p95()
        if sort == "p50":
            return b.hist.p50()
        if sort == "mean_ms":
            return b.hist.mean()
        if sort == "max_ms":
            return b.hist.max_ms
        if sort == "avg_kb":
            return (b.bytes / b.count / 1024) if b.count else 0
        if sort == "mb_tot":
            return b.bytes
        if sort == "err_pct":
            return (b.errors / b.count * 100) if b.count else 0
        if sort == "url_asc":
            return url
        return 0

    rev = reverse if sort != "url_asc" else False
    urls.sort(key=keyfn, reverse=rev)
    return urls


def collect_with_autowiden(store, *, window_min: int = 5, sort: str = "p95",
                           reverse: bool = True, filter_str: str = ""
                           ) -> Tuple[List[Tuple[str, MinuteBucket]], int]:
    """As collect(), but if the chosen window is empty, fall through to
    the next wider window with data. Returns (rows, effective_window_min)
    so the caller can surface the auto-widen in its header.
    """
    rows = collect(store, window_min=window_min, sort=sort,
                   reverse=reverse, filter_str=filter_str)
    if rows or window_min not in WINDOWS:
        return rows, window_min
    idx = WINDOWS.index(window_min)
    for try_min in WINDOWS[idx + 1:]:
        widened = collect(store, window_min=try_min, sort=sort,
                          reverse=reverse, filter_str=filter_str)
        if widened:
            return widened, try_min
    return [], window_min


class URLsSnapshot:
    name = "urls"

    @staticmethod
    def table(store, *, window_min: int = 5, sort: str = "p95",
              reverse: bool = True, filter_str: str = ""):
        rows = collect(store, window_min=window_min, sort=sort,
                       reverse=reverse, filter_str=filter_str)
        headers = ["url", "window_min", "count", "mean_ms", "p50_ms",
                   "p95_ms", "p99_ms", "max_ms", "avg_bytes",
                   "total_bytes", "errors", "err_pct"]
        out = []
        for url, b in rows:
            out.append([
                url,
                window_min,
                b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.p99(), 3),
                round(b.hist.max_ms, 3),
                int(b.bytes / b.count) if b.count else 0,
                b.bytes,
                b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, out

    @staticmethod
    def detail(store, url: str, *, window_min: int = 5) -> dict:
        """Drill-down view for one URL: per-window stats, latency
        histogram, status-code mix, top API keys, last-60-min mean
        latency series. The web URLs panel hits this when an operator
        clicks a row.
        """
        u = store.url_detail(url)
        if u is None:
            return {"url": url, "found": False}

        out = {"url": url, "found": True, "windows": []}
        for m in WINDOWS:
            b = u.window(m)
            if b.count == 0:
                continue
            out["windows"].append({
                "window_min": m,
                "count": b.count,
                "mean_ms": round(b.hist.mean(), 3),
                "p50_ms": round(b.hist.p50(), 3),
                "p95_ms": round(b.hist.p95(), 3),
                "p99_ms": round(b.hist.p99(), 3),
                "max_ms": round(b.hist.max_ms, 3),
                "avg_bytes": int(b.bytes / b.count) if b.count else 0,
                "total_bytes": b.bytes,
                "errors": b.errors,
                "err_pct": round(b.errors / b.count * 100, 3)
                            if b.count else 0,
            })

        now_min = int(time.time() // 60)
        out["minute_series"] = {
            "metric": "mean_ms",
            "minutes": 60,
            "values": list(u.minute_series(60, "mean_ms")),
            "last_ts": float(now_min * 60),
            "step_seconds": 60.0,
        }

        bw = u.window(window_min)
        out["histogram"] = {
            "window_min": window_min,
            "buckets": [
                {"lo_ms": (1.5 ** j) if j > 0 else 0.0,
                 "hi_ms": 1.5 ** (j + 1),
                 "count": int(c)}
                for j, c in enumerate(bw.hist.buckets) if c > 0
            ],
        }

        if bw.count > 0:
            items = sorted(bw.status_counts.items(), key=lambda x: -x[1])
            out["status_codes"] = [
                {"status": int(s), "count": int(c),
                 "pct": round(c / bw.count * 100, 2)}
                for s, c in items
            ]
        else:
            out["status_codes"] = []

        items = sorted(u.apikey_counts.items(), key=lambda x: -x[1])[:10]
        out["apikeys"] = [{"key": k, "count": int(c)} for k, c in items]
        return out

    @staticmethod
    def chart(store, url: str, *, window_min: int = 60,
              metric: str = "mean_ms") -> dict:
        """Per-minute time series for a single URL.

        ``metric`` is forwarded to ``UrlStats.minute_series()``; common
        choices are ``mean_ms`` and ``p95_ms``.
        """
        u = store.url_detail(url)
        if u is None:
            return {"url": url, "found": False, "values": []}
        now_min = int(time.time() // 60)
        return {
            "url": url,
            "found": True,
            "metric": metric,
            "minutes": window_min,
            "values": list(u.minute_series(window_min, metric)),
            "last_ts": float(now_min * 60),
            "step_seconds": 60.0,
        }
