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


# Endpoints we *might* poll; each declares its what= name, the URL suffix,
# and which role (if any) suggests the endpoint should be useful. Panels
# still drive which data they need; this just gates the outgoing HTTP
# calls so we don't spam a host with requests it can't serve.
_POLLED_ENDPOINTS = [
    ("cachestats",     "?what=cachestats&format=json"),
    ("servicestats",   "?what=servicestats&format=json"),
    ("activerequests", "?what=activerequests&format=json"),
    ("lastrequests",   "?what=lastrequests&format=json&minutes=1"),
]

_LIST_REFRESH_SECONDS = 300.0  # re-check endpoint availability every 5 min


async def poll_admin(base_url: str, host: str, store,
                     interval: float = 2.0, executor=None) -> None:
    """Poll admin endpoints forever and push snapshots into store[host]."""
    loop = asyncio.get_event_loop()
    owned_executor = executor is None
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=4)
    store.register_admin_host(host)

    seen_requests: set = set()
    store.admin_status[host] = f"probing {base_url}"
    list_last_fetched: float = 0.0

    try:
        while True:
            start = time.time()
            any_ok = False
            errors = []

            # (1) Refresh the availability map periodically (cheap).
            if start - list_last_fetched >= _LIST_REFRESH_SECONDS:
                try:
                    list_rows = await _run(
                        loop, executor, _fetch,
                        f"{base_url}?what=list&format=json",
                    )
                    whats = set()
                    for r in list_rows:
                        val = r.get("What") or r.get("what") or r.get("name") or ""
                        if val:
                            whats.add(val)
                    if whats:
                        store.available_what[host] = whats
                        store.host_role[host] = _detect_role(whats)
                        list_last_fetched = start
                except Exception as e:
                    # Leave availability empty so panels still attempt the
                    # standard endpoints; the per-endpoint error will surface
                    # if the host is truly unreachable.
                    errors.append(f"list: {e}")

            # (2) Poll the standard endpoints, skipping ones the host has
            #     declared unsupported (if we have a list).
            available = store.available_what.get(host)
            for name, suffix in _POLLED_ENDPOINTS:
                snap_dict = getattr(store, name)
                snap = snap_dict[host]
                if available and name not in available:
                    snap.ok = False
                    snap.error = "not supported on this host"
                    snap.rows = []
                    snap.fetched_at = time.time()
                    continue
                try:
                    rows = await _run(loop, executor, _fetch, base_url + suffix)
                    snap.fetched_at = time.time()
                    snap.ok = True
                    snap.error = ""
                    snap.rows = rows
                    any_ok = True

                    if name == "lastrequests":
                        _ingest_lastrequests(store, rows, seen_requests)
                    elif name == "cachestats":
                        _update_cache_history(
                            store.cache_history[host], rows, snap.fetched_at
                        )
                    elif name == "servicestats":
                        _update_service_history(
                            store.service_history[host], rows, snap.fetched_at
                        )
                except Exception as e:
                    snap.ok = False
                    snap.error = f"{type(e).__name__}: {e}"
                    snap.fetched_at = time.time()
                    errors.append(f"{name}: {e}")

            role = store.host_role.get(host, "unknown")
            role_suffix = f" [{role}]" if role != "unknown" else ""
            if any_ok and not errors:
                store.admin_status[host] = f"ok{role_suffix}"
            elif any_ok:
                store.admin_status[host] = f"partial{role_suffix}: {'; '.join(errors[:2])}"
            else:
                store.admin_status[host] = f"failing{role_suffix}: {errors[0] if errors else 'unknown'}"

            elapsed = time.time() - start
            await asyncio.sleep(max(0.0, interval - elapsed))
    finally:
        if owned_executor:
            executor.shutdown(wait=False)


# Role heuristic. Frontends expose the sputnik backends endpoint; backends
# expose qengine / gridproducers / obsproducers. Keep this generous — the
# role label is just a visual cue, never a hard filter.
def _detect_role(whats: set) -> str:
    frontend_markers = {"backends", "clusterinfo"}
    backend_markers = {"qengine", "producers", "gridproducers", "obsproducers",
                       "gridgenerations", "parameterinfo", "stations"}
    is_frontend = bool(whats & frontend_markers)
    is_backend = bool(whats & backend_markers)
    if is_frontend and is_backend:
        return "mixed"
    if is_frontend:
        return "frontend"
    if is_backend:
        return "backend"
    return "unknown"


async def poll_all(urls, store, interval: float = 2.0) -> None:
    """Run poll_admin concurrently for each (host, url) pair."""
    executor = ThreadPoolExecutor(max_workers=4 * max(1, len(urls)))
    tasks = []
    for host, base_url in urls:
        tasks.append(asyncio.create_task(
            poll_admin(base_url, host, store, interval, executor)
        ))
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        executor.shutdown(wait=False)


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(str(v).rstrip("%"))
    except (TypeError, ValueError):
        return default


def _update_cache_history(history, rows, ts: float) -> None:
    names = set()
    for r in rows:
        name = str(r.get("cache_name") or r.get("name") or "?")
        names.add(name)
        history.append(
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
    history.prune(names)


def _update_service_history(history, rows, ts: float) -> None:
    names = set()
    for r in rows:
        name = str(r.get("Handler") or r.get("handler") or "?")
        names.add(name)
        history.append(
            name,
            ts,
            {
                "req_per_min": _to_float(r.get("LastMinute")),
                "req_per_hour": _to_float(r.get("LastHour")),
                "req_per_day": _to_float(r.get("Last24Hours")),
                "avg_ms": _to_float(r.get("AverageDuration")),
            },
        )
    history.prune(names)


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
