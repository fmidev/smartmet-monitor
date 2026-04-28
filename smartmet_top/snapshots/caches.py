"""Caches snapshot — admin-plugin ?what=cachestats."""

from __future__ import annotations


def _as_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class CachesSnapshot:
    name = "caches"

    @staticmethod
    def table(store):
        headers = ["host", "cache_name", "size", "maxsize",
                   "hits_per_min", "inserts_per_min", "hitrate_pct"]
        rows = []
        for host in store.admin_hosts:
            snap = store.cachestats.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                rows.append([
                    host,
                    str(r.get("cache_name") or r.get("name") or "?"),
                    _as_int(r.get("size")),
                    _as_int(r.get("maxsize") or r.get("max") or 0),
                    _as_float(r.get("hits/min") or r.get("hits_per_min")),
                    _as_float(r.get("inserts/min") or r.get("inserts_per_min")),
                    _as_float(str(r.get("hitrate") or "0").rstrip("%")),
                ])
        return headers, rows
