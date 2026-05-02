"""JSON request handlers — pure functions of ``(store, query_dict) ->
(status, payload_dict)``.

Trivial to unit-test without an HTTP server. The server in
``server.py`` decodes the query string, dispatches by path, and
serialises the dict to JSON.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, List, Mapping, Tuple

from smartmet_top.snapshots.active import ActiveSnapshot
from smartmet_top.snapshots.caches import CachesSnapshot
from smartmet_top.snapshots.flame import FlameSnapshot, MODES as FLAME_MODES
from smartmet_top.snapshots.keys import KeysSnapshot
from smartmet_top.snapshots.logs import LogsSnapshot
from smartmet_top.snapshots.network import NetworkSnapshot
from smartmet_top.snapshots.overview import OverviewSnapshot
from smartmet_top.snapshots.plugins import PluginsSnapshot
from smartmet_top.snapshots.proc import ProcSnapshot
from smartmet_top.snapshots.services import ServicesSnapshot
from smartmet_top.snapshots.urls import URLsSnapshot
from smartmet_top.sources.logparse import parse_iso, strip_query
from smartmet_top.sources.smartmet_filter import (
    THREAD_CLASS_ALL,
    THREAD_CLASS_BACKGROUND,
    THREAD_CLASS_REQUEST,
)


# ---- query-string coercion --------------------------------------------------

def _bool(v, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _table_envelope(snap, headers, rows, **extra) -> dict:
    out = {
        "name": snap.name,
        "headers": list(headers),
        "rows": [dict(zip(headers, r)) for r in rows],
    }
    out.update(extra)
    return out


# ---- meta -------------------------------------------------------------------

def health(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    return 200, {
        "ok": True,
        "log_paths": list(store.logtail_status_per_path.keys())
                      if hasattr(store, "logtail_status_per_path") else [],
        "admin_hosts": list(store.admin_hosts),
        "logtail_status": getattr(store, "logtail_status", "unknown"),
    }


def hosts(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    out = []
    for h in store.admin_hosts:
        out.append({
            "host": h,
            "role": store.host_role.get(h, "unknown"),
            "status": store.admin_status.get(h, "unknown"),
        })
    return 200, {"hosts": out}


def panels(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    """Tells the client which panel ids exist and what their endpoints
    are. The frontend uses this to build the tab strip without
    hard-coding the list.
    """
    return 200, {
        "panels": [
            {"id": "overview", "title": "Overview"},
            {"id": "plugins",  "title": "Plugins"},
            {"id": "urls",     "title": "URLs"},
            {"id": "caches",   "title": "Caches"},
            {"id": "services", "title": "Services"},
            {"id": "active",   "title": "Active"},
            {"id": "keys",     "title": "API Keys"},
            {"id": "proc",     "title": "Proc"},
            {"id": "network",  "title": "Network"},
            {"id": "flame",    "title": "Flame"},
            {"id": "logs",     "title": "Logs"},
        ],
    }


# ---- URLs -------------------------------------------------------------------

def urls_table(store, qs):
    window_min = _int(qs.get("window"), 5)
    sort = qs.get("sort", "p95")
    reverse = _bool(qs.get("reverse"), default=True)
    filter_str = qs.get("filter", "")
    headers, rows = URLsSnapshot.table(
        store, window_min=window_min, sort=sort,
        reverse=reverse, filter_str=filter_str,
    )
    return 200, _table_envelope(URLsSnapshot, headers, rows,
                                window_min=window_min, sort=sort,
                                reverse=reverse, filter=filter_str)


def urls_detail(store, qs):
    url = qs.get("url", "")
    if not url:
        return 400, {"error": "missing 'url' query parameter"}
    return 200, URLsSnapshot.detail(
        store, url, window_min=_int(qs.get("window"), 5))


def urls_chart(store, qs):
    url = qs.get("url", "")
    if not url:
        return 400, {"error": "missing 'url' query parameter"}
    return 200, URLsSnapshot.chart(
        store, url,
        window_min=_int(qs.get("window"), 60),
        metric=qs.get("metric", "mean_ms"))


# ---- Overview ---------------------------------------------------------------

def overview_table(store, qs):
    headers, rows = OverviewSnapshot.table(store)
    return 200, _table_envelope(OverviewSnapshot, headers, rows)


def overview_chart(store, qs):
    return 200, OverviewSnapshot.chart(
        store,
        metric=qs.get("metric", "mean_ms"),
        minutes=_int(qs.get("minutes"), 0))


# ---- Plugins ----------------------------------------------------------------

def plugins_table(store, qs):
    label = qs.get("window", "60s")
    sort = qs.get("sort", "rps")
    reverse = _bool(qs.get("reverse"), default=True)
    filter_str = qs.get("filter", "")
    hide_idle = _bool(qs.get("hide_idle"), default=True)
    headers, rows = PluginsSnapshot.table(
        store, window_label=label, sort=sort,
        reverse=reverse, filter_str=filter_str, hide_idle=hide_idle,
    )
    return 200, _table_envelope(PluginsSnapshot, headers, rows,
                                window=label, sort=sort, reverse=reverse,
                                filter=filter_str, hide_idle=hide_idle)


def plugins_trends(store, qs):
    return 200, PluginsSnapshot.trends(
        store,
        window_label=qs.get("window", "60s"),
        filter_str=qs.get("filter", ""),
        hide_idle=_bool(qs.get("hide_idle"), default=True))


# ---- Caches -----------------------------------------------------------------

def caches_table(store, qs):
    headers, rows = CachesSnapshot.table(store)
    return 200, _table_envelope(CachesSnapshot, headers, rows)


def caches_trends(store, qs):
    return 200, CachesSnapshot.trends(
        store,
        metric=qs.get("metric", "hits_per_min"),
        samples=_int(qs.get("samples"), 30))


def caches_cluster_chart(store, qs):
    """Per-backend trend for one cache. Reads from store.cache_history
    (already populated by the per-host admin polling) — no extra HTTP
    fetches, in contrast to /api/cluster/urls/chart."""
    name = qs.get("cache_name", "")
    if not name:
        # No cache picked yet — return the list of available caches so
        # the UI can populate its picker on first paint.
        return 200, {
            "cache_names": CachesSnapshot.cluster_cache_names(store),
            "series": [], "cache_name": "",
        }
    payload = CachesSnapshot.cluster_chart_per_host(
        store,
        cache_name=name,
        metric=qs.get("metric", "hits_per_min"),
        samples=_int(qs.get("samples"), 150))
    payload["cache_names"] = CachesSnapshot.cluster_cache_names(store)
    return 200, payload


# ---- Services ---------------------------------------------------------------

def services_table(store, qs):
    headers, rows = ServicesSnapshot.table(store)
    return 200, _table_envelope(ServicesSnapshot, headers, rows)


def services_trends(store, qs):
    return 200, ServicesSnapshot.trends(
        store,
        metric=qs.get("metric", "req_per_min"),
        samples=_int(qs.get("samples"), 30))


def services_cluster_chart(store, qs):
    """Per-backend trend for one service handler."""
    handler = qs.get("handler", "")
    if not handler:
        return 200, {
            "handlers": ServicesSnapshot.cluster_handler_names(store),
            "series": [], "handler": "",
        }
    payload = ServicesSnapshot.cluster_chart_per_host(
        store,
        handler=handler,
        metric=qs.get("metric", "req_per_min"),
        samples=_int(qs.get("samples"), 150))
    payload["handlers"] = ServicesSnapshot.cluster_handler_names(store)
    return 200, payload


# ---- Active -----------------------------------------------------------------

def active_table(store, qs):
    headers, rows = ActiveSnapshot.table(store)
    return 200, _table_envelope(ActiveSnapshot, headers, rows)


def active_chart(store, qs):
    # ?multi=1 returns one series per backend (for cluster-mode line
    # overlay); the default is the aggregated cluster-total series.
    if _bool(qs.get("multi"), default=False):
        return 200, ActiveSnapshot.chart_per_host(store)
    return 200, ActiveSnapshot.chart(store)


# ---- Keys -------------------------------------------------------------------

def keys_table(store, qs):
    headers, rows = KeysSnapshot.table(
        store,
        window_min=_int(qs.get("window"), 60),
        sort=qs.get("sort", "count"),
        reverse=_bool(qs.get("reverse"), default=True),
        filter_str=qs.get("filter", ""),
    )
    return 200, _table_envelope(KeysSnapshot, headers, rows,
                                window_min=_int(qs.get("window"), 60),
                                sort=qs.get("sort", "count"))


def keys_detail(store, qs):
    apikey = qs.get("apikey", "")
    if not apikey:
        return 400, {"error": "missing 'apikey' query parameter"}
    return 200, KeysSnapshot.detail(
        store, apikey,
        window_min=_int(qs.get("window"), 60),
        top_urls=_int(qs.get("top_urls"), 50))


# ---- Proc -------------------------------------------------------------------

def proc_table(store, qs):
    headers, rows = ProcSnapshot.table(store)
    return 200, _table_envelope(ProcSnapshot, headers, rows)


def proc_pids(store, qs):
    return 200, {"pids": ProcSnapshot.list_pids(store),
                 "default": store.proc_default_pid()}


def proc_detail(store, qs):
    pid = qs.get("pid")
    return 200, ProcSnapshot.detail(store, _int(pid, 0) if pid else None)


# ---- Network ----------------------------------------------------------------

def network_table(store, qs):
    headers, rows = NetworkSnapshot.table(store)
    return 200, _table_envelope(NetworkSnapshot, headers, rows)


def network_detail(store, qs):
    return 200, NetworkSnapshot.detail(store)


# ---- Flame ------------------------------------------------------------------

def _norm_thread_class(qs) -> str:
    v = (qs.get("thread") or qs.get("thread_class")
         or THREAD_CLASS_ALL).lower()
    if v in (THREAD_CLASS_REQUEST, "request"):
        return THREAD_CLASS_REQUEST
    if v in (THREAD_CLASS_BACKGROUND, "background", "bg"):
        return THREAD_CLASS_BACKGROUND
    return THREAD_CLASS_ALL


def flame_status(store, qs):
    return 200, FlameSnapshot.status(store)


def flame_tree(store, qs):
    pid = qs.get("pid")
    return 200, FlameSnapshot.tree(
        store,
        pid=_int(pid, 0) if pid else None,
        mode=qs.get("mode", "on-cpu"),
        smartmet_only=_bool(qs.get("smartmet_only"), default=True),
        thread_class=_norm_thread_class(qs),
        max_stacks=_int(qs.get("max_stacks"), 50_000))


def flame_top(store, qs):
    pid = qs.get("pid")
    return 200, FlameSnapshot.top_symbols(
        store,
        pid=_int(pid, 0) if pid else None,
        mode=qs.get("mode", "on-cpu"),
        smartmet_only=_bool(qs.get("smartmet_only"), default=True),
        thread_class=_norm_thread_class(qs),
        n=_int(qs.get("n"), 25))


# ---- Logs -------------------------------------------------------------------

def logs_stream(store, qs):
    return 200, LogsSnapshot.stream(
        store,
        n=_int(qs.get("n"), 500),
        filter_str=qs.get("filter", ""))


# ---- Cluster (registry-scope, not store-scope) ------------------------------

def clusters_list(registry, qs):
    """List configured clusters with discovery status. Used by the UI's
    cluster selector dropdown — populates one entry per cluster plus the
    pseudo-entry "(single host)" if a non-empty single-host store
    coexists with the registry."""
    if registry is None:
        return 200, {"clusters": []}
    out = []
    for ctx in registry.all():
        backends = ctx.last_backends or []
        alive = sum(1 for b in backends if b.alive)
        out.append({
            "name": ctx.config.name,
            "frontend_url": ctx.config.frontend_url,
            "discovery_status": ctx.discovery_status,
            "backend_count": len(backends),
            "alive_count": alive,
            "polling_count": len(ctx.tasks),
        })
    return 200, {"clusters": out}


def cluster_topology(registry, qs):
    """Backend list for one cluster with handler service mix. Powers
    the topology card (Phase 3) — backend prefixes, alive/down state,
    and which services they host (so c2-c5 cluster as 'timeseries' nodes
    while v1.q3 / v2.q3 cluster as 'q3' nodes)."""
    name = qs.get("cluster", "")
    if registry is None or not name:
        return 400, {"error": "missing cluster query parameter"}
    ctx = registry.get(name)
    if ctx is None:
        return 404, {"error": "no such cluster", "name": name}
    backends = []
    for b in ctx.last_backends:
        backends.append({
            "prefix": b.prefix,
            "alive": b.alive,
            "handlers": b.handlers,
        })
    return 200, {
        "name": ctx.config.name,
        "frontend_url": ctx.config.frontend_url,
        "discovery_status": ctx.discovery_status,
        "backends": backends,
        "polling_prefixes": sorted(ctx.tasks.keys()),
    }


# ---- Cluster URLs chart (on-demand parallel lastrequests) -------------------
#
# The data path:
#
#   1. Parallel fetch of /admin?what=lastrequests&minutes=N from each
#      backend in the cluster (one HTTP thread per backend; a cluster
#      has ≤10 backends in practice).
#   2. Bucket each backend's rows by minute on the URL the operator
#      clicked. The URL match is exact on the strip_query-cleaned path,
#      matching the keying that ``_ingest_lastrequests`` already uses
#      so the URL string from the cluster URLs table will match here.
#   3. Reduce each minute's durations to a single value per the
#      operator's chosen metric (p50/p95/mean/max/count).
#
# Why parallel HTTP fetches and not "read from store.lastrequests[host]":
# the store snapshot only holds the most recent admin poll (with
# minutes=1) — barely a minute of history. A meaningful chart needs 30+
# minutes, which we get by overriding minutes= on this single call. The
# fetches are kicked off concurrently so wall time ≈ slowest backend,
# not sum of backends. Result: ~1 s for a six-backend cluster.

_LASTREQ_TIMEOUT = 5.0  # seconds; per-backend HTTP timeout


def _fetch_lastreq_rows(admin_url: str, minutes: int) -> List[dict]:
    """Synchronous fetch of /admin?what=lastrequests&minutes=N. Returns
    the row list (or [] on any failure — errors are reported per-backend
    in the response envelope, not as exceptions, so a single misbehaving
    backend does not fail the whole chart)."""
    full = (admin_url.rstrip("/")
            + f"?what=lastrequests&format=json&minutes={int(minutes)}")
    req = urllib.request.Request(
        full, headers={"User-Agent": "smartmet-webmon-cluster-chart"})
    # Bypass any HTTP_PROXY env: the request is to internal admin
    # endpoints only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=_LASTREQ_TIMEOUT) as resp:
        body = resp.read()
    parsed = json.loads(body)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return []


def _aggregate_minute(durs: List[float], metric: str) -> float:
    if not durs:
        return 0.0
    durs.sort()
    n = len(durs)
    if metric == "count":
        return float(n)
    if metric == "mean_ms":
        return sum(durs) / n
    if metric == "p50_ms":
        return durs[n // 2]
    if metric == "p95_ms":
        return durs[min(n - 1, int(0.95 * n))]
    if metric == "max_ms":
        return durs[-1]
    return 0.0


def _resolve_cluster(registry, qs):
    """Common preflight for cluster-scope endpoints. Returns (ctx, error)
    where error is a (status, body) tuple or None on success."""
    name = qs.get("cluster", "")
    if registry is None or not name:
        return None, (400, {"error": "missing cluster query parameter"})
    ctx = registry.get(name)
    if ctx is None:
        return None, (404, {"error": "no such cluster", "name": name})
    return ctx, None


def _fetch_cluster_lastreqs(ctx, minutes: int):
    """Parallel-fetch /admin?what=lastrequests&minutes=N from every
    currently-polled backend in a cluster. Returns
    ``(prefixes, rows_by_prefix, errors)`` — prefixes is the sorted
    list, rows_by_prefix maps prefix → row list (or [] on per-backend
    failure), errors maps the failing prefixes to their reason
    strings."""
    prefixes = sorted(ctx.tasks.keys())
    if not prefixes:
        return prefixes, {}, {}
    pattern = ctx.config.admin_url_pattern

    def fetch_one(prefix: str):
        admin_url = pattern.format(prefix=prefix)
        try:
            return prefix, _fetch_lastreq_rows(admin_url, minutes), ""
        except urllib.error.URLError as e:
            return prefix, [], f"unreachable: {e.reason}"
        except Exception as e:  # noqa: BLE001
            return prefix, [], f"{type(e).__name__}: {e}"

    rows_by_prefix: dict = {}
    errors: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, len(prefixes))) as ex:
        futs = [ex.submit(fetch_one, p) for p in prefixes]
        for fut in as_completed(futs):
            prefix, rows, err = fut.result()
            rows_by_prefix[prefix] = rows
            if err:
                errors[prefix] = err
    return prefixes, rows_by_prefix, errors


def _row_url(r):
    """Extract the (query-stripped) URL path from a lastrequests row."""
    req_str = r.get("RequestString") or r.get("requeststring") or ""
    if " " in req_str:
        req_str = req_str.split(" ", 1)[1]
    return strip_query(req_str)


def _row_ts_dur(r):
    """(epoch_seconds, dur_ms) or (None, None) if either parse fails."""
    t_str = r.get("Time") or r.get("time") or ""
    dur_str = r.get("Duration") or r.get("duration") or "0"
    if not t_str:
        return None, None
    try:
        ts = parse_iso(t_str)
        dur = float(dur_str)
    except (ValueError, TypeError):
        return None, None
    return (ts or None), dur


def _plugin_label(url_path: str) -> str:
    """Leading non-empty path segment. ``/timeseries?...`` → "timeseries".
    Empty / "/" → "". Used as the cluster-mode plugin grouping key
    (matches what the Plugins panel calls a "plugin")."""
    if not url_path or url_path == "/":
        return ""
    parts = url_path.split("/", 2)
    # parts[0] is the empty string before the leading slash.
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return ""


def _build_cluster_chart(prefixes, rows_by_prefix, *,
                         minutes: int, metric: str,
                         row_matches, errors=None) -> dict:
    """Bucket each backend's matching rows by minute and reduce to a
    metric value per minute. ``row_matches(row)`` is the per-row filter
    (returns True if the row should be counted in the chosen entity's
    series). Returns the response envelope shared by all four
    on-demand cluster chart endpoints (URLs / Plugins / Keys /
    Overview)."""
    now_min = int(time.time() // 60)
    series = []
    for prefix in prefixes:
        rows = rows_by_prefix.get(prefix, [])
        buckets: dict = {}
        for r in rows:
            if not row_matches(r):
                continue
            ts, dur = _row_ts_dur(r)
            if ts is None or dur is None:
                continue
            mb = int(ts // 60)
            buckets.setdefault(mb, []).append(dur)
        values = [
            _aggregate_minute(buckets.get(m, []), metric)
            for m in range(now_min - minutes + 1, now_min + 1)
        ]
        series.append({"label": prefix, "values": values})
    return {
        "metric": metric,
        "minutes": minutes,
        "step_seconds": 60.0,
        "last_ts": float(now_min * 60),
        "series": series,
        "errors": dict(errors or {}),
    }


def cluster_urls_chart(registry, qs):
    """Per-backend per-minute time series for one URL across a cluster.

    Drives the URL drill-down modal's chart in cluster mode (multi-line
    overlay, one line per backend). Single-host mode keeps using the
    store-based ``/api/urls/chart`` endpoint.
    """
    ctx, err = _resolve_cluster(registry, qs)
    if err: return err
    target = qs.get("url", "")
    if not target:
        return 400, {"error": "missing 'url' query parameter"}
    minutes = max(1, _int(qs.get("minutes"), 60))
    metric = qs.get("metric", "p95_ms")

    prefixes, rows_by_prefix, errors = _fetch_cluster_lastreqs(ctx, minutes)
    chart = _build_cluster_chart(
        prefixes, rows_by_prefix,
        minutes=minutes, metric=metric, errors=errors,
        row_matches=lambda r: _row_url(r) == target,
    )
    chart["url"] = target
    return 200, chart


def cluster_plugins_chart(registry, qs):
    """Per-backend trend for one plugin (leading URL path segment).

    Same on-demand parallel data path as cluster_urls_chart. The
    grouping key is the first non-empty path segment of each row's
    URL: ``/timeseries?...`` → ``timeseries``, ``/wms?...`` → ``wms``.
    Without a ``plugin`` query, returns the union of plugin names
    observed in the most recent admin polls so the UI can populate
    its picker without a second request.
    """
    ctx, err = _resolve_cluster(registry, qs)
    if err: return err
    target = qs.get("plugin", "")
    minutes = max(1, _int(qs.get("minutes"), 60))
    metric = qs.get("metric", "p95_ms")

    prefixes, rows_by_prefix, errors = _fetch_cluster_lastreqs(ctx, minutes)

    # Always derive the available-plugin list from this fetch so the UI
    # picker stays accurate even if a backend's plugin set changed.
    plugin_names: set = set()
    for rows in rows_by_prefix.values():
        for r in rows:
            p = _plugin_label(_row_url(r))
            if p:
                plugin_names.add(p)

    if not target:
        return 200, {
            "plugin": "",
            "plugin_names": sorted(plugin_names),
            "minutes": minutes,
            "metric": metric,
            "step_seconds": 60.0,
            "last_ts": float(int(time.time() // 60) * 60),
            "series": [],
            "errors": dict(errors),
        }

    chart = _build_cluster_chart(
        prefixes, rows_by_prefix,
        minutes=minutes, metric=metric, errors=errors,
        row_matches=lambda r: _plugin_label(_row_url(r)) == target,
    )
    chart["plugin"] = target
    chart["plugin_names"] = sorted(plugin_names)
    return 200, chart


def cluster_keys_chart(registry, qs):
    """Per-backend trend for one API key.

    Filters lastrequests by the ``Apikey`` field. Without an
    ``apikey`` query, returns the union of API keys observed
    (excluding the dash placeholder ``-``) for the picker.
    """
    ctx, err = _resolve_cluster(registry, qs)
    if err: return err
    target = qs.get("apikey", "")
    minutes = max(1, _int(qs.get("minutes"), 60))
    metric = qs.get("metric", "p95_ms")

    prefixes, rows_by_prefix, errors = _fetch_cluster_lastreqs(ctx, minutes)

    keys: set = set()
    for rows in rows_by_prefix.values():
        for r in rows:
            k = (r.get("Apikey") or r.get("apikey") or "").strip()
            if k and k != "-":
                keys.add(k)

    if not target:
        return 200, {
            "apikey": "",
            "apikeys": sorted(keys),
            "minutes": minutes,
            "metric": metric,
            "step_seconds": 60.0,
            "last_ts": float(int(time.time() // 60) * 60),
            "series": [],
            "errors": dict(errors),
        }

    def row_matches(r):
        k = (r.get("Apikey") or r.get("apikey") or "").strip()
        return k == target

    chart = _build_cluster_chart(
        prefixes, rows_by_prefix,
        minutes=minutes, metric=metric, errors=errors,
        row_matches=row_matches,
    )
    chart["apikey"] = target
    chart["apikeys"] = sorted(keys)
    return 200, chart


def cluster_overview_chart(registry, qs):
    """Per-backend trend across the whole cluster — one line per
    backend, no entity filter. Same parallel-fetch as the URLs chart
    but the row filter is "everything", and one HTTP fetch produces
    every requested metric.

    ``metrics`` may be a comma-separated list (e.g.
    ``count,mean_ms,p95_ms``); the response then carries a
    ``charts`` map ``{metric: {series, last_ts, ...}}`` so the
    Overview panel can paint all five mini-charts from a single
    parallel fetch — N backend HTTP calls, not 5N.
    """
    ctx, err = _resolve_cluster(registry, qs)
    if err: return err
    minutes = max(1, _int(qs.get("minutes"), 60))
    metrics_param = qs.get("metrics", "") or qs.get("metric", "count")
    metrics = [m.strip() for m in metrics_param.split(",") if m.strip()]
    if not metrics:
        metrics = ["count"]

    prefixes, rows_by_prefix, errors = _fetch_cluster_lastreqs(ctx, minutes)

    if len(metrics) == 1:
        return 200, _build_cluster_chart(
            prefixes, rows_by_prefix,
            minutes=minutes, metric=metrics[0], errors=errors,
            row_matches=lambda r: True,
        )

    charts = {
        m: _build_cluster_chart(
            prefixes, rows_by_prefix,
            minutes=minutes, metric=m, errors=errors,
            row_matches=lambda r: True,
        )
        for m in metrics
    }
    return 200, {
        "minutes": minutes,
        "step_seconds": 60.0,
        "charts": charts,
        "errors": dict(errors),
    }


# ---- routing ----------------------------------------------------------------

# Cluster-scope endpoints — these get the ``ClusterRegistry`` directly
# (not a per-cluster Store) because they introspect the registry
# itself.
CLUSTER_ROUTES = {
    "/clusters":              clusters_list,
    "/cluster/topology":      cluster_topology,
    "/cluster/urls/chart":    cluster_urls_chart,
    "/cluster/plugins/chart": cluster_plugins_chart,
    "/cluster/keys/chart":    cluster_keys_chart,
    "/cluster/overview/chart": cluster_overview_chart,
}

ROUTES = {
    # meta
    "/health":            health,
    "/hosts":             hosts,
    "/panels":            panels,
    # URLs
    "/urls":              urls_table,
    "/urls/detail":       urls_detail,
    "/urls/chart":        urls_chart,
    # Overview
    "/overview":          overview_table,
    "/overview/chart":    overview_chart,
    # Plugins
    "/plugins":           plugins_table,
    "/plugins/trends":    plugins_trends,
    # Caches
    "/caches":               caches_table,
    "/caches/trends":        caches_trends,
    "/caches/cluster_chart": caches_cluster_chart,
    # Services
    "/services":               services_table,
    "/services/trends":        services_trends,
    "/services/cluster_chart": services_cluster_chart,
    # Active
    "/active":            active_table,
    "/active/chart":      active_chart,
    # API keys
    "/keys":              keys_table,
    "/keys/detail":       keys_detail,
    # Proc
    "/proc":              proc_table,
    "/proc/pids":         proc_pids,
    "/proc/detail":       proc_detail,
    # Network
    "/network":           network_table,
    "/network/detail":    network_detail,
    # Flame
    "/flame/status":      flame_status,
    "/flame/tree":        flame_tree,
    "/flame/top":         flame_top,
    # Logs
    "/logs":              logs_stream,
}
