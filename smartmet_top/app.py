"""Curses + asyncio application."""

from __future__ import annotations

import asyncio
import curses
import signal
import time
from typing import List, Optional

from . import export, runtime, theme
from .panels.active import ActivePanel
from .panels.base import Panel, safe_addstr, write_label
from .panels.caches import CachesPanel
from .panels.flame import FlamePanel
from .panels.help import HelpPanel
from .panels.keys import KeysPanel
from .panels.logs import LogsPanel
from .panels.overview import OverviewPanel
from .panels.plugins import PluginsPanel
from .panels.proc import ProcPanel
from .panels.services import ServicesPanel
from .panels.urls import UrlsPanel
from .panels.network import NetworkPanel
from .panels.alerts_overlay import draw_alerts_overlay, handle_alerts_key
from .views.admin import AdminView
from .views.live import LiveView
from .state.store import Store


REFRESH = 0.3     # seconds between redraws
KEY_POLL = 0.02   # seconds between key polls


class App:
    def __init__(self, log_paths: List[str],
                 admin_urls: List[tuple],  # [(host_label, base_url), ...]
                 admin_interval: float, replay: bool,
                 replay_bytes: int = 1024 * 1024 * 1024,
                 include_rotated: bool = False,
                 enable_perf: bool = False,
                 perf_interval: float = 10.0,
                 perf_record_seconds: int = 3,
                 malloc_flame_min_bytes: Optional[int] = None,
                 journal_unit: str = "smartmet-backend,smartmet-frontend"
                 ) -> None:
        self.store = Store()
        for host, _ in admin_urls:
            self.store.register_admin_host(host)
        self.log_paths = log_paths
        self.admin_urls = admin_urls
        self.admin_interval = admin_interval
        self.replay = replay
        self.replay_bytes = replay_bytes
        self.include_rotated = include_rotated
        self.enable_perf = enable_perf
        self.perf_interval = perf_interval
        self.perf_record_seconds = perf_record_seconds
        self.malloc_flame_min_bytes = malloc_flame_min_bytes
        self.journal_unit = journal_unit
        self.store.perf_enabled = enable_perf
        self.panels: List[Panel] = [
            LiveView(),
            AdminView(),
            FlamePanel(),
            OverviewPanel(),
            PluginsPanel(),
            UrlsPanel(),
            CachesPanel(),
            ServicesPanel(),
            ActivePanel(),
            ProcPanel(),
            NetworkPanel(),
            LogsPanel(),
            KeysPanel(),
        ]
        # HelpPanel needs the App back-reference so it can render the
        # contextual help of whichever panel was active when ? was
        # pressed. We pass self after self.panels is populated.
        self.help_panel = HelpPanel(self)
        self.panel_idx = 0  # default: Live composite (Graphs + URLs)
        self.show_help = False
        self.running = True
        self.last_error = ""
        self.toast: Optional[tuple] = None  # (expires_at, message, attr)
        # Alerts overlay (`!` to open). State lives here rather than on
        # any single Panel because the overlay is drawn on top of
        # whichever panel happens to be active.
        self._alerts_open: bool = False
        self._alerts_cursor: int = 0

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
        for host in self.store.admin_hosts:
            s = self.store.admin_status.get(host, "?")
            src.append(f"{host}:{s}")
        status = "  ".join(src)
        safe_addstr(stdscr, 0, 0, (title + status).ljust(w - 1),
                    theme.attr(theme.P_TITLE, curses.A_BOLD))

        # tabs — each label has one character (the panel's hotkey) drawn in
        # red+bold so the user sees at a glance which key switches to it.
        x = 1
        for i, p in enumerate(self.panels):
            active = (not self.show_help and i == self.panel_idx)
            base_attr = (theme.attr(theme.P_TAB_ACTIVE, curses.A_BOLD)
                         if active else theme.attr(theme.P_TAB_INACTIVE))
            hot_attr = theme.attr(theme.P_MNEMONIC,
                                  curses.A_BOLD | curses.A_UNDERLINE)
            # leading and trailing space framed in the base attribute
            safe_addstr(stdscr, 1, x, " ", base_attr)
            x = write_label(stdscr, 1, x + 1, p.name,
                            p.mnemonic_pos, base_attr, hot_attr)
            safe_addstr(stdscr, 1, x, " ", base_attr)
            x += 2  # one space gap between tabs
        # Right-aligned: alert badge + standard hint. Badge colours
        # carry severity: red on crit, yellow on warn, dim otherwise.
        # Always visible regardless of which panel is active so the
        # operator notices a problem detected by another sampler
        # without having to switch view.
        n_alerts, top_sev = self.store.alerts_summary()
        hint = " ? help   q quit   ! alerts   Tab next "
        right_x = w - len(hint) - 2
        if n_alerts > 0:
            badge_attr = (curses.A_BOLD | (
                theme.attr(theme.P_BAD) if top_sev == "crit"
                else theme.attr(theme.P_WARN) if top_sev == "warn"
                else theme.attr(theme.P_HEADER)))
            badge = f" ⚠ {n_alerts} alert{'s' if n_alerts != 1 else ''} "
            badge_x = right_x - len(badge)
            if badge_x > x:
                safe_addstr(stdscr, 1, badge_x, badge, badge_attr)
        if x + len(hint) < w:
            safe_addstr(stdscr, 1, right_x, hint, theme.attr(theme.P_DIM))

        # status line (or toast if one is active)
        p = self.current_panel
        toast = self.toast
        if toast is not None:
            expires, msg, tattr = toast
            if time.time() < expires:
                safe_addstr(stdscr, h - 1, 0, f" {msg}".ljust(w - 1), tattr)
                return
            self.toast = None
        safe_addstr(stdscr, h - 1, 0, f" {p.help_text}".ljust(w - 1),
                    theme.attr(theme.P_TITLE))

    def draw(self, stdscr) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        # GC stale alerts each redraw so detectors that stopped firing
        # do not leave dead entries cluttering the badge / overlay.
        self.store.gc_alerts()
        self.draw_chrome(stdscr)

        # Carve out room for the global notification strip and the
        # per-panel banner BEFORE the panel sub-window. The panel
        # itself does not need to know they are there.
        panel_top = 2
        unviewed = self.store.alerts_unviewed()
        if unviewed:
            self._draw_global_strip(stdscr, unviewed, panel_top)
            panel_top += 1
        active_panel = self.current_panel
        panel_letter = getattr(active_panel, "hotkey", "")
        panel_alerts = self.store.alerts_for(panel_letter) if panel_letter else []
        if panel_alerts:
            self._draw_panel_banner(stdscr, panel_alerts, panel_top)
            panel_top += 1

        # panel content drawn into a sub-window so panels don't need to
        # reserve chrome rows themselves.
        if h > panel_top + 2 and w > 4:
            try:
                sub = stdscr.derwin(h - panel_top - 1, w - 1, panel_top, 0)
            except curses.error:
                sub = stdscr
            try:
                self.current_panel.draw(sub, self.store)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                safe_addstr(stdscr, panel_top, 2,
                            f"draw error: {self.last_error}")

        # Modal overlay sits on top of everything.
        if self._alerts_open:
            self._alerts_cursor = draw_alerts_overlay(
                stdscr, self.store, self._alerts_cursor
            )

        stdscr.noutrefresh()
        curses.doupdate()

    def _draw_global_strip(self, stdscr, unviewed, row: int) -> None:
        """Bright row above the panel announcing brand-new alerts.
        Visible regardless of which panel is active. Vanishes the
        moment every active alert has been viewed."""
        h, w = stdscr.getmaxyx()
        # Pick the highest-severity unviewed alert as the headline.
        head = max(unviewed, key=lambda a: a.severity_rank())
        sev_color = (theme.P_BAD if head.severity == "crit"
                     else theme.P_WARN if head.severity == "warn"
                     else theme.P_HEADER)
        attr = theme.attr(sev_color, curses.A_BOLD | curses.A_BLINK)
        n = len(unviewed)
        msg = (f" ⚠ NEW ALERT — {head.title} "
               f"  press '!' to view "
               f"{('(+' + str(n - 1) + ' more)') if n > 1 else ''}")
        safe_addstr(stdscr, row, 0, msg.ljust(w - 1), attr)

    def _close_alerts_overlay(self) -> None:
        self._alerts_open = False
        self._alerts_cursor = 0

    def _draw_panel_banner(self, stdscr, alerts, row: int) -> None:
        """One-row banner above the panel content when an alert
        suggests THIS panel as the place to investigate. Same per
        panel — no special wiring per Panel subclass."""
        h, w = stdscr.getmaxyx()
        a = alerts[0]
        sev_color = (theme.P_BAD if a.severity == "crit"
                     else theme.P_WARN if a.severity == "warn"
                     else theme.P_HEADER)
        attr = theme.attr(sev_color, curses.A_REVERSE | curses.A_BOLD)
        msg = f" [!] {a.title} — {a.suggested_action or 'investigate here'}  press '!' for full alert "
        safe_addstr(stdscr, row, 0, msg.ljust(w - 1), attr)

    def handle_key(self, key: int) -> None:
        # The alerts overlay, when open, owns every keystroke: arrows,
        # Enter (jump), d (dismiss), Esc (close). We route to it first
        # so the underlying panel never sees the keys.
        if self._alerts_open:
            self._alerts_cursor, action = handle_alerts_key(
                key, self.store, self._alerts_cursor
            )
            if action == "close":
                self._close_alerts_overlay()
            elif action and action.startswith("jump:"):
                target = action.split(":", 1)[1]
                self._close_alerts_overlay()
                for i, panel in enumerate(self.panels):
                    if panel.hotkey == target:
                        self.show_help = False
                        self.panel_idx = i
                        break
            return

        # `!` opens the overlay. Mark every active alert "viewed" the
        # moment we open — the global blinking strip should disappear
        # the instant the operator acknowledges.
        if key == ord("!"):
            self._alerts_open = True
            self._alerts_cursor = 0
            self.store.mark_alerts_viewed()
            return

        # Delegate to the active panel first. Panels return True if they
        # consumed the key. Anything they don't consume falls through to
        # the global keys below.
        p = self.current_panel
        try:
            consumed = bool(p.handle_key(key, self.store))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            consumed = True
        # A panel may have asked to drill into another panel — e.g. the
        # Plugins panel's Enter wants to take the operator to the URLs
        # panel filtered by the selected plugin label.
        if self.store.pending_panel_switch is not None:
            target, params = self.store.pending_panel_switch
            self.store.pending_panel_switch = None
            for i, panel in enumerate(self.panels):
                if panel.hotkey == target:
                    self.show_help = False
                    self.panel_idx = i
                    if "filter" in params and hasattr(panel, "set_filter"):
                        panel.set_filter(params["filter"])
                    break
        if consumed:
            return

        # Global keys.
        if key == ord("q"):
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
        if key in (ord("?"), curses.KEY_F1):
            self.show_help = not self.show_help
            return
        if key == ord("e"):
            self._export("csv"); return
        if key == ord("E"):
            self._export("json"); return

        # Panel mnemonics — single uppercase letter per panel, taken
        # from each panel's `hotkey` attribute (stored lowercase).
        # **Uppercase = switch panel, lowercase = within-panel
        # navigation.** Splitting the case lets each panel bind
        # lowercase letters freely (e.g. `n` for next-PID on Proc)
        # without shadowing a global panel-switch (`N` for Network).
        if 65 <= key <= 90:                  # ASCII A-Z
            ch = chr(key).lower()
            for i, panel in enumerate(self.panels):
                if panel.hotkey == ch:
                    self.show_help = False
                    self.panel_idx = i
                    return

    def _set_toast(self, msg: str, attr: int, seconds: float = 4.0) -> None:
        self.toast = (time.time() + seconds, msg, attr)

    def _export(self, fmt: str) -> None:
        p = self.current_panel
        try:
            headers, rows = p.export_snapshot(self.store)
        except Exception as e:
            self._set_toast(f"export failed: {e}", theme.attr(theme.P_BAD, curses.A_BOLD))
            return
        if headers is None:
            self._set_toast(f"{p.name}: nothing to export",
                            theme.attr(theme.P_WARN))
            return
        try:
            path = export.save_snapshot(p.name, headers, rows, fmt=fmt)
        except Exception as e:
            self._set_toast(f"export failed: {e}", theme.attr(theme.P_BAD, curses.A_BOLD))
            return
        self._set_toast(f"exported {len(rows)} rows → {path}",
                        theme.attr(theme.P_GOOD, curses.A_BOLD))

    async def run(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        theme.init()
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except Exception:
            pass

        # background tasks. Replay first (synchronous) so the panels
        # come up populated; then schedule the rest via the shared
        # runtime so smwebmon stays in lock-step on which sources run.
        if self.replay:
            await runtime.replay_logs(
                self.store, self.log_paths,
                replay_bytes=self.replay_bytes,
                include_rotated=self.include_rotated,
            )
        tasks = runtime.start_sources(
            self.store,
            log_paths=self.log_paths,
            admin_urls=self.admin_urls,
            admin_interval=self.admin_interval,
            enable_perf=self.enable_perf,
            perf_interval=self.perf_interval,
            perf_record_seconds=self.perf_record_seconds,
            malloc_flame_min_bytes=self.malloc_flame_min_bytes,
            journal_unit=self.journal_unit,
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


def run_app(log_paths: List[str], admin_urls: List[tuple],
            admin_interval: float, replay: bool,
            replay_bytes: int = 1024 * 1024 * 1024,
            include_rotated: bool = False,
            enable_perf: bool = False, perf_interval: float = 10.0,
            perf_record_seconds: int = 3,
            malloc_flame_min_bytes: Optional[int] = None,
            journal_unit: str = "smartmet-backend,smartmet-frontend"
            ) -> None:
    app = App(log_paths, admin_urls, admin_interval, replay,
              replay_bytes=replay_bytes,
              include_rotated=include_rotated,
              enable_perf=enable_perf, perf_interval=perf_interval,
              perf_record_seconds=perf_record_seconds,
              malloc_flame_min_bytes=malloc_flame_min_bytes,
              journal_unit=journal_unit)

    def _curses_main(stdscr):
        # asyncio.run inside the curses wrapper keeps teardown correct
        asyncio.run(app.run(stdscr))

    # Make Ctrl-C translate to a clean shutdown: curses handles SIGINT by
    # default but the asyncio loop needs to see it as app.running=False.
    def _sigint(signum, frame):
        app.running = False

    signal.signal(signal.SIGINT, _sigint)
    curses.wrapper(_curses_main)
