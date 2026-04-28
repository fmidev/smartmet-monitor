"""API-keys snapshot — per-apikey aggregate stats."""

from __future__ import annotations

from typing import List, Tuple

from ..state.store import MinuteBucket

WINDOWS: Tuple[int, ...] = (1, 5, 15, 60)

SORT_KEYS = ("count", "p95", "mean_ms", "mb_tot", "err_pct", "key_asc")


def collect(store, *, window_min: int = 60, sort: str = "count",
            reverse: bool = True, filter_str: str = ""
            ) -> List[Tuple[str, MinuteBucket]]:
    rows = store.snapshot_keys(window_min)
    if filter_str:
        f = filter_str.lower()
        rows = [(k, b) for (k, b) in rows if f in k.lower()]

    def keyfn(item):
        k, b = item
        if sort == "count":
            return b.count
        if sort == "p95":
            return b.hist.p95()
        if sort == "mean_ms":
            return b.hist.mean()
        if sort == "mb_tot":
            return b.bytes
        if sort == "err_pct":
            return (b.errors / b.count * 100) if b.count else 0
        if sort == "key_asc":
            return k
        return 0

    rev = reverse if sort != "key_asc" else False
    rows.sort(key=keyfn, reverse=rev)
    return rows


class KeysSnapshot:
    name = "keys"

    @staticmethod
    def table(store, *, window_min: int = 60, sort: str = "count",
              reverse: bool = True, filter_str: str = ""):
        rows = collect(store, window_min=window_min, sort=sort,
                       reverse=reverse, filter_str=filter_str)
        headers = ["apikey", "window_min", "count", "mean_ms", "p50_ms",
                   "p95_ms", "max_ms", "total_bytes", "errors", "err_pct"]
        out = []
        for k, b in rows:
            out.append([
                k, window_min, b.count,
                round(b.hist.mean(), 3),
                round(b.hist.p50(), 3),
                round(b.hist.p95(), 3),
                round(b.hist.max_ms, 3),
                b.bytes, b.errors,
                round(b.errors / b.count * 100, 3) if b.count else 0,
            ])
        return headers, out

    @staticmethod
    def detail(store, apikey: str, *, window_min: int = 60,
               top_urls: int = 50) -> dict:
        """Per-key drill-down: per-window stats + the top URLs this key
        has hit. Mirrors the curses Keys-panel detail view.
        """
        ks = store.key_detail(apikey)
        if ks is None:
            return {"apikey": apikey, "found": False}

        windows = []
        for m in WINDOWS:
            b = ks.window(m)
            if b.count == 0:
                continue
            windows.append({
                "window_min": m,
                "count": b.count,
                "mean_ms": round(b.hist.mean(), 3),
                "p50_ms": round(b.hist.p50(), 3),
                "p95_ms": round(b.hist.p95(), 3),
                "max_ms": round(b.hist.max_ms, 3),
                "total_bytes": b.bytes,
                "errors": b.errors,
                "err_pct": round(b.errors / b.count * 100, 3)
                            if b.count else 0,
            })

        urls = sorted(ks.apikey_counts.items(), key=lambda x: -x[1])[:top_urls]
        return {
            "apikey": apikey,
            "found": True,
            "windows": windows,
            "minute_series": {
                "metric": "mean_ms",
                "minutes": 60,
                "values": list(ks.minute_series(60, "mean_ms")),
            },
            "urls": [{"url": u, "count": int(c)} for u, c in urls],
        }
