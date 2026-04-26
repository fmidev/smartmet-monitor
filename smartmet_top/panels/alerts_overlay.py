"""Modal alerts overlay opened with `!`.

This is not a regular Panel — it has no tab, no mnemonic, and is
drawn on top of whatever panel is currently active. The state lives
on the App instance (which alert is selected, whether the overlay is
open at all); this module is just rendering + key dispatch.

Layout:

   row 0 .................................. ┌─ Active alerts (N) ─┐
   row 1                                    │  ⚠ severity title    │
   row 2 (selected)  reverse-video bar       │ < cursor row >       │
   row 3..K          remaining alert rows    │                      │
   row K+1                                   ├──────────────────────┤
   row K+2..h-3      detail of selected      │ multi-line body      │
   row h-2 ........................ footer  │ ↵ jump · d dismiss · │
                                            │ ?  docs · Esc close  │
                                            └──────────────────────┘

Keys (handled here, not by the underlying panel):

   ↑ / ↓     move cursor
   Enter     jump to suggested panel AND mark alert dismissed
   d         dismiss without jumping
   Esc / !   close the overlay (alerts stay active)
"""

from __future__ import annotations

import curses
import time
from typing import List, Optional

from .. import theme
from ..state.alerts import Alert
from .base import safe_addstr, write_label


def _severity_attr(severity: str) -> int:
    if severity == "crit":
        return theme.attr(theme.P_BAD, curses.A_BOLD)
    if severity == "warn":
        return theme.attr(theme.P_WARN, curses.A_BOLD)
    return theme.attr(theme.P_HEADER)


def _severity_glyph(severity: str) -> str:
    if severity == "crit":
        return "✗"
    if severity == "warn":
        return "⚠"
    return "ⓘ"


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h"


