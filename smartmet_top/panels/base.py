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
