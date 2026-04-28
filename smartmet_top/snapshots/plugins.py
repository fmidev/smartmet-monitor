"""Plugins snapshot — per-source (per access-log file) aggregate stats."""

from __future__ import annotations

from typing import List, Tuple

# (display label, span, resolution)
WINDOWS = (
    ("60s", 60, "second"),
    ("1m",  1,  "minute"),
    ("5m",  5,  "minute"),
    ("15m", 15, "minute"),
    ("60m", 60, "minute"),
)

SORT_KEYS = ("name", "rps", "mean_ms", "p95_ms", "err_pct", "bytes")


def _window_snap(src, span: int, resolution: str):
    if resolution == "second":
        return src.second_window(span)
    return src.minute_window(span)


def collect(store, *, window_label: str = "60s", sort: str = "rps",
            reverse: bool = True, filter_str: str = "",
            hide_idle: bool = True) -> List[Tuple[object, object]]:
    """Pick window by label ("60s","1m","5m","15m","60m"), filter, sort.

    Returns [(SourceStats, window_snapshot)] tuples in the requested
    order. The snapshot is the per-window aggregate (count / hist /
    bytes / errors).
    """
    span, resolution = 60, "second"
    for lbl, sp, res in WINDOWS:
        if lbl == window_label:
            span, resolution = sp, res
            break
    rows: List[Tuple[object, object]] = []
    for src in store.snapshot_sources():
        snap = _window_snap(src, span, resolution)
        if hide_idle and snap.count == 0:
            continue
        if filter_str and filter_str.lower() not in src.label.lower():
            continue
        rows.append((src, snap))

    def keyfn(item):
        src, snap = item
        if sort == "name":
            return src.label
        if sort == "rps":
            return snap.count
        if sort == "mean_ms":
            return snap.hist.mean()
        if sort == "p95_ms":
            return snap.hist.p95()
        if sort == "err_pct":
            return (snap.errors / snap.count * 100) if snap.count else 0.0
        if sort == "bytes":
            return snap.bytes
        return 0

    rev = reverse if sort != "name" else False
    rows.sort(key=keyfn, reverse=rev)
    return rows


def collect_with_autowiden(store, *, window_label: str = "60s",
                           sort: str = "rps", reverse: bool = True,
                           filter_str: str = "", hide_idle: bool = True
                           ) -> Tuple[List[Tuple[object, object]], str]:
    """As collect(), but if the chosen window is empty, fall through to
    the next wider window. Returns (rows, effective_window_label).
    Auto-widening only kicks in when there *are* sources; an
    everything-empty store returns the originally requested label.
    """
    rows = collect(store, window_label=window_label, sort=sort,
                   reverse=reverse, filter_str=filter_str,
                   hide_idle=hide_idle)
    if rows or not store.snapshot_sources():
        return rows, window_label
    labels = [w[0] for w in WINDOWS]
    if window_label not in labels:
        return rows, window_label
    idx = labels.index(window_label)
    for lbl in labels[idx + 1:]:
        widened = collect(store, window_label=lbl, sort=sort,
                          reverse=reverse, filter_str=filter_str,
                          hide_idle=hide_idle)
        if widened:
            return widened, lbl
    return [], window_label


def _series_for(src, span: int, resolution: str, metric: str):
    """Time-series accessor parameterised on resolution."""
    if resolution == "second":
        return list(src.second_series(metric, seconds=span))
    return list(src.minute_series(metric, span))


class PluginsSnapshot:
    name = "plugins"

    @staticmethod
    def table(store, *, window_label: str = "60s", sort: str = "rps",
              reverse: bool = True, filter_str: str = "",
              hide_idle: bool = True):
        rows = collect(store, window_label=window_label, sort=sort,
                       reverse=reverse, filter_str=filter_str,
                       hide_idle=hide_idle)
        # Headers preserve the units used by the previous CSV export
        # (the "_60s" suffix is the default-window label, unchanged so
        # existing CSV consumers don't see a column rename — even
        # though the actual window may be different now).
        headers = [
            "plugin", "rps_60s", "mean_ms_60s", "p50_ms_60s", "p95_ms_60s",
            "max_ms_60s", "bytes_per_sec_60s", "err_pct_60s",
            "requests_60s", "errors_60s",
        ]
        out = []
        for src, snap in rows:
            out.append([
                src.label,
                round(snap.count / 60.0, 3),
                round(snap.hist.mean(), 3),
                round(snap.hist.p50(), 3),
                round(snap.hist.p95(), 3),
                round(snap.hist.max_ms, 3),
                round(snap.bytes / 60.0, 1),
                round(snap.errors / snap.count * 100.0, 3) if snap.count else 0.0,
                snap.count, snap.errors,
            ])
        return headers, out

    @staticmethod
    def trends(store, *, window_label: str = "60s",
               filter_str: str = "", hide_idle: bool = True,
               metrics: Tuple[str, ...] = ("mean_ms", "bytes_mean")):
        """Per-source time-series for the given metrics.

        Returns ``[{label, metrics: {metric: [...values]}}]`` so the web
        view can render two Canvas sparklines per row (the curses
        equivalent shows latency + size sparks per row).
        """
        span, resolution = 60, "second"
        for lbl, sp, res in WINDOWS:
            if lbl == window_label:
                span, resolution = sp, res
                break
        out = []
        for src in store.snapshot_sources():
            if filter_str and filter_str.lower() not in src.label.lower():
                continue
            metric_data = {}
            for metric in metrics:
                metric_data[metric] = [
                    round(float(v), 3)
                    for v in _series_for(src, span, resolution, metric)
                ]
            if hide_idle and not any(any(v for v in vs)
                                     for vs in metric_data.values()):
                continue
            out.append({"label": src.label, "metrics": metric_data})
        return {
            "window_label": window_label,
            "metrics": list(metrics),
            "rows": out,
        }
