"""Logs panel — multi-source `tail -F` with per-source ring buffers.

Layout:
    row 0           panel header (current selection, FOLLOW state)
    row 1..K        source list — each tailed log on its own row, with
                    a marker on the focused one, navigable with ←/↑/→/↓
    row K+1         divider
    row K+2..h-2    `tail -F` of the focused source (or merged "all")
    row h-1         hint footer

Each tailed source has its own bounded ring buffer in the Store, so
switching sources is instant and a high-rate plugin never crowds out
a low-rate one. There's also a virtual "[all]" entry that pulls from
the global merged ring (still useful for spotting cross-plugin
interleaving).
"""

from __future__ import annotations

import curses
from typing import List, Optional

from .. import theme
from .base import Panel, safe_addstr


ALL_LABEL = "[all]"


class LogsPanel(Panel):
    name = "Logs"
    hotkey = "l"
    help_text = (
        "Multi-source tail. ←↑→↓ pick a log file, Enter / End jump to "
        "live, / type a substring filter for the focused log, Esc "
        "clears the filter."
    )

    def __init__(self) -> None:
        # Currently-focused source; "" means the [all] merged view.
        self.selected_source: str = ""
        self.filter: str = ""
        self.filter_editing: bool = False
        self.scroll: int = 0   # lines from bottom; 0 = newest
        self.follow: bool = True

    # ---- key handling ------------------------------------------------------

    def handle_key(self, key, store):
        if self.filter_editing:
            if key in (10, 13, curses.KEY_ENTER, 27):
                self.filter_editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.filter = self.filter[:-1]
            elif 32 <= key < 127:
                self.filter += chr(key)
            return True

        labels = self._label_list(store)

        # Logs panel deliberately uses ONLY arrow keys for navigation —
        # not vim-style h/j/k/l. Those letters are global panel
        # mnemonics (h = Health, k = Apikeys, l = Logs itself,
        # g = Graphs) and intercepting them here would prevent the
        # operator from switching panels with their mnemonics.
        if key == ord("/"):
            self.filter_editing = True
        elif key == 27:
            self.filter = ""
        elif key == curses.KEY_LEFT:
            self._cycle_source(labels, -1)
        elif key == curses.KEY_RIGHT:
            self._cycle_source(labels, +1)
        elif key == curses.KEY_UP:
            # Scroll one line back (older).
            self.scroll += 1
            self.follow = False
        elif key == curses.KEY_DOWN:
            # Scroll one line forward (toward live tail).
            self.scroll = max(0, self.scroll - 1)
            if self.scroll == 0:
                self.follow = True
        elif key in (10, 13, curses.KEY_ENTER, curses.KEY_END):
            # Lock onto the focused source and jump to the live tail.
            self.scroll = 0
            self.follow = True
        elif key == curses.KEY_PPAGE:
            self.scroll += 20
            self.follow = False
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 20)
            if self.scroll == 0:
                self.follow = True
        elif key == curses.KEY_HOME:
            self.scroll = 10_000_000
            self.follow = False
        else:
            return False
        return True

    def _label_list(self, store) -> List[str]:
        """`[all]` first, then every tailed source label."""
        return [""] + sorted(s.label for s in store.snapshot_sources())

    def _cycle_source(self, labels: List[str], delta: int) -> None:
        if not labels:
            return
        try:
            idx = labels.index(self.selected_source)
        except ValueError:
            idx = 0
        self.selected_source = labels[(idx + delta) % len(labels)]
        # New source: jump to live so the operator sees fresh activity.
        self.scroll = 0
        self.follow = True

    # ---- drawing -----------------------------------------------------------

    def draw(self, win, store):
        h, w = win.getmaxyx()
        labels = self._label_list(store)
        if self.selected_source not in labels:
            self.selected_source = ""

        # Header
        sel_display = self.selected_source if self.selected_source else ALL_LABEL
        hdr = (
            f" Logs — {sel_display}  "
            f"{'FOLLOW' if self.follow else 'scrolled'}  "
            f"filter:{self.filter or '<none>'}  "
            "(←→ source  ↑↓ scroll  End live  / filter)"
        )
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        # Source bar — tabs across one or more rows.
        bar_top = 2
        bar_bottom = self._draw_source_bar(win, labels, bar_top)
        # Divider
        if bar_bottom < h - 1:
            safe_addstr(win, bar_bottom, 0, "─" * (w - 1),
                        theme.attr(theme.P_DIM))

        body_top = bar_bottom + 1
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        lines = self._lines_for_selection(store)
        if self.filter:
            f = self.filter.lower()
            lines = [ln for ln in lines if f in ln.lower()]

        if not lines:
            # Empty buffer — common when the operator just cycled to
            # an idle plugin. Show a placeholder so it doesn't look
            # like the panel is broken.
            placeholder = (
                "(no lines for this source yet)"
                if self.selected_source
                else "(no log lines tailed yet)"
            )
            safe_addstr(win, body_top + body_h - 1, 2, placeholder,
                        theme.attr(theme.P_DIM))
            if self.filter_editing:
                safe_addstr(win, h - 1, 0,
                            f" /{self.filter}_ (enter/esc to stop)".ljust(w - 1),
                            theme.attr(theme.P_HIGHLIGHT))
            return

        # Scroll semantics: 0 = newest at bottom.
        if self.scroll > max(0, len(lines) - body_h):
            self.scroll = max(0, len(lines) - body_h)
        end = len(lines) - self.scroll
        start = max(0, end - body_h)
        visible = lines[start:end]
        # Bottom-anchor when there's less data than rows so the panel
        # visually behaves like `tail -F` (newest at the bottom edge,
        # blank space above) rather than top-aligned.
        pad = max(0, body_h - len(visible))
        for i, ln in enumerate(visible):
            safe_addstr(win, body_top + pad + i, 0, ln)

        if self.filter_editing:
            safe_addstr(win, h - 1, 0,
                        f" /{self.filter}_ (enter/esc to stop)".ljust(w - 1),
                        theme.attr(theme.P_HIGHLIGHT))

    def _draw_source_bar(self, win, labels: List[str], top: int) -> int:
        """Render the source list as ` plugin  plugin  ▶plugin◀  plugin `
        with the focused entry marked. Wraps to multiple rows if
        there are too many sources for one line.
        """
        h, w = win.getmaxyx()
        x = 0
        y = top
        for label in labels:
            display = ALL_LABEL if label == "" else label
            is_sel = (label == self.selected_source)
            text = f" ▶{display}◀ " if is_sel else f"  {display}  "
            attr = (theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD) if is_sel
                    else theme.attr(theme.P_TAB_INACTIVE))
            if x + len(text) >= w - 1:
                # Wrap to next row
                if y + 1 >= h - 1:
                    break  # ran out of room; remaining sources hidden
                y += 1
                x = 0
            safe_addstr(win, y, x, text, attr)
            x += len(text)
        return y + 1

    def _lines_for_selection(self, store) -> List[str]:
        """Pull the tail buffer for the currently-focused selection."""
        if not self.selected_source:
            # Merged view — use the global ring (lines already prefixed).
            return list(store.recent_lines)
        buf = store.source_lines.get(self.selected_source)
        return list(buf) if buf else []
