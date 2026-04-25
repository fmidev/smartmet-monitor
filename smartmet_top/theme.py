"""Terminal colour palette.

Colour pairs are initialised once at app startup. Panels access colours
through `attr(P_FOO)` so they stay agnostic of curses' pair-number API.
"""

from __future__ import annotations

import curses

# Pair indices. 0 is reserved by curses for "default".
P_TITLE = 1
P_TAB_ACTIVE = 2
P_TAB_INACTIVE = 3
P_GOOD = 4
P_WARN = 5
P_BAD = 6
P_DIM = 7
P_HEADER = 8
P_HIGHLIGHT = 9
P_SPARK = 10
P_AXIS = 11
P_ACCENT = 12
P_MNEMONIC = 13

_initialized = False


def init() -> None:
    global _initialized
    if _initialized:
        return
    try:
        curses.start_color()
    except curses.error:
        return
    try:
        curses.use_default_colors()
        default_bg = -1
    except curses.error:
        default_bg = curses.COLOR_BLACK

    curses.init_pair(P_TITLE,        curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(P_TAB_ACTIVE,   curses.COLOR_BLACK,  curses.COLOR_YELLOW)
    curses.init_pair(P_TAB_INACTIVE, curses.COLOR_CYAN,   default_bg)
    curses.init_pair(P_GOOD,         curses.COLOR_GREEN,  default_bg)
    curses.init_pair(P_WARN,         curses.COLOR_YELLOW, default_bg)
    curses.init_pair(P_BAD,          curses.COLOR_RED,    default_bg)
    curses.init_pair(P_DIM,          curses.COLOR_WHITE,  default_bg)
    curses.init_pair(P_HEADER,       curses.COLOR_WHITE,  default_bg)
    curses.init_pair(P_HIGHLIGHT,    curses.COLOR_BLACK,  curses.COLOR_YELLOW)
    curses.init_pair(P_SPARK,        curses.COLOR_CYAN,   default_bg)
    curses.init_pair(P_AXIS,         curses.COLOR_WHITE,  default_bg)
    curses.init_pair(P_ACCENT,       curses.COLOR_MAGENTA, default_bg)
    # Mnemonic letter = red on the default background. Used to highlight
    # the single hotkey character inside tab labels and inline option
    # markers ("[s]ort", "[r]everse", etc.).
    curses.init_pair(P_MNEMONIC,     curses.COLOR_RED,    default_bg)
    _initialized = True


def attr(pair: int, extra: int = 0) -> int:
    try:
        return curses.color_pair(pair) | extra
    except curses.error:
        return extra


# ---- semantic helpers ------------------------------------------------------
#
# Each helper returns a curses attribute (or 0) appropriate for the given
# metric. The thresholds are deliberately static — panels can override if a
# specific context needs different cut-offs.

def err_color(pct: float) -> int:
    if pct >= 5.0:
        return attr(P_BAD, curses.A_BOLD)
    if pct >= 1.0:
        return attr(P_WARN)
    return 0


def latency_color(ms: float, warn: float = 500.0, bad: float = 2000.0) -> int:
    if ms >= bad:
        return attr(P_BAD, curses.A_BOLD)
    if ms >= warn:
        return attr(P_WARN)
    return 0


def duration_color(secs: float, warn: float = 5.0, bad: float = 30.0) -> int:
    if secs >= bad:
        return attr(P_BAD, curses.A_BOLD)
    if secs >= warn:
        return attr(P_WARN)
    return 0


def hitrate_color(pct: float) -> int:
    """Lower hit-rate is worse (inverted)."""
    if pct < 50.0:
        return attr(P_BAD, curses.A_BOLD)
    if pct < 80.0:
        return attr(P_WARN)
    return attr(P_GOOD)


def fill_color(used: float, maxv: float) -> int:
    """Cache/resource fill level: higher is worse."""
    if maxv <= 0:
        return 0
    r = used / maxv
    if r >= 0.95:
        return attr(P_BAD, curses.A_BOLD)
    if r >= 0.80:
        return attr(P_WARN)
    return attr(P_GOOD)
