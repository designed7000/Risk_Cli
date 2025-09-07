"""Small utility helpers used across the package.
"""
from __future__ import annotations

from typing import Iterable


def human_number(x: float) -> str:
    if x is None:
        return "n/a"
    x = float(x)
    for unit in ["", "K", "M", "B", "T"]:
        if abs(x) < 1000.0:
            return f"{x:3.2f}{unit}"
        x /= 1000.0
    return f"{x:.2f}P"


def pct(x: float) -> str:
    if x is None:
        return "n/a"
    return f"{x * 100:.2f}%"


def sparkline(values: Iterable[float]) -> str:
    # simple unicode sparkline
    blocks = "▁▂▃▄▅▆▇█"
    vals = [float(v) for v in values]
    if not vals:
        return ""
    mn, mx = min(vals), max(vals)
    if mx - mn == 0:
        return blocks[0] * len(vals)
    out = "".join(blocks[int((v - mn) / (mx - mn) * (len(blocks) - 1))] for v in vals)
    return out
