"""JSON request handlers — pure functions of ``(store, query_dict) ->
(status, payload_dict)``.

Trivial to unit-test without an HTTP server. The server in
``server.py`` decodes the query string, dispatches by path, and
serialises the dict to JSON.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

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


# ---- Services ---------------------------------------------------------------

def services_table(store, qs):
    headers, rows = ServicesSnapshot.table(store)
    return 200, _table_envelope(ServicesSnapshot, headers, rows)


def services_trends(store, qs):
    return 200, ServicesSnapshot.trends(
        store,
        metric=qs.get("metric", "req_per_min"),
        samples=_int(qs.get("samples"), 30))


# ---- Active -----------------------------------------------------------------

def active_table(store, qs):
    headers, rows = ActiveSnapshot.table(store)
    return 200, _table_envelope(ActiveSnapshot, headers, rows)


def active_chart(store, qs):
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


# ---- routing ----------------------------------------------------------------

# Cluster-scope endpoints — these get the ``ClusterRegistry`` directly
# (not a per-cluster Store) because they introspect the registry
# itself.
CLUSTER_ROUTES = {
    "/clusters":         clusters_list,
    "/cluster/topology": cluster_topology,
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
    "/caches":            caches_table,
    "/caches/trends":     caches_trends,
    # Services
    "/services":          services_table,
    "/services/trends":   services_trends,
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
