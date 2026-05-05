"""IP-flow snapshot — feeds the topological "particles flying from
each IP to the centre" panel.

Two read paths off the same in-RAM retention:

  * `timeline(store, minutes)` — per-minute aggregate (count, bytes)
    for the request-rate / byte-rate scrubber graphs at the top of
    the panel. Pulled from `_global_minutes`, which is populated for
    every request whether or not the IP retention is on, so the
    timeline is always complete even if a few sources lacked an IP
    field.

  * `window(store, start, seconds, top_n)` — raw record list for the
    topology animation. Each record carries (ts, ip, dur_ms, bytes,
    status); the panel decides per-particle visual encoding from
    those four channels. The `ips` map carries the angular position
    each IP gets on the rim, computed once server-side so every
    polling cycle places the same IP at the same angle.

Angle encoding: ``angle = (ip_int * 360) / 2**32``. Pure function of
the address — neighbours on the same /24 sit at adjacent angles, so
subnet bursts cluster visually. An internal-only deployment (all in
10.x or 192.168.x) collapses into one tiny arc; that's a real
limitation but worth the stability win that comes from never having
to recompute layout when new IPs appear.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def _ip_to_int(ip: str) -> int:
    """Convert a dotted-quad / IPv6-literal address to a 32-bit
    angular bucket. IPv4 maps directly. IPv6 hashes the full 128 bits
    down to 32 with a multiplicative scramble so v6 clients get
    visually-spread angles too. Bad addresses (logger glitches, "-")
    fall through to 0."""
    if not ip or ip == "-":
        return 0
    if ":" in ip:
        h = 0
        for part in ip.split(":"):
            if not part:
                continue
            try:
                h = (h * 65537 + int(part, 16)) & 0xFFFFFFFF
            except ValueError:
                return 0
        return h
    parts = ip.split(".")
    if len(parts) != 4:
        return 0
    try:
        a, b, c, d = (int(p) for p in parts)
    except ValueError:
        return 0
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return 0
    return (a << 24) | (b << 16) | (c << 8) | d


def angle_for_ip(ip: str) -> float:
    """Stable angular position in degrees [0, 360) for the given IP."""
    return (_ip_to_int(ip) * 360.0) / (1 << 32)


class IPFlowSnapshot:
    name = "ipflow"

    @staticmethod
    def timeline(store, minutes: int = 1440, source: str = "") -> Dict:
        rows = store.ipflow_timeline(minutes=minutes, source=source or None)
        return {
            "name": "ipflow_timeline",
            "minute_step": 60,
            "source": source or "",
            "sources": store.ipflow_sources(),
            "buckets": [
                {"t": int(t), "reqs": int(c), "bytes": int(b)}
                for (t, c, b) in rows
            ],
        }

    @staticmethod
    def window(
        store,
        start_ts: float,
        seconds: float,
        top_n: int = 0,
        source: str = "",
        max_records: int = 200_000,
    ) -> Dict:
        recs, summary = store.ipflow_window(
            start_ts, seconds, top_n=top_n, source=source or None)
        # The top-N filter trims by IP, but a 5-minute window of a
        # busy backend can still spill far more raw records than the
        # browser wants to render. Cap the records list at
        # `max_records`, oldest-first; the per-IP summary stays
        # complete either way.
        if len(recs) > max_records:
            recs = recs[-max_records:]
        cdb = getattr(store, "country_db", None)
        ips = {
            ip: {
                "angle": angle_for_ip(ip),
                "count": int(count),
                "bytes": int(b),
                "cc": cdb.lookup(ip) if cdb else "",
            }
            for ip, (count, b) in summary.items()
        }
        return {
            "name": "ipflow_window",
            "start": float(start_ts),
            "seconds": float(seconds),
            "top_n": int(top_n),
            "source": source or "",
            "ips": ips,
            "requests": [
                {
                    "t": float(ts),
                    "ip": ip,
                    "dur_ms": int(dur),
                    "bytes": int(nb),
                    "status": int(stt),
                    "src": src or "",
                }
                for (ts, ip, dur, nb, stt, src) in recs
            ],
        }
