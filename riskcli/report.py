"""Rendering of the risk report using rich."""
from __future__ import annotations

import math
from typing import Optional

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import utils
from .metrics import TRADING_DAYS, Metrics

DASH = "—"


def fmt_percent(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return DASH
    return f"{x * 100:.2f}%"


def fmt_unit(x: Optional[float]) -> str:
    """Unitless ratios (Sharpe, Sortino, Beta, Calmar) as plain numbers."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return DASH
    return f"{x:.3f}"


def risk_grade(m: Metrics) -> tuple[str, str]:
    """Coarse Low/Medium/High grade from vol, drawdown and tail loss.

    A deep drawdown dominates: an asset that has already lost 60% of its
    value peak-to-trough is not a low-risk holding whatever its vol says.
    """
    score = 0

    if m.annual_vol > 0.50:
        score += 2
    elif m.annual_vol > 0.25:
        score += 1

    if m.max_drawdown > 0.60:
        score += 4
    elif m.max_drawdown > 0.25:
        score += 2
    elif m.max_drawdown > 0.15:
        score += 1

    if m.var_95 < -0.05:
        score += 2
    elif m.var_95 < -0.02:
        score += 1

    if score >= 4:
        return "High", "red"
    if score >= 2:
        return "Medium", "yellow"
    return "Low", "green"


def _var_label(m: Metrics) -> str:
    horizon = "1d" if round(m.periods_per_year) == TRADING_DAYS else "1 bar"
    return f"VaR 95% ({horizon})"


def _summary_grid(ticker: str, meta: dict, df, period: str, benchmark: str, m: Metrics) -> Table:
    currency = meta.get("currency") or ""
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    last_price = float(df[col].dropna().iloc[-1])

    spark_vals = meta.get("_spark_values")
    width = int(meta.get("_spark_width", 32))
    if not spark_vals:
        spark_vals = df[col].dropna().tolist()
    spark = utils.sparkline(spark_vals[-width:])

    # Hug the content: a padded grid keeps the panel narrow enough that two
    # reports still sit side by side in compare mode.
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim")
    grid.add_column()
    grid.add_row("Last", f"{last_price:,.2f} {currency}".strip())
    grid.add_row("Market Cap", utils.human_number(meta.get("market_cap")))
    grid.add_row("Period", f"{period} vs {benchmark}")
    grid.add_row("Bars", f"{len(df)} ({m.periods_per_year:g}/yr)")
    grid.add_row("Spark", spark)
    return grid


def _metrics_table(m: Metrics, currency: str) -> Table:
    t = Table(title="Metrics", box=box.SIMPLE)
    t.add_column("Metric")
    t.add_column("Value", justify="right")

    rows = [
        ("Annual Return (CAGR)", fmt_percent(m.annual_return)),
        ("Annual Vol", fmt_percent(m.annual_vol)),
        ("Sharpe", fmt_unit(m.sharpe)),
        ("Sortino", fmt_unit(m.sortino)),
        ("Max Drawdown", fmt_percent(-m.max_drawdown)),
        ("Calmar", fmt_unit(m.calmar)),
        (_var_label(m), fmt_percent(m.var_95)),
        (_var_label(m).replace("VaR", "CVaR"), fmt_percent(m.cvar_95)),
        ("Skew", fmt_unit(m.skew)),
        ("Excess Kurtosis", fmt_unit(m.excess_kurtosis)),
        ("Beta", fmt_unit(m.beta)),
        ("Alpha (Jensen, annual)", fmt_percent(m.alpha)),
        ("R²", fmt_percent(m.r2)),
        ("Avg Value Traded", f"{utils.human_number(m.avg_daily_dollar_vol)} {currency}".strip()),
    ]
    for k, v in rows:
        t.add_row(k, v)
    return t


def build_report_panel(ticker: str, meta: dict, df, period: str, benchmark: str, m: Metrics) -> Panel:
    """Renderable report for one ticker over one period."""
    header = Text(f"{ticker} — {meta.get('name') or ticker}", style="bold")
    grade, color = risk_grade(m)

    body = Group(
        Panel.fit(_summary_grid(ticker, meta, df, period, benchmark, m), title=header),
        _metrics_table(m, meta.get("currency") or ""),
        Text(f"Risk Grade: {grade}", style=f"bold {color}"),
    )
    return Panel.fit(body, box=box.ROUNDED)
