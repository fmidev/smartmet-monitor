"""Snapshot providers — toolkit-agnostic data extraction.

Each module here exposes a class with a unique `name` and at least a
``table(store, **opts) -> (headers, rows)`` static method. The flat
tabular shape is consumed by:

  * the curses panel's ``export_snapshot()`` (CSV / JSON dumps)
  * the smwebmon HTTP server's ``/api/<name>`` JSON endpoint

Snapshots may grow ``detail(store, key, **opts) -> dict`` and
``chart(store, **opts) -> dict`` methods as web panels come online.
"""
