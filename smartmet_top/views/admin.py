"""Admin / health composite view.

Stacks Caches, Services and Active in a single screen so the operator
can answer "is this server healthy?" without tab-switching. Heights
are equal thirds; on small terminals the bottom region drops off
gracefully (CompositeView skips regions below MIN_REGION_H).
"""

from __future__ import annotations

from typing import List, Tuple

from ..panels.active import ActivePanel
from ..panels.caches import CachesPanel
from ..panels.services import ServicesPanel
from .composite import CompositeView


class AdminView(CompositeView):
    name = "Health"
    hotkey = "h"
    help_text = (
        "Server health overview: Caches (top), Services (middle), "
        "Active in-flight requests (bottom). Switch to c/s/a for "
        "scrollable single-panel views."
    )

    def __init__(self) -> None:
        super().__init__()
        self._regions = [
            ("caches", CachesPanel()),
            ("services", ServicesPanel()),
            ("active", ActivePanel()),
        ]

    def geometry(self, H: int, W: int) -> List[Tuple[int, int, int, int]]:
        # Equal thirds with a single divider row between sections so the
        # panel headers (drawn at row 0 of each sub-window) don't collide.
        gap = 1
        usable = max(self.MIN_REGION_H * 3, H - 2 * gap)
        third = max(self.MIN_REGION_H, usable // 3)
        return [
            (0, 0, third, W),
            (third + gap, 0, third, W),
            (2 * third + 2 * gap, 0,
             max(self.MIN_REGION_H, H - (2 * third + 2 * gap)), W),
        ]
