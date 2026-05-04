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

    @staticmethod
    def trends(store, *, metric: str = "hits_per_min", samples: int = 30):
        """Per-cache trend lines for one metric. Returns
        ``[{host, cache_name, values}]`` so the web view can render a
        sparkline alongside each table row.
        """
        out = []
        for host in store.admin_hosts:
            snap = store.cachestats.get(host)
            if snap is None or not snap.ok:
                continue
            hist = store.cache_history.get(host)
            for r in snap.rows or []:
                name = str(r.get("cache_name") or r.get("name") or "?")
                vs = (list(hist.series(name, metric, samples=samples))
                      if hist else [])
                out.append({
                    "host": host,
                    "cache_name": name,
                    "values": [round(float(v), 3) for v in vs],
                })
        # step_seconds + last_ts so the per-row sparkline tooltip can
        # show the time at the cursor. The trend buckets are stamped
        # at admin-poll cadence (2 s by default) and the most recent
        # fetched_at across all hosts is "now-ish" for the right edge.
        return {
            "metric": metric,
            "samples": samples,
            "step_seconds": 2.0,
            "last_ts": _last_fetched(store.cachestats, store.admin_hosts),
            "rows": out,
        }

    @staticmethod
    def cluster_chart_per_host(store, *, cache_name: str,
                               metric: str = "hits_per_min",
                               samples: int = 150,
                               step_seconds: float = 2.0):
        """One per-host series for a single cache, for the cluster-mode
        multi-line chart. Reuses the existing per-host cache_history so
        no extra HTTP fetches are needed (cachestats is polled on the
        same 2 s admin cadence as everything else)."""
        series = []
        for host in store.admin_hosts:
            hist = store.cache_history.get(host)
            if hist is None:
                continue
            vs = list(hist.series(cache_name, metric, samples=samples))
            if not vs:
                continue
            series.append({
                "label": host,
                "values": [round(float(v), 3) for v in vs],
            })
        return {
            "cache_name": cache_name,
            "metric": metric,
            "step_seconds": step_seconds,
            "last_ts": _last_fetched(store.cachestats, store.admin_hosts),
            "series": series,
        }

    @staticmethod
    def cluster_cache_names(store):
        """Union of every cache name observed across all hosts in the
        cluster's store. Powers the cache picker in the Caches panel
        for cluster mode."""
        names: set = set()
        for host in store.admin_hosts:
            hist = store.cache_history.get(host)
            if hist is None:
                continue
            names.update(hist.names())
            snap = store.cachestats.get(host)
            if snap is not None and snap.ok:
                for r in snap.rows or []:
                    names.add(str(r.get("cache_name")
                                  or r.get("name") or "?"))
        return sorted(names)


def _last_fetched(snapshot_dict, hosts) -> float:
    """Most recent ``fetched_at`` across the given snapshot dict; used
    to label the right edge of cluster trend charts."""
    best = 0.0
    for host in hosts:
        snap = snapshot_dict.get(host)
        if snap is None:
            continue
        if snap.fetched_at and snap.fetched_at > best:
            best = snap.fetched_at
    return best
