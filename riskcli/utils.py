"""Small formatting helpers used across the package."""
from __future__ import annotations

from typing import Iterable, Optional

BLOCKS = "▁▂▃▄▅▆▇█"


def human_number(x: Optional[float]) -> str:
    """1_500 -> '1.50K', 3.12e9 -> '3.12B'."""
    if x is None:
        return "n/a"
    x = float(x)
    for unit in ("", "K", "M", "B", "T"):
        if abs(x) < 1000.0:
            return f"{x:.2f}{unit}"
        x /= 1000.0
    return f"{x:.2f}P"


def sparkline(values: Iterable[float]) -> str:
    """Unicode block sparkline, scaled to the min/max of `values`."""
    vals = [float(v) for v in values]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return BLOCKS[0] * len(vals)
    span = hi - lo
    return "".join(BLOCKS[int((v - lo) / span * (len(BLOCKS) - 1))] for v in vals)
