"""Live access-log monitoring view.

Combines the per-plugin Graphs panel (top) with the per-URL table
(bottom) so the operator can see "which plugin is busy" and "which
URLs inside that plugin are slow" at the same time. Display-only:
both regions render their default sort/filter; the dedicated `g`
(Graphs) and `u` (URLs) views handle interactive use.
"""

from __future__ import annotations

from typing import List, Tuple

from ..panels.plugins import PluginsPanel
from ..panels.urls import UrlsPanel
from .composite import CompositeView


class LiveView(CompositeView):
    name = "Live"
    # 'i' for the second letter of "Live" — 'l' is taken by Logs.
    hotkey = "i"
    mnemonic_pos = 1
    help_text = (
        "Live access-log monitoring: per-plugin (top) + per-URL (bottom). "
        "Switch to g or u for sortable/filterable interaction."
    )

    def __init__(self) -> None:
        super().__init__()
        # Plugins panel defaults to the 60-second per-second window for
        # live monitoring, but on the Live composite that's empty until
        # 60 s of fresh tail arrives — and the composite is display-only
        # so the operator can't widen it from here. Initial window 2
        # = "5m" (per-minute resolution) is populated by --replay's
        # minute buckets, so the Live view always has something to show.
        # default_cursor=-1 suppresses the cursor highlight on the
        # embedded Plugins panel — a "selected" indicator is misleading
        # in the display-only Live composite where the operator can't
        # actually select anything from this view.
        # default_window_idx=4 (60m) is wider than the dedicated Graphs
        # panel's default — Live is meant to show "what's happening on
        # this host overall" so plugins whose last activity was 10-60
        # minutes ago should still appear; the 5m default crushed the
        # row list down to only the most recently active plugin.
        # default_hide_idle=False so the embedded Plugins shows the
        # full plugin list rather than vanishing down to "the only
        # plugin active in this exact window". Live is the at-a-glance
        # composite — every tailed plugin is there, with a flat row
        # for those that haven't been active. The dedicated Graphs
        # panel (`g`) keeps hide_idle=True for the focused use case.
        self._regions = [
            ("plugins", PluginsPanel(default_window_idx=4,
                                     default_cursor=-1,
                                     default_hide_idle=False)),
            ("urls", UrlsPanel()),
        ]

    def geometry(self, H: int, W: int) -> List[Tuple[int, int, int, int]]:
        # Plugins on top, URLs on bottom. 60/40 split with a 1-row gap
        # so the panel headers are visually separated.
        top_h = max(self.MIN_REGION_H, int(H * 0.60))
        bot_h = max(self.MIN_REGION_H, H - top_h - 1)
        return [
            (0, 0, top_h, W),
            (top_h + 1, 0, bot_h, W),
        ]
