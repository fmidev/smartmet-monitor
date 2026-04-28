"""Active-requests snapshot — admin-plugin ?what=activerequests."""

from __future__ import annotations


def _dur(r) -> float:
    try:
        return float(r.get("Duration") or r.get("duration") or 0)
    except (ValueError, TypeError):
        return 0.0


class ActiveSnapshot:
    name = "active"

    @staticmethod
    def table(store):
        headers = ["host", "id", "duration_s", "client_ip", "apikey", "request"]
        rows = []
        for host in store.admin_hosts:
            snap = store.activerequests.get(host)
            if snap is None or not snap.ok:
                continue
            for r in snap.rows or []:
                rows.append([
                    host,
                    str(r.get("Id") or r.get("id") or ""),
                    round(_dur(r), 3),
                    str(r.get("ClientIP") or r.get("clientip") or ""),
                    str(r.get("Apikey") or r.get("apikey") or "-"),
                    str(r.get("RequestString") or r.get("requeststring") or ""),
                ])
        return headers, rows

    @staticmethod
    def chart(store) -> dict:
        """Aggregated in-flight count history across hosts.

        Mirrors the Active panel's top-of-screen sparkline: pad shorter
        per-host buffers with leading zeros, then sum.
        """
        agg: list = []
        for host in store.admin_hosts:
            buf = store.active_count_history.get(host)
            if not buf:
                continue
            samples = list(buf)
            if not agg:
                agg = samples[:]
                continue
            if len(samples) < len(agg):
                samples = [0] * (len(agg) - len(samples)) + samples
            elif len(samples) > len(agg):
                agg = [0] * (len(samples) - len(agg)) + agg
            agg = [a + b for a, b in zip(agg, samples)]
        return {
            "values": [int(v) for v in agg],
            "current": int(agg[-1]) if agg else 0,
            "peak": int(max(agg)) if agg else 0,
        }
