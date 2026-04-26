"""Logs panel — raw tail view with filter."""

from __future__ import annotations

import curses

from .. import theme
from .base import Panel, safe_addstr


class LogsPanel(Panel):
    name = "Logs"
    hotkey = "l"
    help_text = (
        "Live `tail -F` over every tailed access log, merged into "
        "one stream. Each line is prefixed with [<plugin>] so the "
        "source is visible. / filters, End jumps to newest."
    )

    def __init__(self):
        self.filter = ""
        self.filter_editing = False
        self.scroll = 0          # lines from bottom; 0 = newest
        self.follow = True

    def handle_key(self, key, store):
        if self.filter_editing:
            if key in (10, 13, curses.KEY_ENTER, 27):
                self.filter_editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.filter = self.filter[:-1]
            elif 32 <= key < 127:
                self.filter += chr(key)
            return True

        if key == ord("/"):
            self.filter_editing = True
        elif key == 27:
            self.filter = ""
        elif key in (curses.KEY_UP, ord("k")):
            self.scroll += 1
            self.follow = False
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.scroll += 20
            self.follow = False
        elif key == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 20)
        elif key in (curses.KEY_END, ord("G")):
            self.scroll = 0
            self.follow = True
        elif key in (curses.KEY_HOME, ord("g")):
            self.scroll = 10_000_000
            self.follow = False
        else:
            return False
        return True

    def draw(self, win, store):
        h, w = win.getmaxyx()
        n_sources = len(store.snapshot_sources())
        hdr = (
            f" Logs — tail -F across {n_sources} log file"
            f"{'s' if n_sources != 1 else ''}  "
            f"filter:{self.filter or '<none>'}  "
            f"{'FOLLOW' if self.follow else 'scrolled'}  "
            "(/ filters by [plugin] or substring, End jumps to live)"
        )
        safe_addstr(win, 0, 0, hdr.ljust(w - 1),
                    theme.attr(theme.P_TAB_ACTIVE))

        lines = list(store.recent_lines)
        if self.filter:
            f = self.filter.lower()
            lines = [ln for ln in lines if f in ln.lower()]
        body_top = 2
        body_h = h - body_top - 1
        if body_h <= 0:
            return

        # scroll semantics: 0 means show latest body_h lines
        if self.scroll > max(0, len(lines) - body_h):
            self.scroll = max(0, len(lines) - body_h)
        end = len(lines) - self.scroll
        start = max(0, end - body_h)
        visible = lines[start:end]
        for i, ln in enumerate(visible):
            safe_addstr(win, body_top + i, 0, ln)

        if self.filter_editing:
            safe_addstr(win, h - 1, 0, f" /{self.filter}_ (enter/esc to stop)".ljust(w - 1),
                        theme.attr(theme.P_HIGHLIGHT))
