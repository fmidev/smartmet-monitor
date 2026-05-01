"""Active-requests snapshot — admin-plugin ?what=activerequests."""

from __future__ import annotations

import time


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
        # Each agg[] entry is one admin poll cycle; the operator's
        # --admin-interval (default 2s) is the nominal step. last_ts
        # is the wall-clock at the moment of API call; the most
        # recent sample was taken within ~step_seconds of that.
        return {
            "values": [int(v) for v in agg],
            "current": int(agg[-1]) if agg else 0,
            "peak": int(max(agg)) if agg else 0,
            "last_ts": time.time(),
            "step_seconds": 2.0,
        }

    @staticmethod
    def chart_per_host(store) -> dict:
        """Per-host in-flight count history, for cluster-mode line
        overlay (one line per backend instead of the aggregated total).

        Returned shape:
          {
            "step_seconds": <admin poll interval>,
            "last_ts":      <Unix epoch of most recent sample>,
            "series":       [{"label": <host>, "values": [...] }, ...]
          }

        Hosts with no buffered samples yet are skipped. The client
        decides ordering / color assignment.
        """
        series = []
        for host in store.admin_hosts:
            buf = store.active_count_history.get(host)
            if not buf:
                continue
            series.append({
                "label": host,
                "values": [int(v) for v in buf],
            })
        return {
            "series": series,
            "last_ts": time.time(),
            "step_seconds": 2.0,
        }
