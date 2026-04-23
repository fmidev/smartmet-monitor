"""Periodic poller for the SmartMet admin plugin.

Endpoints (all with &format=json):
  cachestats      - per-cache size/hits/misses/hitrate
  servicestats    - per-handler request rates
  activerequests  - in-flight requests
  lastrequests    - recently completed requests (used as a fallback data
                    source when we can't read the log file directly)
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .logparse import parse_iso, strip_query


def _fetch(url: str, timeout: float = 5.0) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "smartmet-top/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    parsed = json.loads(data)
    if isinstance(parsed, list):
        return parsed
    # Some endpoints wrap the rows in an object; flatten to a list.
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return []


async def _run(loop, executor, fn, *args):
    return await loop.run_in_executor(executor, fn, *args)


async def poll_admin(base_url: str, store, interval: float = 2.0) -> None:
    """Poll admin endpoints forever and push snapshots into `store`.

    Also feeds lastrequests results into store.record_request so the URLs
    panel has data even when log files are not readable locally.
    """
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=4)

    endpoints = [
        ("cachestats", f"{base_url}?what=cachestats&format=json"),
        ("servicestats", f"{base_url}?what=servicestats&format=json"),
        ("activerequests", f"{base_url}?what=activerequests&format=json"),
        ("lastrequests", f"{base_url}?what=lastrequests&format=json&minutes=1"),
    ]

    seen_requests: set = set()  # dedupe lastrequests so we don't double-count
    store.admin_status = f"polling {base_url}"

    while True:
        start = time.time()
        any_ok = False
        errors = []
        for name, url in endpoints:
            try:
                rows = await _run(loop, executor, _fetch, url)
                snap = getattr(store, name)
                snap.fetched_at = time.time()
                snap.ok = True
                snap.error = ""
                snap.rows = rows
                any_ok = True

                if name == "lastrequests":
                    _ingest_lastrequests(store, rows, seen_requests)
                elif name == "cachestats":
                    _update_cache_history(store, rows, snap.fetched_at)
                elif name == "servicestats":
                    _update_service_history(store, rows, snap.fetched_at)
            except Exception as e:
                snap = getattr(store, name)
                snap.ok = False
                snap.error = f"{type(e).__name__}: {e}"
                snap.fetched_at = time.time()
                errors.append(f"{name}: {e}")

        store.admin_status = (
            f"ok {base_url}" if any_ok and not errors
            else f"partial {base_url}: {'; '.join(errors[:2])}" if any_ok
            else f"failing {base_url}: {errors[0] if errors else 'unknown'}"
        )

        elapsed = time.time() - start
        await asyncio.sleep(max(0.0, interval - elapsed))


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return default


def _update_cache_history(store, rows, ts: float) -> None:
    names = set()
    for r in rows:
        name = str(r.get("cache_name") or r.get("name") or "?")
        names.add(name)
        store.cache_history.append(
            name,
            ts,
            {
                "size": _to_float(r.get("size")),
                "maxsize": _to_float(r.get("maxsize") or r.get("max") or 0),
                "hits_per_min": _to_float(r.get("hits/min") or r.get("hits_per_min")),
                "inserts_per_min": _to_float(
                    r.get("inserts/min") or r.get("inserts_per_min")
                ),
                "hitrate": _to_float(r.get("hitrate")),
            },
        )
    store.cache_history.prune(names)


def _update_service_history(store, rows, ts: float) -> None:
    names = set()
    for r in rows:
        name = str(r.get("Handler") or r.get("handler") or "?")
        names.add(name)
        store.service_history.append(
            name,
            ts,
            {
                "req_per_min": _to_float(r.get("LastMinute")),
                "req_per_hour": _to_float(r.get("LastHour")),
                "req_per_day": _to_float(r.get("Last24Hours")),
                "avg_ms": _to_float(r.get("AverageDuration")),
            },
        )
    store.service_history.prune(names)


def _ingest_lastrequests(store, rows, seen: set, keep_last: int = 20_000) -> None:
    """Feed admin /lastrequests rows into the URL stats store.

    Each row has at minimum: Time, Duration, RequestString (plus Status in
    recent versions). We dedupe by (Time, RequestString).
    """
    for r in rows:
        t_str = r.get("Time") or r.get("time") or ""
        dur_str = r.get("Duration") or r.get("duration") or "0"
        req_str = r.get("RequestString") or r.get("requeststring") or ""
        status = r.get("Status") or r.get("status") or 0
        bytes_ = r.get("ContentLength") or r.get("contentlength") or 0
        apikey = r.get("Apikey") or r.get("apikey") or "-"
        key = (t_str, req_str)
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > keep_last:
            # drop some arbitrary old entries to bound memory
            for _ in range(keep_last // 4):
                seen.pop()
        try:
            ts = parse_iso(t_str) if t_str else time.time()
            dur = float(dur_str)
            st = int(status) if status else 0
            nb = int(bytes_) if bytes_ else 0
        except (ValueError, TypeError):
            continue
        url = strip_query(req_str.split(" ", 1)[-1] if " " in req_str else req_str)
        if not url:
            continue
        store.record_request(
            ts=ts,
            url=url,
            dur_ms=dur,
            nbytes=nb,
            status=st,
            apikey=apikey,
        )
