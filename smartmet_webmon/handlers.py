"""JSON request handlers.

Pure functions of ``(store, query_dict) -> (status, payload_dict)``
so they're trivial to unit-test without an HTTP server. The server
in ``server.py`` decodes the query string, dispatches by path, and
serialises the dict to JSON.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from smartmet_top.snapshots.urls import URLsSnapshot


def _bool(v, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def health(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    return 200, {
        "ok": True,
        "log_paths": list(store.logtail_status_per_path.keys())
                      if hasattr(store, "logtail_status_per_path") else [],
        "admin_hosts": list(store.admin_hosts),
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


def urls_table(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    window_min = _int(qs.get("window"), 5)
    sort = qs.get("sort", "p95")
    reverse = _bool(qs.get("reverse"), default=True)
    filter_str = qs.get("filter", "")
    headers, rows = URLsSnapshot.table(
        store,
        window_min=window_min,
        sort=sort,
        reverse=reverse,
        filter_str=filter_str,
    )
    # Emit row dicts so the JS doesn't have to know column order.
    rows_out = [dict(zip(headers, r)) for r in rows]
    return 200, {
        "name": URLsSnapshot.name,
        "window_min": window_min,
        "sort": sort,
        "reverse": reverse,
        "filter": filter_str,
        "headers": headers,
        "rows": rows_out,
    }


def urls_detail(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    url = qs.get("url", "")
    if not url:
        return 400, {"error": "missing 'url' query parameter"}
    window_min = _int(qs.get("window"), 5)
    return 200, URLsSnapshot.detail(store, url, window_min=window_min)


def urls_chart(store, qs: Mapping[str, str]) -> Tuple[int, Any]:
    url = qs.get("url", "")
    if not url:
        return 400, {"error": "missing 'url' query parameter"}
    window_min = _int(qs.get("window"), 60)
    metric = qs.get("metric", "mean_ms")
    return 200, URLsSnapshot.chart(store, url, window_min=window_min,
                                    metric=metric)


# Path → handler. The server adds /api prefix.
ROUTES = {
    "/health":       health,
    "/hosts":        hosts,
    "/urls":         urls_table,
    "/urls/detail":  urls_detail,
    "/urls/chart":   urls_chart,
}