def draw_alerts_overlay(stdscr, store, cursor_idx: int) -> int:
    """Render the modal. Returns the clamped cursor index so callers
    can keep their state in sync if alerts went away under them."""
    h, w = stdscr.getmaxyx()
    alerts = store.alerts_active()
    if not alerts:
        # Empty state — useful when the operator opened the overlay
        # right after dismissing the last alert.
        msg = " No active alerts. Press Esc / ! to close. "
        x0 = max(0, (w - len(msg)) // 2)
        y0 = max(0, h // 2)
        safe_addstr(stdscr, y0, x0, msg,
                    theme.attr(theme.P_TITLE, curses.A_BOLD))
        return 0

    # Clamp the cursor in case alerts went away mid-session.
    if cursor_idx < 0:
        cursor_idx = 0
    if cursor_idx >= len(alerts):
        cursor_idx = len(alerts) - 1

    # Draw a full-screen wash so the panel underneath does not bleed
    # through the overlay (especially the Flame view, which paints
    # solid colours).
    bg = theme.attr(theme.P_TITLE)
    for y in range(h):
        safe_addstr(stdscr, y, 0, " " * (w - 1), bg)

    # Title
    title = f" Active alerts ({len(alerts)}) — ↑↓ select, Enter jump, d dismiss, Esc close "
    safe_addstr(stdscr, 0, 0, title.ljust(w - 1),
                theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD))

    # Alert list — top half of the modal (max half the screen so the
    # detail body always has room).
    list_top = 2
    list_max = min(len(alerts), max(3, (h - 6) // 2))
    now = time.time()
    for i in range(list_max):
        a = alerts[i]
        y = list_top + i
        if y >= h - 4:
            break
        sel = (i == cursor_idx)
        sev_attr = _severity_attr(a.severity)
        row_attr = (curses.A_REVERSE | curses.A_BOLD) if sel else 0
        # severity glyph + title
        glyph = _severity_glyph(a.severity)
        age = _format_age(a.age_seconds(now))
        suggested = (f"→ {a.suggested_panel}" if a.suggested_panel
                     else "")
        line = f"  {glyph} [{a.severity:<4}] {a.title}"
        suffix = f"  {age} {suggested} "
        avail = max(0, w - len(suffix) - 2)
        line_short = (line[:avail] + " " * max(0, avail - len(line)))
        full = (line_short + suffix).ljust(w - 1)
        # Render with severity color when not selected; reverse when selected.
        if sel:
            safe_addstr(stdscr, y, 0, full,
                        sev_attr | curses.A_REVERSE | curses.A_BOLD)
        else:
            safe_addstr(stdscr, y, 0, full, sev_attr)

    # Divider
    div_y = list_top + list_max
    if div_y < h - 3:
        safe_addstr(stdscr, div_y, 0, "─" * (w - 1),
                    theme.attr(theme.P_DIM))

    # Detail body for the selected alert
    detail_top = div_y + 1
    selected = alerts[cursor_idx]
    detail_lines = (
        [f"Detector: {selected.detector}    "
         f"Severity: {selected.severity}    "
         f"Age: {_format_age(selected.age_seconds(now))}",
         ""]
        + selected.detail.splitlines()
    )
    if selected.suggested_action:
        detail_lines += ["", f"  → {selected.suggested_action}"]
    if selected.suggested_panel:
        detail_lines += [
            f"  Press Enter to jump to the "
            f"{_panel_letter_to_name(selected.suggested_panel)} panel."
        ]
    if selected.docs_anchor:
        detail_lines += [
            f"  README: doc/README.md#{selected.docs_anchor}"
        ]
    for i, line in enumerate(detail_lines):
        y = detail_top + i
        if y >= h - 2:
            break
        attr = (theme.attr(theme.P_BAD)
                if line.startswith("  → ") else 0)
        safe_addstr(stdscr, y, 2, line[:max(0, w - 4)], attr)

    # Footer
    if h >= 2:
        footer_attr = theme.attr(theme.P_TITLE)
        hot = theme.attr(theme.P_MNEMONIC,
                         curses.A_BOLD | curses.A_UNDERLINE)
        x = 0
        safe_addstr(stdscr, h - 1, x, " ", footer_attr); x += 1
        x = write_label(stdscr, h - 1, x, "↑↓", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, " select  ", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, "Enter", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, " jump+dismiss  ", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, "d", 0, footer_attr, hot)
        x = write_label(stdscr, h - 1, x, " dismiss  ", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, "Esc", 0, footer_attr, footer_attr)
        x = write_label(stdscr, h - 1, x, " close ", 0, footer_attr, footer_attr)
        if x < w - 1:
            safe_addstr(stdscr, h - 1, x, " " * (w - x - 1), footer_attr)

    return cursor_idx


def handle_alerts_key(key, store, cursor_idx: int):
    """Returns (new_cursor_idx, action) where action is one of:

      'close'  — close the overlay
      'jump:X' — close + switch to panel with mnemonic letter X
                 (caller dismisses the alert before switching)
      None     — stay open
    """
    alerts = store.alerts_active()
    n = len(alerts)
    if key in (27, ord("!")):
        return cursor_idx, "close"
    if n == 0:
        return cursor_idx, None
    if key == curses.KEY_UP:
        return max(0, cursor_idx - 1), None
    if key == curses.KEY_DOWN:
        return min(n - 1, cursor_idx + 1), None
    if key in (10, 13, curses.KEY_ENTER):
        a = alerts[min(cursor_idx, n - 1)]
        store.alert_dismiss(a.id)
        if a.suggested_panel:
            return cursor_idx, f"jump:{a.suggested_panel}"
        return cursor_idx, "close"
    if key == ord("d"):
        a = alerts[min(cursor_idx, n - 1)]
        store.alert_dismiss(a.id)
        return cursor_idx, None
    return cursor_idx, None


def _panel_letter_to_name(letter: str) -> str:
    """Friendly panel name for the docs/footer text — short table
    that mirrors the mnemonic letters of the built-in panels."""
    names = {
        "i": "Live", "h": "Health", "f": "Flame",
        "o": "Overview", "g": "Graphs", "u": "URLs",
        "c": "Caches", "s": "Services", "a": "Active",
        "p": "Proc", "l": "Logs", "k": "Apikeys",
    }
    return names.get(letter, letter)
