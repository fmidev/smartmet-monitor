"""Services snapshot — admin-plugin ?what=servicestats."""

from __future__ import annotations


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class ServicesSnapshot:
    name = "services"

    @staticmethod
    def table(store):
        headers = ["host", "handler", "req_per_min", "req_per_hour",
                   "req_per_day", "avg_ms", "avg_cpu_ms"]
        rows = []
        for host in store.admin_hosts:
            snap = store.servicestats.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                rows.append([
                    host,
                    str(r.get("Handler") or r.get("handler") or "?"),
                    _f(r.get("LastMinute")),
                    _f(r.get("LastHour")),
                    _f(r.get("Last24Hours")),
                    _f(r.get("AverageDuration")),
                    _f(r.get("AverageCPUMs")),
                ])
        return headers, rows

    @staticmethod
    def trends(store, *, metric: str = "req_per_min", samples: int = 30):
        """Per-handler trend series for the requested metric. Returned as
        ``[{host, handler, values}]`` so the web view can render a
        per-row sparkline.
        """
        out = []
        for host in store.admin_hosts:
            snap = store.servicestats.get(host)
            if snap is None or not snap.ok:
                continue
            hist = store.service_history.get(host)
            for r in snap.rows or []:
                handler = str(r.get("Handler") or r.get("handler") or "?")
                vs = (list(hist.series(handler, metric, samples=samples))
                      if hist else [])
                out.append({
                    "host": host,
                    "handler": handler,
                    "values": [round(float(v), 3) for v in vs],
                })
        return {"metric": metric, "samples": samples, "rows": out}

    @staticmethod
    def cluster_chart_per_host(store, *, handler: str,
                               metric: str = "req_per_min",
                               samples: int = 150,
                               step_seconds: float = 2.0):
        """One per-host series for a single handler — the Services
        cluster-mode multi-line chart. Reuses the existing per-host
        service_history; servicestats is polled on the same 2 s admin
        cadence so no extra HTTP fetches are needed."""
        series = []
        for host in store.admin_hosts:
            hist = store.service_history.get(host)
            if hist is None:
                continue
            vs = list(hist.series(handler, metric, samples=samples))
            if not vs:
                continue
            series.append({
                "label": host,
                "values": [round(float(v), 3) for v in vs],
            })
        best = 0.0
        for host in store.admin_hosts:
            snap = store.servicestats.get(host)
            if snap is None:
                continue
            if snap.fetched_at and snap.fetched_at > best:
                best = snap.fetched_at
        return {
            "handler": handler,
            "metric": metric,
            "step_seconds": step_seconds,
            "last_ts": best,
            "series": series,
        }

    @staticmethod
    def cluster_handler_names(store):
        """Union of every handler observed across hosts in the cluster
        store. Powers the handler picker in the Services panel for
        cluster mode."""
        names: set = set()
        for host in store.admin_hosts:
            hist = store.service_history.get(host)
            if hist is None:
                continue
            names.update(hist.names())
            snap = store.servicestats.get(host)
            if snap is not None and snap.ok:
                for r in snap.rows or []:
                    names.add(str(r.get("Handler")
                                  or r.get("handler") or "?"))
        return sorted(names)
