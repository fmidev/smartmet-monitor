"""Network snapshot — TCP states, listen sockets, per-NIC bandwidth."""

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

    @staticmethod
    def detail(store) -> dict:
        """Full Network-panel payload: TCP summary series, per-state
        history, listen sockets with accept-queue depth, per-NIC rx/tx
        rate series, and the latest values for headline numbers.
        """
        counts, listen = store.netstats_states_latest()
        retrans, overflow, drops = store.netstats_tcp_series()
        retrans = [round(float(v), 3) for v in retrans]
        overflow = [round(float(v), 3) for v in overflow]
        drops = [round(float(v), 3) for v in drops]

        states = []
        for st, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            if c <= 0:
                continue
            series = [int(v) for v in store.netstats_state_series(st)]
            states.append({
                "state": st,
                "count": int(c),
                "trend": series,
            })

        ifaces = []
        for nic in store.netstats_iface_names():
            rx, tx = store.netstats_iface_series(nic)
            ifaces.append({
                "iface": nic,
                "rx_bps": [round(float(v), 1) for v in rx],
                "tx_bps": [round(float(v), 1) for v in tx],
                "rx_latest": round(float(rx[-1]), 1) if rx else 0.0,
                "tx_latest": round(float(tx[-1]), 1) if tx else 0.0,
            })

        return {
            "enabled": bool(store.netstats_enabled),
            "tcp_summary": {
                "retrans_per_s": retrans,
                "listen_overflow_per_s": overflow,
                "listen_drop_per_s": drops,
                "retrans_latest": retrans[-1] if retrans else 0.0,
                "overflow_latest": overflow[-1] if overflow else 0.0,
                "drop_latest": drops[-1] if drops else 0.0,
            },
            "states": states,
            "listen_sockets": [
                {"port": int(p), "recv_q": int(q)} for p, q in listen
            ],
            "ifaces": ifaces,
        }
