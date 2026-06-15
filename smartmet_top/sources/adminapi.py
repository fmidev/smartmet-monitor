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

from collections import deque

from ..state.store import ADMIN_HISTORY_SAMPLES
from .logparse import parse_iso, strip_query


def _new_active_history():
    """Bounded deque for active-request count history, sized identically
    to the other admin-derived sparkline rings."""
    return deque(maxlen=ADMIN_HISTORY_SAMPLES)


# Default localhost admin URLs probed when the operator passes no
# explicit -u flag. SmartMet's FMI deployments always run the
# frontend on 8080 and the backend on 8081 (this is the convention
# baked into smartmet-server's default config), so probing those is
# almost always what the operator wants.
DEFAULT_PROBE_URLS = (
    ("frontend", "http://localhost:8080/admin"),
    ("backend",  "http://localhost:8081/admin"),
)


def _looks_like_admin(body: str) -> bool:
    """Heuristic: does ``body`` look like the SmartMet admin
    plugin's ``?what=list`` output?

    The plugin returns either a JSON list of supported handler names
    (``["cachestats", "servicestats", ...]``) or — on some older
    builds — a whitespace-separated list. Either way, the strings
    we actually care about are present. We accept the response if
    any one of those tokens appears.
    """
    return any(tok in body for tok in
               ("cachestats", "servicestats",
                "activerequests", "lastrequests"))


