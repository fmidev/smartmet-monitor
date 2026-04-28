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
