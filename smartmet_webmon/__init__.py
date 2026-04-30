"""smartmet-webmon — browser dashboard for SmartMet Server.

Companion to ``smartmet-monitor``. Imports the same ``Store``,
``sources`` and ``snapshots`` modules used by ``smtop``; exposes a
small HTTP server (with SSE for live updates) over loopback so
operators can tunnel in from a browser.

The first slice ships only the URLs panel as a web view; the
remaining panels port over one at a time, each adding handlers and
optional ``detail`` / ``chart`` methods on the corresponding
``smartmet_top.snapshots`` module.
"""

__version__ = "26.4.30"
