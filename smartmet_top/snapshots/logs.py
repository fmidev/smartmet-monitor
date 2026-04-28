"""Logs snapshot — recent multi-source log lines.

The store keeps a bounded ``recent_lines`` deque holding the most
recent access-log lines (with ``[source]`` prefix when multiple log
files are tailed) plus systemd-journal lines if the journal source
is enabled. The web Logs panel polls this to render a live
scroll-back.
"""

from __future__ import annotations

from typing import List


class LogsSnapshot:
    name = "logs"

    @staticmethod
    def table(store, *, n: int = 500, filter_str: str = ""):
        """Last ``n`` lines, optionally filtered by substring.

        Returns a flat ``(headers=[line], rows=[[line]])`` shape so the
        existing CSV exporter works without special-casing.
        """
        headers = ["line"]
        lines: List[str] = list(store.recent_lines)
        if filter_str:
            f = filter_str.lower()
            lines = [ln for ln in lines if f in ln.lower()]
        if n > 0 and len(lines) > n:
            lines = lines[-n:]
        rows = [[ln] for ln in lines]
        return headers, rows

    @staticmethod
    def stream(store, *, n: int = 500, filter_str: str = "") -> dict:
        """Same as table() but in a richer JSON envelope: convenient for
        the web client to know how many were dropped on its behalf.
        """
        all_n = len(store.recent_lines)
        headers, rows = LogsSnapshot.table(store, n=n, filter_str=filter_str)
        return {
            "lines": [r[0] for r in rows],
            "returned": len(rows),
            "stored_total": all_n,
            "filter": filter_str,
        }
