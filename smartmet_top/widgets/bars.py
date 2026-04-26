"""Unicode bar / sparkline / chart helpers.

Default rendering uses Braille (U+2800..U+28FF), which packs 2×4 dots
per cell, giving 2× horizontal density and 4× vertical resolution
compared to the eighth-block trick btop calls "block_up".

Each Braille character encodes a *transition* between two consecutive
samples — left column = previous value, right column = current value.
Adjacent characters share a data point (right of `char[i]` is the same
as left of `char[i+1]`), so the graph reads as a continuous line/bar
instead of a sequence of stepped pairs. This means a `width=W` graph
displays `W + 1` samples rather than `2*W`; the trade is half the
horizontal density for a graph that actually looks smooth. btop uses
the same overlap trick.

Horizontal bars (`hbar`) stay on eighth-blocks because horizontally an
eighth-block has 8 sub-cell positions, while a Braille cell only has 2
columns × 4 dots = 4 horizontal positions per pair of cells. The
sub-row trick only pays off vertically.

Set the global mode with `set_ascii(True)` to fall back to eighth-blocks
everywhere (some terminals + the `bstat --ascii` ethos).
"""

from __future__ import annotations

from typing import List, Sequence

EIGHTH = " ▏▎▍▌▋▊▉█"      # 0/8 .. 8/8 — horizontal
SPARK = " ▁▂▃▄▅▆▇█"        # 0..8 — vertical spark (eighth-block)
FULL = "█"
BLANK_BRAILLE = chr(0x2800)

# Braille dot bits per Unicode standard. Counted from the bottom of the
# cell up; level k means "k dots filled from the bottom":
#   left  column: 0x08, 0x04, 0x02, 0x01
#   right column: 0x80, 0x40, 0x20, 0x10
_LEFT_LEVELS = (0x00, 0x08, 0x0C, 0x0E, 0x0F)
_RIGHT_LEVELS = (0x00, 0x80, 0xC0, 0xE0, 0xF0)

_ASCII = False


def set_ascii(enabled: bool) -> None:
    """Force eighth-block (ASCII-friendly) rendering globally."""
    global _ASCII
    _ASCII = bool(enabled)


def is_ascii() -> bool:
    return _ASCII


def _braille_cell(left_lev: int, right_lev: int) -> str:
    return chr(0x2800 + _LEFT_LEVELS[left_lev] + _RIGHT_LEVELS[right_lev])


# ---- horizontal eighth-block bar (mode-independent) ------------------------

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


# ---- vertical spark / chart ------------------------------------------------

def _spark_eighth(values: Sequence[float], maxval: float, width: int) -> str:
    vals = list(values)
    if width > 0:
        if len(vals) > width:
            vals = vals[-width:]
        else:
            vals = [0.0] * (width - len(vals)) + vals
    if not vals:
        return ""
    if maxval <= 0:
        m = max(vals)
        if m <= 0:
            return " " * len(vals)
        maxval = m
    return "".join(
        SPARK[max(0, min(8, int(round(v / maxval * 8))))] for v in vals
    )


def _spark_braille(values: Sequence[float], maxval: float, width: int) -> str:
    if width <= 0:
        return ""
    # Overlapping pairs: `width` chars need `width + 1` data points,
    # so each char's right column equals the next char's left column
    # and the rendered graph stays continuous.
    n_values = width + 1
    vals = list(values)
    if len(vals) > n_values:
        vals = vals[-n_values:]
    elif len(vals) < n_values:
        vals = [0.0] * (n_values - len(vals)) + vals
    if maxval <= 0:
        m = max(vals) if vals else 0.0
        if m <= 0:
            return BLANK_BRAILLE * width
        maxval = m
    levels = [max(0, min(4, int(round(v / maxval * 4)))) for v in vals]
    return "".join(_braille_cell(levels[i], levels[i + 1])
                   for i in range(width))


def sparkline(values: Sequence[float], maxval: float = 0.0, width: int = 0) -> str:
    """Single-row spark line of `width` visual chars.

    In Braille mode adjacent chars share a data point (overlapping
    pairs), so a `width=W` spark needs `W + 1` samples and renders as
    a continuous line. In --ascii mode it falls back to one eighth-block
    char per value.
    """
    if width <= 0:
        n = len(list(values))
        # Braille: one char per (prev, curr) pair → width = n-1 ; but
        # the chart prepends a leading 0 if needed, so width = n is
        # also fine and keeps the visual width predictable.
        width = n if _ASCII else max(0, n - 1)
    if _ASCII:
        return _spark_eighth(values, maxval, width)
    return _spark_braille(values, maxval, width)


def _chart_eighth(values: Sequence[float], height: int, cell_width: int,
                  maxval: float) -> List[str]:
    vals = list(values)
    if not vals:
        return [""] * height
    if maxval <= 0:
        maxval = max(vals)
    if maxval <= 0:
        return [" " * (len(vals) * cell_width) for _ in range(height)]
    rows: List[str] = []
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


def _chart_braille(values: Sequence[float], height: int,
                   maxval: float, width: int = 0) -> List[str]:
    vals = list(values)
    if not vals:
        return [BLANK_BRAILLE * max(0, width)] * height
    if width <= 0:
        # Each rendered char is a transition (vals[i], vals[i+1]); n
        # values produce n-1 chars. Prepend a leading 0 so the first
        # rendered char ramps up from baseline cleanly.
        vals = [0.0] + vals
        width = len(vals) - 1
    else:
        n_values = width + 1
        if len(vals) > n_values:
            vals = vals[-n_values:]
        elif len(vals) < n_values:
            vals = [0.0] * (n_values - len(vals)) + vals
    if maxval <= 0:
        maxval = max(vals) if vals else 0.0
    if maxval <= 0:
        return [BLANK_BRAILLE * width] * height
    total_dots = 4 * height
    levels = [
        max(0, min(total_dots, int(round(v / maxval * total_dots))))
        for v in vals
    ]
    rows: List[str] = []
    for r in range(height):  # 0 = top
        bottom_offset = 4 * (height - 1 - r)
        chars = []
        for i in range(width):
            lv = max(0, min(4, levels[i] - bottom_offset))
            rv = max(0, min(4, levels[i + 1] - bottom_offset))
            chars.append(_braille_cell(lv, rv))
        rows.append("".join(chars))
    return rows


def vchart(values: Sequence[float], height: int, cell_width: int = 1,
           maxval: float = 0.0, width: int = 0) -> List[str]:
    """Return `height` strings forming a top-down vertical chart.

    In Braille mode (default) the chart packs two consecutive values per
    character, so the chart width is `ceil(len(values)/2)` visual columns
    unless `width` is set explicitly. `cell_width` only applies in ASCII
    mode.
    """
    if _ASCII:
        return _chart_eighth(values, height, cell_width, maxval)
    return _chart_braille(values, height, maxval, width)


# ---- human-readable formatters ---------------------------------------------

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