def probe_one_admin_url(url: str, timeout: float = 1.0) -> bool:
    """Synchronous probe used at startup.

    Hits ``<url>?what=list`` with a short timeout. Returns True if
    the response is HTTP 200 and looks like an admin endpoint, False
    on any error / timeout / unexpected body. Safe to call from
    main(); doesn't depend on the asyncio loop.
    """
    try:
        req = urllib.request.Request(
            url + "?what=list",
            headers={"User-Agent": "smartmet-monitor-probe"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = resp.read(8192).decode("utf-8", errors="replace")
        return _looks_like_admin(body)
    except Exception:
        return False


def probe_default_admin_urls(timeout: float = 1.0):
    """Probe the SmartMet default ports on localhost. Returns a list of
    ``(label, url)`` tuples for the URLs that responded — same shape
    ``_parse_admin_urls`` produces, so callers can pass it directly to
    ``runtime.start_sources(admin_urls=...)``.

    Silently skips anything that fails or times out: if neither port
    is listening the result is an empty list and the caller proceeds
    as if no admin URL had been configured.
    """
    out = []
    for label, url in DEFAULT_PROBE_URLS:
        if probe_one_admin_url(url, timeout=timeout):
            out.append((label, url))
    return out


def _fetch(url: str, timeout: float = 5.0) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "smartmet-top/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    # The admin plugin happily returns HTML when it doesn't know the
    # request (and an empty body in some edge cases). Surface both as a
    # clear error including a body preview, so a JSONDecodeError at
    # char 0 actually tells the operator what they're looking at.
    if not data:
        raise ValueError(
            f"empty response from {url} (Content-Type: {ctype or '?'})"
        )
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        snippet = data[:120].decode("utf-8", errors="replace").replace("\n", " ").strip()
        raise ValueError(
            f"non-JSON response from {url} "
            f"(Content-Type: {ctype or '?'}, body[:120]={snippet!r})"
        ) from e
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
    # ``fields=`` and ``timeformat=`` were added to spine's
    # lastrequests handler in the log-flush-no-lock branch.
    # Smwebmon asks for the minimum set of fields IP Flow needs
    # (no RequestString — URLs and API-key per-URL stats fill from
    # the live tail when log files are reachable; on log-less
    # backends those panels stay empty until the operator finds an
    # alternative ingest, but the size + parsing cost of pulling
    # full URLs every 2 s is too high to justify pulling them by
    # default). ``timeformat=epoch`` returns Time as Unix epoch
    # seconds; ``_ingest_lastrequests`` parses that directly. Older
    # spine builds ignore both parameters and return the legacy
    # three-column response; the parser detects that case by the
    # presence of RequestString and falls back to URL-stats ingest
    # so old hosts keep their previous behaviour.
    ("lastrequests",   "?what=lastrequests&format=json&minutes=1"
                       "&fields=time,duration,ip,status,size,plugin"
                       "&timeformat=epoch"),
]

_LIST_REFRESH_SECONDS = 300.0  # re-check endpoint availability every 5 min


# mallocstats lives outside the standard 2-s rotation: the JSON dump
# is large (~50 KB on a 32-arena backend) and the underlying numbers
# don't change at sub-second rates, so a slower cadence saves the
# bandwidth and the spine-side epoch-refresh cost.
_MALLOCSTATS_REFRESH_SECONDS = 30.0


async def poll_admin(base_url: str, host: str, store,
                     interval: float = 2.0, executor=None) -> None:
    """Poll admin endpoints forever and push snapshots into store[host]."""
    loop = asyncio.get_event_loop()
    owned_executor = executor is None
    if executor is None:
        executor = ThreadPoolExecutor(max_workers=4)
    store.register_admin_host(host)
    # Operators often paste the URL with a trailing slash; suffixes start
    # with `?` so a stray `/` would produce `…/admin/?what=…` which on
    # some configurations 404s back as HTML.
    base_url = base_url.rstrip("/")

    seen_requests: set = set()
    store.admin_status[host] = f"probing {base_url}"
    list_last_fetched: float = 0.0
    mallocstats_last_fetched: float = 0.0

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
                        # The list endpoint historically returned HTML-style
                        # rows with a "What" column; some builds return a
                        # plain JSON array of strings instead. Accept both.
                        if isinstance(r, str):
                            val = r
                        elif isinstance(r, dict):
                            val = (r.get("What") or r.get("what")
                                   or r.get("name") or "")
                        else:
                            val = ""
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
                    elif name == "activerequests":
                        # Track in-flight count per poll for the
                        # sparkline at the top of the Active panel.
                        store.active_count_history.setdefault(
                            host, _new_active_history()
                        ).append(len(rows))
                except Exception as e:
                    snap.ok = False
                    snap.error = f"{type(e).__name__}: {e}"
                    snap.fetched_at = time.time()
                    errors.append(f"{name}: {e}")

            # (3) Allocator stats — poll on a 30 s cadence (see comment
            #     near _MALLOCSTATS_REFRESH_SECONDS for the rationale).
            #     Skip when the host hasn't advertised support, so the
            #     poller doesn't spam old spine builds with 404s.
            if (start - mallocstats_last_fetched >= _MALLOCSTATS_REFRESH_SECONDS
                    and (not available or "mallocstats" in available)):
                try:
                    from . import mallocstats
                    text = await _run(loop, executor,
                                       mallocstats._fetch_mallocstats, base_url)
                    if text.startswith("__MALLOCSTATS_FETCH_ERROR__"):
                        store.mallocstats_error[host] = text.split(": ", 1)[-1]
                    else:
                        sample = mallocstats.parse_mallocstats(text)
                        if sample is not None:
                            store.mallocstats_latest[host] = sample
                            store.mallocstats_history[host].append(sample)
                            store.mallocstats_error[host] = ""
                        else:
                            # Empty / non-JSON / non-jemalloc response.
                            # Keep the last good sample but record the
                            # failure so the panel can surface it.
                            store.mallocstats_error[host] = (
                                "unrecognised mallocstats payload")
                    store.mallocstats_fetched_at[host] = time.time()
                    mallocstats_last_fetched = start
                except Exception as e:
                    store.mallocstats_error[host] = (
                        f"{type(e).__name__}: {e}")

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
                # AverageCPUMs is added by spine 26.4.27+. Older
                # spine versions silently omit the field; we read 0
                # in that case and the panel renders "—" so the
                # operator can tell the spine on the host has not
                # been upgraded yet.
                "avg_cpu_ms": _to_float(r.get("AverageCPUMs")),
            },
        )
    history.prune(names)


