"""Flame snapshot — folded stacks + top symbols, per-mode.

CSV ``table()`` returns empty (the curses panel declines too — flame
trees aren't tabular). The web view consumes ``tree()`` for the
interactive rectangle view and ``top_symbols()`` for the ranked
function list shown alongside.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from ..panels.flame import _is_lock_stack
from ..sources.smartmet_filter import (
    THREAD_CLASS_ALL,
    collapse_to_smartmet,
    keep_for_thread_class,
)


# Modes mirror panels/flame.py:_MODES — kept here so the web client
# learns the option set from the same place that drives smtop.
MODES = ("on-cpu", "off-cpu", "off-cpu-locks", "pagefault",
         "wakeup", "blockflame", "malloc")


def _raw_stacks(store, pid: int, mode: str
                ) -> List[Tuple[Tuple[str, ...], int]]:
    """Return ``(stack, weight)`` pairs for ``pid`` in ``mode``.

    On-CPU / page-fault / wakeup / block-I/O are sample-count weighted
    (weight = 1 per stack). Off-CPU is microsecond-blocked. Malloc is
    bytes-allocated.
    """
    if mode == "on-cpu":
        pd = store.perfdata.get(pid)
        if pd is None:
            return []
        return [(s, 1) for s in pd.recent_stacks]
    if mode in ("off-cpu", "off-cpu-locks"):
        items = store.offcpu_recent_stacks(pid)
        if mode == "off-cpu-locks":
            items = [(s, w) for s, w in items if _is_lock_stack(s)]
        return list(items)
    if mode == "pagefault":
        return [(s, 1) for s in store.pagefault_recent_stacks(pid)]
    if mode == "wakeup":
        return [(s, 1) for s in store.wakeup_recent_stacks(pid)]
    if mode == "blockflame":
        return [(s, 1) for s in store.blockflame_recent_stacks(pid)]
    if mode == "malloc":
        return list(store.malloc_recent_stacks(pid))
    return []


def _filter(items: Iterable[Tuple[Tuple[str, ...], int]], *,
            smartmet_only: bool, thread_class: str
            ) -> List[Tuple[Tuple[str, ...], int]]:
    out = []
    for stack, weight in items:
        if not keep_for_thread_class(stack, thread_class):
            continue
        if smartmet_only:
            collapsed = collapse_to_smartmet(stack)
            if collapsed is None:
                continue
            stack = collapsed
        out.append((stack, weight))
    return out


def _fold(items: Iterable[Tuple[Tuple[str, ...], int]]
          ) -> Dict[Tuple[str, ...], int]:
    """Aggregate identical stacks. The result is small relative to the
    raw event stream — number of unique paths through the call graph.
    """
    folded: Dict[Tuple[str, ...], int] = {}
    for stack, weight in items:
        if not stack or weight <= 0:
            continue
        folded[stack] = folded.get(stack, 0) + int(weight)
    return folded


class FlameSnapshot:
    name = "flame"
    modes: Tuple[str, ...] = MODES

    @staticmethod
    def table(store):
        return [], []

    @staticmethod
    def status(store) -> dict:
        """Tell the client which modes have data right now plus the
        backend hints / install messages from the perf-capability
        probe. Used to grey out unavailable modes in the UI.

        The ``*_status`` fields are panel-friendly (one short line, fits
        the panel header even after truncation). When the underlying
        sampler hits a multi-line error — perf's stderr is typically
        paragraph-shaped and the first line is just "Error:" with the
        diagnostic on the lines below — the full text is mirrored into
        ``perf_last_error``. Without that, operators chase the real
        error through journalctl instead of seeing it in the dashboard
        where they're already looking.
        """
        out = {
            "perf_enabled": bool(store.perf_enabled),
            "perf_status": getattr(store, "perf_status", ""),
            "perf_last_error": getattr(store, "perf_last_error", ""),
            "offcpu_status": getattr(store, "offcpu_status", ""),
            "pagefault_status": getattr(store, "pagefault_status", ""),
            "wakeup_status": getattr(store, "wakeup_status", ""),
            "blockflame_status": getattr(store, "blockflame_status", ""),
            "malloc_status": getattr(store, "malloc_status", ""),
            "biolat_status": getattr(store, "biolat_status", ""),
            "runqlat_status": getattr(store, "runqlat_status", ""),
            "perfstat_status": getattr(store, "perfstat_status", ""),
            "modes": [],
        }
        for mode in MODES:
            sample_count = 0
            if mode == "on-cpu":
                for pd in store.perfdata.values():
                    sample_count += len(pd.recent_stacks)
            elif mode in ("off-cpu", "off-cpu-locks"):
                for od in store.offcpu_data.values():
                    sample_count += len(od.recent_stacks)
            elif mode == "pagefault":
                for pd in store.pagefault_data.values():
                    sample_count += len(pd.recent_stacks)
            elif mode == "wakeup":
                for pd in store.wakeup_data.values():
                    sample_count += len(pd.recent_stacks)
            elif mode == "blockflame":
                for pd in store.blockflame_data.values():
                    sample_count += len(pd.recent_stacks)
            elif mode == "malloc":
                for pd in getattr(store, "mallocflame_data", {}).values():
                    sample_count += len(pd.recent_stacks)
            out["modes"].append({"mode": mode,
                                 "samples": int(sample_count)})
        return out

    @staticmethod
    def tree(store, *, pid: Optional[int] = None,
             mode: str = "on-cpu",
             smartmet_only: bool = False,
             thread_class: str = THREAD_CLASS_ALL,
             max_stacks: int = 50_000) -> dict:
        """Folded stacks for the interactive flame view.

        Returns ``{mode, pid, total_weight, stacks: [{frames, weight}]}``.
        ``frames`` is root → leaf (the same orientation curses
        `_build_flame_tree` expects). ``weight`` is microseconds for
        off-CPU, bytes for malloc, sample count otherwise.
        """
        if pid is None:
            pid = store.proc_default_pid()
        if pid is None or mode not in MODES:
            return {"mode": mode, "pid": pid, "total_weight": 0,
                    "stacks": []}
        raw = _raw_stacks(store, pid, mode)
        # Cap before filtering to bound work; the bounded ring on the
        # store side already keeps the most recent samples.
        if len(raw) > max_stacks:
            raw = raw[-max_stacks:]
        filtered = _filter(raw, smartmet_only=smartmet_only,
                           thread_class=thread_class)
        folded = _fold(filtered)
        total = sum(folded.values())
        stacks = [{"frames": list(stack), "weight": w}
                  for stack, w in folded.items()]
        # Sort heaviest-first so a client doing top-N has a fast path.
        stacks.sort(key=lambda s: -s["weight"])
        return {
            "mode": mode,
            "pid": pid,
            "smartmet_only": smartmet_only,
            "thread_class": thread_class,
            "total_weight": total,
            "stacks": stacks,
        }

    @staticmethod
    def top_symbols(store, *, pid: Optional[int] = None,
                    mode: str = "on-cpu",
                    smartmet_only: bool = False,
                    thread_class: str = THREAD_CLASS_ALL,
                    n: int = 25) -> dict:
        """Aggregate by leaf frame across the filtered stack set.

        Mirrors the curses panel's "Top symbols" / "Top wakeup-causing
        functions" / "Top off-CPU leaves" lists below the flame
        rectangle. The unit string is descriptive: ``samples`` for
        sample-count modes, ``microseconds`` for off-CPU, ``bytes``
        for malloc.
        """
        if pid is None:
            pid = store.proc_default_pid()
        if pid is None or mode not in MODES:
            return {"mode": mode, "pid": pid, "rows": []}

        unit = ("microseconds" if mode in ("off-cpu", "off-cpu-locks")
                else "bytes" if mode == "malloc"
                else "samples")
        raw = _raw_stacks(store, pid, mode)
        filtered = _filter(raw, smartmet_only=smartmet_only,
                           thread_class=thread_class)
        leaves: Dict[str, int] = {}
        for stack, weight in filtered:
            leaf = stack[-1]
            leaves[leaf] = leaves.get(leaf, 0) + int(weight)
        ranked = sorted(leaves.items(), key=lambda x: -x[1])[:n]
        total = sum(leaves.values())
        return {
            "mode": mode,
            "pid": pid,
            "unit": unit,
            "total": int(total),
            "rows": [{"symbol": s, "weight": int(w),
                      "pct": round(w / total * 100, 2) if total else 0.0}
                     for s, w in ranked],
        }
