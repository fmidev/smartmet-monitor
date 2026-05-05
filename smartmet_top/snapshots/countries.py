"""Per-country aggregate of access-log traffic.

Derived on demand from ``Store._ipflow_minutes`` and
``store.country_db`` (which the smwebmon entry point loads from RIR
delegated-stats files at startup). When no country DB is loaded, the
snapshot returns empty tables and the panel hides itself.

Two reads:
  * ``timeline(store, minutes, top_n)`` — multi-line series, one line
    per top-N country by total request count, plus an "other" series
    aggregating the long tail. Used by the Countries panel header
    chart for "who's hitting me right now?".
  * ``table(store, minutes, top_n)`` — sortable table per country with
    request count, byte total, error %, and a short list of the top
    IPs from that country. Drives the panel's main view.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _aggregate(
    store,
    minutes: int,
) -> Tuple[List[Tuple[int, Dict[str, Tuple[int, int]]]],
          Dict[str, Tuple[int, int, int, Dict[str, int]]]]:
    """Walk ``_ipflow_minutes`` once and produce two views the
    snapshot's two callers need:

      * ``per_minute``: list of ``(minute_epoch, {cc: (reqs, bytes)})``
        sorted by minute. The chart slices this.
      * ``totals``: ``{cc: (reqs, bytes, errors, {ip: count})}`` —
        cumulative across the whole window. The table reads this.

    Returning both from one walk avoids re-scanning ~100 K records
    for each panel refresh."""
    cdb = getattr(store, "country_db", None)
    if cdb is None:
        return [], {}
    with store._lock:
        if not store._ipflow_minutes:
            return [], {}
        latest = max(store._ipflow_minutes)
        cutoff = latest - max(1, int(minutes)) + 1
        per_minute: List[Tuple[int, Dict[str, Tuple[int, int]]]] = []
        totals: Dict[str, List] = {}
        for m in sorted(store._ipflow_minutes):
            if m < cutoff:
                continue
            bucket_cc: Dict[str, List[int]] = {}
            for rec in store._ipflow_minutes[m]:
                ts, ip, dur, nb, status = rec[0], rec[1], rec[2], rec[3], rec[4]
                cc = cdb.lookup(ip) or "??"
                pm = bucket_cc.get(cc)
                if pm is None:
                    bucket_cc[cc] = [1, nb]
                else:
                    pm[0] += 1
                    pm[1] += nb
                t = totals.get(cc)
                if t is None:
                    totals[cc] = [1, nb, 1 if status >= 400 else 0, {ip: 1}]
                else:
                    t[0] += 1
                    t[1] += nb
                    if status >= 400:
                        t[2] += 1
                    t[3][ip] = t[3].get(ip, 0) + 1
            per_minute.append((
                m * 60,
                {cc: (c, b) for cc, (c, b) in bucket_cc.items()},
            ))
    out_totals = {cc: (v[0], v[1], v[2], v[3]) for cc, v in totals.items()}
    return per_minute, out_totals


class CountriesSnapshot:
    name = "countries"

    @staticmethod
    def status(store) -> Dict:
        cdb = getattr(store, "country_db", None)
        return {
            "enabled": cdb is not None and bool(cdb),
            "netblocks_v4": len(cdb.v4_starts) if cdb else 0,
            "netblocks_v6": len(cdb.v6_starts) if cdb else 0,
            "loaded_paths": list(cdb.loaded_paths) if cdb else [],
        }

    @staticmethod
    def timeline(store, minutes: int = 60, top_n: int = 8) -> Dict:
        per_minute, totals = _aggregate(store, minutes)
        # Pick the top-N countries by total request count; everything
        # else folds into a single "other" series so a 200-country
        # multi-line chart doesn't overwhelm the rendering.
        if not totals:
            return {"name": "countries_timeline", "minute_step": 60,
                    "series": [], "totals_top": []}
        ranked = sorted(totals.items(), key=lambda kv: -kv[1][0])
        top = [cc for cc, _ in ranked[:max(1, top_n)]]
        top_set = set(top)
        # Empty per-cc series indexed by minute order.
        ts_index = [t for t, _ in per_minute]
        cc_to_reqs: Dict[str, List[int]] = {cc: [0] * len(per_minute) for cc in top}
        cc_to_reqs["other"] = [0] * len(per_minute)
        for i, (_t, by_cc) in enumerate(per_minute):
            for cc, (c, _b) in by_cc.items():
                key = cc if cc in top_set else "other"
                cc_to_reqs[key][i] += c
        # Drop "other" if everything fit in top_n.
        if all(v == 0 for v in cc_to_reqs["other"]):
            del cc_to_reqs["other"]
        series = [
            {"label": cc, "values": cc_to_reqs[cc]}
            for cc in (top + (["other"] if "other" in cc_to_reqs else []))
        ]
        totals_top = [
            {"cc": cc, "reqs": int(v[0]), "bytes": int(v[1])}
            for cc, v in ranked[:max(1, top_n)]
        ]
        return {
            "name": "countries_timeline",
            "minute_step": 60,
            "ts": ts_index,
            "series": series,
            "totals_top": totals_top,
        }

    @staticmethod
    def table(store, minutes: int = 60, top_n: int = 0) -> Dict:
        _, totals = _aggregate(store, minutes)
        rows = []
        for cc, (reqs, b, errs, ip_counts) in totals.items():
            top_ips = sorted(ip_counts.items(), key=lambda kv: -kv[1])[:5]
            rows.append({
                "cc": cc,
                "reqs": int(reqs),
                "bytes": int(b),
                "err_pct": round(100.0 * errs / reqs, 2) if reqs else 0.0,
                "ips": int(len(ip_counts)),
                "top_ips": [
                    {"ip": ip, "count": int(c)} for ip, c in top_ips
                ],
            })
        rows.sort(key=lambda r: -r["reqs"])
        if top_n and len(rows) > top_n:
            rows = rows[:top_n]
        return {
            "name": "countries",
            "headers": ["cc", "reqs", "bytes", "err_pct", "ips"],
            "rows": rows,
            "minutes": minutes,
        }
