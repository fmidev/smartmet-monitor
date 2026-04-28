"""Network snapshot — TCP connection-state counts."""

from __future__ import annotations


class NetworkSnapshot:
    name = "network"

    @staticmethod
    def table(store):
        counts, _ = store.netstats_states_latest()
        if not counts:
            return [], []
        headers = ["state", "count"]
        rows = sorted(counts.items(), key=lambda kv: -kv[1])
        rows = [[k, v] for k, v in rows if v > 0]
        return headers, rows
