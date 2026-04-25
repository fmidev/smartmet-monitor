"""CompositeView base — a Panel that hosts multiple sub-panels.

Layout is delegated to a `geometry(H, W)` method that returns a list of
`(top, left, height, width)` rects, one per sub-panel. Sub-panels are
drawn into derwin'd sub-windows of the parent, so each panel's existing
draw code (which uses `getmaxyx` / `safe_addstr`) keeps working without
modification.

In v0.3 composite views are **display-only** — operator key input goes
to dedicated single-panel views (URLs, Graphs, Proc, ...). This avoids
the focus-management problem on day one; we can add per-region focus
later once the layouts have stabilised from real use.
"""

from __future__ import annotations

import curses
from typing import List, Tuple

from .. import theme
from ..panels.base import Panel, safe_addstr


class CompositeView(Panel):
    """Stacks multiple sub-panels in a fixed grid.

    Subclasses populate `self._regions` (a list of `(label, panel)`
    tuples) and override `geometry(H, W)`. The default `draw` uses
    those to derwin one sub-window per region and forwards the call.
    """

    # Minimum sub-region size. Below this we skip the region rather
    # than render garbage; the operator can drop into the dedicated
    # single-panel view to see the data on a tiny terminal.
    MIN_REGION_H = 4
    MIN_REGION_W = 20

    def __init__(self) -> None:
        # Subclass populates these.
        self._regions: List[Tuple[str, Panel]] = []

    def geometry(self, H: int, W: int) -> List[Tuple[int, int, int, int]]:
        """Return rects for each region: [(top, left, h, w), ...].

        Default is a vertical stack with equal share. Override for
        non-trivial layouts.
        """
        n = len(self._regions)
        if n == 0:
            return []
        each = max(self.MIN_REGION_H, H // n)
        return [
            (i * each, 0, each if i < n - 1 else max(self.MIN_REGION_H, H - each * (n - 1)), W)
            for i in range(n)
        ]

    def draw(self, win, store) -> None:
        H, W = win.getmaxyx()
        rects = self.geometry(H, W)
        for (label, panel), rect in zip(self._regions, rects):
            top, left, h, w = rect
            if h < self.MIN_REGION_H or w < self.MIN_REGION_W:
                continue
            try:
                sub = win.derwin(h, w, top, left)
            except curses.error:
                # Window doesn't fit — skip rather than crash.
                continue
            try:
                panel.draw(sub, store)
            except Exception as e:
                safe_addstr(sub, 0, 0,
                            f" {label}: {type(e).__name__}: {e}",
                            theme.attr(theme.P_BAD, curses.A_BOLD))

    def handle_key(self, key, store) -> bool:
        # Display-only in v0.3 — keys fall through to the global handler
        # so the operator can switch views or use export shortcuts.
        return False

    def export_snapshot(self, store):
        # Default to the first region's snapshot. Subclasses can override
        # to produce a combined export.
        if self._regions:
            return self._regions[0][1].export_snapshot(store)
        return None, None
