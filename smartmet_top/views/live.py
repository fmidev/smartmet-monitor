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
        self._regions = [
            ("plugins", PluginsPanel()),
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
