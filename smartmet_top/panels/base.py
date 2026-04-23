"""Panel base class.

A Panel is a full-screen view (minus the chrome — title bar and status
line drawn by App). Panels receive a curses window via draw(win, store)
and handle keys via handle_key(key, store).
"""

from __future__ import annotations

import curses
from typing import Optional


class Panel:
    name: str = "panel"
    hotkey: str = "?"
    help_text: str = ""

    def draw(self, win, store) -> None:  # pragma: no cover
        raise NotImplementedError

    def handle_key(self, key: int, store) -> Optional[str]:
        """Return a command string (or None). Known commands:
          "quit"                - exit the app
          "panel:<name>"        - switch to panel
          "help"                - show help overlay
        """
        return None

    def export_snapshot(self, store):
        """Return (headers, rows) describing what this panel is showing right
        now, for export to CSV/JSON. Panels that cannot be exported return
        (None, None)."""
        return None, None


def safe_addstr(win, y, x, text, attr=0):
    """Write a string clipped to the window width."""
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h:
            return
        avail = w - x - 1
        if avail <= 0:
            return
        # Clip by character count — assumes the chars we use are single-width.
        if len(text) > avail:
            text = text[:avail]
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def write_row(win, y, x, cells, row_attr=0):
    """Write a list of (text, attr) cells left-to-right on one row.

    row_attr is ORed with every cell's attribute. This lets the caller
    apply a whole-row highlight (e.g. A_REVERSE on the selected row)
    without losing per-cell colours.
    """
    try:
        h, w = win.getmaxyx()
    except curses.error:
        return x
    if y < 0 or y >= h:
        return x
    for text, a in cells:
        avail = w - x - 1
        if avail <= 0:
            break
        s = text if len(text) <= avail else text[:avail]
        try:
            win.addstr(y, x, s, a | row_attr)
        except curses.error:
            pass
        x += len(s)
    return x
