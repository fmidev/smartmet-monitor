"""Unicode bar / sparkline helpers.

All functions return *strings of visual-width N*. Because full blocks and
eighth blocks are single-column wide in a UTF-8 terminal, the visual
width equals the count of characters (not bytes) — so downstream curses
code can use `addstr` plus explicit column advancement rather than rely
on byte length.
"""

from __future__ import annotations

from typing import Sequence

EIGHTH = " ▏▎▍▌▋▊▉█"      # 0/8 .. 8/8 — horizontal
SPARK = " ▁▂▃▄▅▆▇█"        # 0..8 — vertical spark
FULL = "█"


def hbar(value: float, maxval: float, width: int) -> str:
    """Horizontal eighth-block bar of exactly `width` visual columns."""
    if width <= 0:
        return ""
    if maxval <= 0:
        return " " * width
    ratio = max(0.0, min(1.0, value / maxval))
    eighths = int(round(ratio * width * 8))
    full = eighths // 8
    rem = eighths - full * 8
    out = FULL * full
    if rem > 0 and full < width:
        out += EIGHTH[rem]
        full += 1
    if full < width:
        out += " " * (width - full)
    return out


def sparkline(values: Sequence[float], maxval: float = 0.0, width: int = 0) -> str:
    """Vertical-spark line, one char per value.

    If maxval is 0, auto-scale to max(values). If width > 0, truncate or
    left-pad with spaces so the returned string has exactly `width` chars.
    """
    vals = list(values)
    if width > 0:
        if len(vals) > width:
            vals = vals[-width:]
        else:
            vals = [0.0] * (width - len(vals)) + vals
    if not vals:
        return ""
    if maxval <= 0:
        maxval = max(vals) if vals else 0.0
    if maxval <= 0:
        return " " * len(vals)
    chars = []
    for v in vals:
        r = max(0.0, min(1.0, v / maxval))
        chars.append(SPARK[int(round(r * 8))])
    return "".join(chars)


def vchart(values: Sequence[float], height: int, cell_width: int = 1,
           maxval: float = 0.0) -> list:
    """Return `height` strings representing a top-down btop-style chart.

    Each value is drawn as a column of `cell_width` characters.
    """
    vals = list(values)
    if not vals:
        return [""] * height
    if maxval <= 0:
        maxval = max(vals)
    if maxval <= 0:
        return [" " * (len(vals) * cell_width) for _ in range(height)]
    rows = []
    for row in range(height - 1, -1, -1):
        cells = []
        for v in vals:
            r = max(0.0, min(1.0, v / maxval))
            eighths = int(round(r * height * 8))
            full_rows = eighths // 8
            partial = eighths - full_rows * 8
            if row < full_rows:
                ch = FULL
            elif row == full_rows and partial > 0:
                ch = SPARK[partial]
            else:
                ch = " "
            cells.append(ch * cell_width)
        rows.append("".join(cells))
    return rows


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def human_count(n: float) -> str:
    if n < 1000:
        return f"{int(n)}"
    if n < 1_000_000:
        return f"{n/1000:.1f}k"
    if n < 1_000_000_000:
        return f"{n/1_000_000:.1f}M"
    return f"{n/1_000_000_000:.1f}G"


def human_ms(ms: float) -> str:
    if ms < 1:
        return f"{ms:.2f}"
    if ms < 10:
        return f"{ms:.2f}"
    if ms < 100:
        return f"{ms:.1f}"
    if ms < 10_000:
        return f"{ms:.0f}"
    return f"{ms/1000:.1f}s"
