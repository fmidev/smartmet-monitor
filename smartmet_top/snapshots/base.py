"""Snapshot contract.

Snapshots are plain classes (no inheritance required) used purely as
a namespace for static methods. The conventions:

  ``name``
      Stable identifier; matches the URL segment used by smwebmon
      (e.g. ``urls`` ➔ ``/api/urls``) and the panel's CSV filename.

  ``table(store, **opts) -> (headers, rows)``
      Flat tabular view of the panel's data. ``headers`` is a list of
      column names; ``rows`` is a list of row sequences whose entries
      align with ``headers`` 1:1. The method must be pure with respect
      to ``store`` — no mutation, no I/O.

  ``detail(store, key, **opts) -> dict`` (optional)
      Drill-down view for a single keyed entity (URL, plugin, host).
      Returns a JSON-serialisable dict.

  ``chart(store, **opts) -> dict`` (optional)
      Time-series payload for a chart, typically
      ``{"x": [...], "series": [{"name": ..., "y": [...]}]}``.

Snapshots that cannot be exported flat (e.g. flame trees) return
``([], [])`` from ``table()`` and rely on a ``detail()`` or ``tree()``
method instead.
"""

from typing import Sequence, Tuple


class SnapshotProvider:
    """Optional base. Inherit only if it makes type hints clearer."""

    name: str = ""

    @staticmethod
    def table(store, **opts) -> Tuple[Sequence[str], Sequence[Sequence]]:
        return [], []