def _ingest_lastrequests(store, rows, seen: set, keep_last: int = 20_000) -> None:
    """Feed admin /lastrequests rows into the IP Flow + URL stores.

    Two response shapes:

      * **New spine** (log-flush-no-lock branch and later) honours
        ``fields=time,duration,ip,status,bytes,plugin&timeformat=epoch``
        in the URL — Time arrives as Unix epoch seconds and the
        per-IP fields are present. We feed only the IP Flow
        retention; URL / API-key stats refill from the live log
        tail on backends that have one. The URL itself is not
        requested in fields= since it dominates response size and
        IP Flow doesn't need it.
      * **Old spine** ignores the parameters and returns the
        historical three-column response (Time, Duration, URL).
        We detect that by the presence of URL without IP and
        fall back to URL-stats ingest so backends still on
        pre-fix spine keep their previous behaviour.

    Dedupe key combines (Time, IP-or-URL).
    """
    if not rows:
        return
    ipflow_by_plugin: dict = {}
    for r in rows:
        t_str = r.get("Time") or r.get("time") or ""
        # ``Duration`` is integer milliseconds in the new spine
        # response; older spine used a 4-significant-digit
        # ``average_and_format`` string. ``float()`` handles either.
        dur_str = r.get("Duration") or r.get("duration") or "0"
        # Spine's lastrequests handler renamed RequestString → URL
        # in the log-flush-no-lock branch; tolerate both spellings
        # for old hosts.
        url_str = (r.get("URL") or r.get("url")
                   or r.get("RequestString") or r.get("requeststring")
                   or "")
        status = r.get("Status") or r.get("status") or 0
        # Spine's lastrequests handler renamed Bytes → ContentLength
        # (header) and the field key bytes → size in the
        # log-flush-no-lock branch; tolerate every form for
        # forward / backward compatibility.
        bytes_ = (r.get("ContentLength") or r.get("contentlength")
                  or r.get("Size") or r.get("size")
                  or r.get("Bytes") or r.get("bytes") or 0)
        apikey = r.get("Apikey") or r.get("apikey") or "-"
        ip = r.get("IP") or r.get("ip") or ""
        plugin = r.get("Plugin") or r.get("plugin") or ""
        dedup_key = (t_str, ip or url_str)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        if len(seen) > keep_last:
            for _ in range(keep_last // 4):
                seen.pop()
        try:
            # New-spine path: Time is Unix epoch seconds (via
            # ``timeformat=epoch``). Old-spine path: Time is
            # "HH:MM:SS.fff" time-of-day, which the fast parse_iso
            # can't decode and falls back to wall-clock ``now`` —
            # records remain visible but cluster at the ingestion
            # instant.
            if t_str:
                try:
                    ts = float(t_str)
                except ValueError:
                    ts = parse_iso(t_str)
            else:
                ts = time.time()
            dur = float(dur_str)
            st = int(status) if status else 0
            nb = int(bytes_) if bytes_ else 0
        except (ValueError, TypeError):
            continue

        if ip:
            # New-spine path: feed IP Flow only. No URL parsing.
            ipflow_by_plugin.setdefault(plugin, []).append(
                (ts, dur, nb, st, ip))
        elif url_str:
            # Old-spine fallback: no IP, but URL is present. Feed
            # URL stats so log-less backends running pre-fix spine
            # keep their previous URLs / API Keys panels.
            url = strip_query(
                url_str.split(" ", 1)[-1] if " " in url_str else url_str)
            if url:
                store.record_request(
                    ts=ts,
                    url=url,
                    dur_ms=dur,
                    nbytes=nb,
                    status=st,
                    apikey=apikey,
                    source_label=plugin or None,
                )

    # Batch IP Flow records under one Store lock per plugin.
    for plugin, records in ipflow_by_plugin.items():
        store.record_requests_bulk(records, source_label=plugin or None)
