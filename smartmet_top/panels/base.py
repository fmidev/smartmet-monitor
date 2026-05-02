"""Panel base class.

A Panel is a full-screen view (minus the chrome — title bar and status
line drawn by App). Panels receive a curses window via draw(win, store)
and handle keys via handle_key(key, store).
"""

from __future__ import annotations

import curses


class Panel:
    name: str = "panel"
    # Single-character mnemonic (lowercase) used both as the global hotkey
    # to switch to this panel and as the highlighted character in the tab
    # label. By default it is the first letter of `name`, but a panel can
    # override `mnemonic_pos` to highlight a different character.
    hotkey: str = "?"
    mnemonic_pos: int = 0
    help_text: str = ""
    # Multi-line contextual help shown by HelpPanel when the operator
    # presses `?` while this panel is active. Format: paragraphs
    # separated by blank lines; first line of each section can be a
    # heading by ending with `:`. Useful for panels showing several
    # different metrics where each metric needs its own explanation
    # (the perf-related panels are the primary use case).
    panel_help: str = ""

    def draw(self, win, store) -> None:  # pragma: no cover
        raise NotImplementedError

    def handle_key(self, key: int, store) -> bool:
        """Return True if the panel consumed the key, False otherwise.

        Returning False lets the App fall through to global keys (panel
        switching, help, quit, export). Panels consume their own per-mode
        bindings and pass everything else up.
        """
        return False

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


def write_label(win, y, x, label: str, mnemonic_pos: int,
                base_attr: int, hot_attr: int) -> int:
    """Write `label` left-to-right, applying `hot_attr` to the character at
    `mnemonic_pos` and `base_attr` to the rest. Returns the next x column.

    Keeps the chrome simple: one call places a tab/title with its hotkey
    letter visually highlighted.
    """
    if not label:
        return x
    pos = mnemonic_pos if 0 <= mnemonic_pos < len(label) else 0
    if pos > 0:
        safe_addstr(win, y, x, label[:pos], base_attr)
        x += pos
    safe_addstr(win, y, x, label[pos], hot_attr)
    x += 1
    if pos + 1 < len(label):
        safe_addstr(win, y, x, label[pos + 1:], base_attr)
        x += len(label) - pos - 1
    return x


def write_section_header(win, y: int, hotkey: str, label: str,
                         hidden: bool = False) -> None:
    """Render a section divider with a bracketed hotkey chip.

    Layout: ``▾ [t] TCP host-wide ─────────────`` (or ``▸`` when
    ``hidden=True``). ``[t]`` is drawn with the letter in
    P_MNEMONIC red bold; the label uses P_HEADER bold when visible
    and P_DIM (no bold) when hidden, so a collapsed section greys
    out its title. Trailing dashes fill the row.

    Used as the standard section-header widget on the Network and
    Proc panels (and any future multi-section curses panel) so the
    keyboard convention — uppercase = panel switch, lowercase =
    within-panel toggle — has a single, consistent visualisation.

    For sections that are not toggleable (no hotkey wired), pass
    ``hotkey=""`` and only the label + dashes render.
    """
    from .. import theme  # avoid circular import at module load time
    h, w = win.getmaxyx()
    base = theme.attr(theme.P_DIM)
    x = 0
    chevron = "▸" if hidden else "▾"
    if hotkey:
        safe_addstr(win, y, x, chevron + " ", base)
        x += 2
        safe_addstr(win, y, x, "[", base); x += 1
        safe_addstr(win, y, x, hotkey,
                    theme.attr(theme.P_MNEMONIC, curses.A_BOLD))
        x += 1
        safe_addstr(win, y, x, "] ", base); x += 2
    else:
        safe_addstr(win, y, x, "─ ", base)
        x += 2
    label_attr = (theme.attr(theme.P_DIM) if hidden
                  else theme.attr(theme.P_HEADER, curses.A_BOLD))
    safe_addstr(win, y, x, label + " ", label_attr)
    x += len(label) + 1
    if x < w - 1:
        safe_addstr(win, y, x, "─" * max(0, w - x - 1), base)


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
