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
    panel_help = """\
Three admin-endpoint panels stacked in equal thirds —
the at-a-glance "is this server healthy?" view. All three
panels are display-only here; for sorting and scrolling
switch to the dedicated single-panel views.

Top — Services:
  Per-handler request rates (last1m, last1h, last24h) and
  mean wall-clock duration. The fastest signal that a
  particular endpoint is taking off or grinding to a halt.

Middle — Active:
  Requests currently in flight, sorted by descending
  duration. Sparkline of in-flight count at the top of the
  region tracks "how many things are happening at once?"
  over the recent admin-poll history.

Bottom — Caches:
  Per-cache hit rate and trend. Cache misery is rarely the
  cause of an active incident (the application would have
  been slow long before), so caches sit at the bottom —
  available for the day-2 "why is this never quite as fast
  as I'd like?" question rather than the day-0 "everything
  is on fire" one.

To sort, scroll, or export, switch to the dedicated
mnemonics:
  s  → Services panel
  a  → Active panel
  c  → Caches panel

On a tall terminal all three regions fit comfortably; on a
small one the bottom region drops off gracefully (the
composite view's geometry skips regions below a minimum
height rather than overlap them).
"""

    def __init__(self) -> None:
        super().__init__()
        # Order top-to-bottom by operational priority: per-handler load
        # (Services) and what's currently in flight (Active) are the
        # signals operators usually look for first. Caches drops to
        # the bottom — useful but rarely the bottleneck on a healthy
        # day.
        self._regions = [
            ("services", ServicesPanel()),
            ("active", ActivePanel()),
            ("caches", CachesPanel()),
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
