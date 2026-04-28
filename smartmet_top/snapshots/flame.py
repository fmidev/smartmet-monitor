"""Flame snapshot — hierarchical, declines a flat table view.

The flame tree is too deep to express as CSV. The web view will use
``tree(store, mode, ...)`` to serve folded stacks as JSON; for now
``table()`` returns empty so the panel's CSV export remains a no-op.
"""

from __future__ import annotations


class FlameSnapshot:
    name = "flame"

    @staticmethod
    def table(store):
        return [], []
