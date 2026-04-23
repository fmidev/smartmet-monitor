"""Curses + asyncio application."""

from __future__ import annotations

import asyncio
import curses
import signal
import time
from typing import List, Optional

from . import theme
from .panels.active import ActivePanel
from .panels.base import Panel, safe_addstr
from .panels.caches import CachesPanel
from .panels.help import HelpPanel
from .panels.logs import LogsPanel
from .panels.overview import OverviewPanel
from .panels.services import ServicesPanel
from .panels.urls import UrlsPanel
from .sources.adminapi import poll_admin
from .sources.logtail import bulk_load, tail_many
from .state.store import Store


REFRESH = 0.3     # seconds between redraws
KEY_POLL = 0.02   # seconds between key polls


class App:
    def __init__(self, log_paths: List[str], admin_url: Optional[str],
                 admin_interval: float, replay: bool) -> None:
        self.store = Store()
        self.log_paths = log_paths
        self.admin_url = admin_url
        self.admin_interval = admin_interval
        self.replay = replay
        self.panels: List[Panel] = [
            OverviewPanel(),
            UrlsPanel(),
            CachesPanel(),
            ServicesPanel(),
            ActivePanel(),
            LogsPanel(),
        ]
        self.help_panel = HelpPanel()
        self.panel_idx = 1  # default: URLs (primary)
        self.show_help = False
        self.running = True
        self.last_error = ""

    @property
    def current_panel(self) -> Panel:
        if self.show_help:
            return self.help_panel
        return self.panels[self.panel_idx]

    def draw_chrome(self, stdscr) -> None:
        h, w = stdscr.getmaxyx()
        title = f" smartmet-top  {time.strftime('%F %T')}  "
        src = []
        if self.log_paths:
            src.append(f"logs:{self.store.logtail_status}")
        if self.admin_url:
            src.append(f"admin:{self.store.admin_status}")
        status = "  ".join(src)
        safe_addstr(stdscr, 0, 0, (title + status).ljust(w - 1),
                    theme.attr(theme.P_TITLE, curses.A_BOLD))

        # tabs
        x = 1
        for i, p in enumerate(self.panels):
            label = f" {p.hotkey} {p.name} "
            if not self.show_help and i == self.panel_idx:
                a = theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD)
            else:
                a = theme.attr(theme.P_TAB_INACTIVE)
            safe_addstr(stdscr, 1, x, label, a)
            x += len(label) + 1
        # help hint
        hint = " ? help   q quit   Tab next "
        if x + len(hint) < w:
            safe_addstr(stdscr, 1, w - len(hint) - 2, hint, theme.attr(theme.P_DIM))

        # status line
        p = self.current_panel
        safe_addstr(stdscr, h - 1, 0, f" {p.help_text}".ljust(w - 1),
                    theme.attr(theme.P_TITLE))

    def draw(self, stdscr) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        self.draw_chrome(stdscr)

        # panel content drawn into a sub-window so panels don't need to
        # reserve chrome rows themselves.
        if h > 4 and w > 4:
            try:
                sub = stdscr.derwin(h - 3, w - 1, 2, 0)
            except curses.error:
                sub = stdscr
            try:
                self.current_panel.draw(sub, self.store)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                safe_addstr(stdscr, 2, 2, f"draw error: {self.last_error}")

        stdscr.noutrefresh()
        curses.doupdate()

    def handle_key(self, key: int) -> None:
        # Let the active panel consume input first if it has modal state
        # (filter editing, detail view). Otherwise global keys.
        p = self.current_panel
        intercept = getattr(p, "filter_editing", False) or getattr(p, "detail_url", None)

        if not intercept:
            if key in (ord("q"),):
                self.running = False
                return
            if key == 9:  # Tab
                self.show_help = False
                self.panel_idx = (self.panel_idx + 1) % len(self.panels)
                return
            if key == curses.KEY_BTAB:  # Shift-Tab
                self.show_help = False
                self.panel_idx = (self.panel_idx - 1) % len(self.panels)
                return
            if ord("1") <= key <= ord("9"):
                n = key - ord("1")
                if n < len(self.panels):
                    self.show_help = False
                    self.panel_idx = n
                    return
            if key in (ord("?"), curses.KEY_F1):
                self.show_help = not self.show_help
                return

        # delegate to the panel
        try:
            p.handle_key(key, self.store)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"

    async def run(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        theme.init()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except Exception:
            pass

        # background tasks
        tasks = []
        if self.replay and self.log_paths:
            # synchronous bulk load first so opening on a live server feels fast
            await bulk_load(self.log_paths, self.store)
        if self.log_paths:
            tasks.append(asyncio.create_task(tail_many(self.log_paths, self.store)))
        if self.admin_url:
            tasks.append(
                asyncio.create_task(
                    poll_admin(self.admin_url, self.store, self.admin_interval)
                )
            )

        last_draw = 0.0
        try:
            while self.running:
                now = time.time()
                if now - last_draw >= REFRESH:
                    self.draw(stdscr)
                    last_draw = now

                # drain keys
                for _ in range(32):
                    try:
                        key = stdscr.getch()
                    except KeyboardInterrupt:
                        self.running = False
                        break
                    if key == -1:
                        break
                    if key == curses.KEY_RESIZE:
                        curses.update_lines_cols()
                        continue
                    self.handle_key(key)

                await asyncio.sleep(KEY_POLL)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass


def run_app(log_paths: List[str], admin_url: Optional[str],
            admin_interval: float, replay: bool) -> None:
    app = App(log_paths, admin_url, admin_interval, replay)

    def _curses_main(stdscr):
        # asyncio.run inside the curses wrapper keeps teardown correct
        asyncio.run(app.run(stdscr))

    # Make Ctrl-C translate to a clean shutdown: curses handles SIGINT by
    # default but the asyncio loop needs to see it as app.running=False.
    def _sigint(signum, frame):
        app.running = False

    signal.signal(signal.SIGINT, _sigint)
    curses.wrapper(_curses_main)
