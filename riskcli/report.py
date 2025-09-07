"""Rendering of risk report using rich.
"""
from __future__ import annotations

from typing import Optional
import math

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from . import utils
from .metrics import Metrics

console = Console()


def _risk_grade(m: Metrics) -> tuple[str, str]:
    # simple heuristic: high vol > 0.5, mdd < -0.5, var95 < -0.05
    score = 0
    if m.annual_vol > 0.5:
        score += 2
    elif m.annual_vol > 0.25:
        score += 1
    if m.max_drawdown < -0.5:
        score += 2
    elif m.max_drawdown < -0.25:
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


def render_report(ticker: str, meta: dict, df, period: str, benchmark: str, m: Metrics) -> None:
    name = meta.get("name") or ticker
    currency = meta.get("currency") or ""
    last_price = float(df["Adj Close"].dropna().iloc[-1])
    mcap = meta.get("market_cap")
    points = len(df)

    spark = utils.sparkline(df["Adj Close"].dropna().values[-32:])

    header = Text(f"{ticker} — {name}")
    header.stylize("bold")

    grade, color = _risk_grade(m)

    # summary panel
    summary = Table.grid(expand=True)
    summary.add_column(ratio=2)
    summary.add_column(ratio=3)
    summary.add_row("Last", f"{last_price:.2f} {currency}")
    summary.add_row("Market Cap", f"{mcap}")
    summary.add_row("Period", f"{period} vs {benchmark}")
    summary.add_row("Points", str(points))
    summary.add_row("Spark", spark)

    # metrics table
    t = Table(title="Metrics", box=box.SIMPLE)
    t.add_column("Metric")
    t.add_column("Value", justify="right")

    def fmt_percent(x: float) -> str:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x*100:.2f}%"

    def fmt_unit(x: float) -> str:
        # unitless ratios: show as plain numbers
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x:.3f}"

    def fmt_r2(x: float) -> str:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x*100:.2f}%"

    # format ADV with currency
    adv_label = f"{utils.human_number(m.avg_daily_dollar_vol)} {currency}".strip()

    rows = [
        ("Annual Return", fmt_percent(m.annual_return)),
        ("Annual Vol", fmt_percent(m.annual_vol)),
        ("Sharpe", fmt_unit(m.sharpe)),
        ("Sortino", fmt_unit(m.sortino)),
        ("Max Drawdown", f"{m.max_drawdown*100:.2f}%" if m.max_drawdown is not None else "—"),
        ("Calmar", fmt_unit(m.calmar)),
        ("VaR(95%)", fmt_percent(m.var_95)),
        ("CVaR(95%)", fmt_percent(m.cvar_95)),
        ("Beta", fmt_unit(m.beta)),
        ("Alpha (annual)", fmt_percent(m.alpha)),
        ("R^2", fmt_r2(m.r2)),
        ("Avg Daily Value Traded", adv_label),
    ]

    for k, v in rows:
        t.add_row(k, v)

    grade_text = Text(f"Risk Grade: {grade}", style=f"bold {color}")

    console.print(Panel(summary, title=header))
    console.print(t)
    console.print(grade_text)


def build_report_panel(ticker: str, meta: dict, df, period: str, benchmark: str, m: Metrics):
    """Return a Panel representing the report for side-by-side display."""
    name = meta.get("name") or ticker
    currency = meta.get("currency") or ""
    last_price = float(df["Adj Close"].dropna().iloc[-1])
    mcap = meta.get("market_cap")
    points = len(df)

    # use provided spark values if available
    spark_vals = meta.get("_spark_values")
    if spark_vals:
        spark = utils.sparkline(spark_vals[-meta.get("_spark_width", 32):])
    else:
        spark = utils.sparkline(df["Adj Close"].dropna().values[-32:])

    header = Text(f"{ticker} — {name}")
    header.stylize("bold")

    grade, color = _risk_grade(m)

    summary = Table.grid(expand=True)
    summary.add_column(ratio=2)
    summary.add_column(ratio=3)
    summary.add_row("Last", f"{last_price:.2f} {currency}")
    summary.add_row("Market Cap", f"{mcap}")
    summary.add_row("Period", f"{period} vs {benchmark}")
    summary.add_row("Points", str(points))
    summary.add_row("Spark", spark)

    t = Table(title="Metrics", box=box.SIMPLE)
    t.add_column("Metric")
    t.add_column("Value", justify="right")

    def fmt_percent(x: float) -> str:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x*100:.2f}%"

    def fmt_unit(x: float) -> str:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x:.3f}"

    def fmt_r2(x: float) -> str:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return "—"
        return f"{x*100:.2f}%"

    adv_label = f"{utils.human_number(m.avg_daily_dollar_vol)} {currency}".strip()

    rows = [
        ("Annual Return", fmt_percent(m.annual_return)),
        ("Annual Vol", fmt_percent(m.annual_vol)),
        ("Sharpe", fmt_unit(m.sharpe)),
        ("Sortino", fmt_unit(m.sortino)),
        ("Max Drawdown", f"{m.max_drawdown*100:.2f}%" if m.max_drawdown is not None else "—"),
        ("Calmar", fmt_unit(m.calmar)),
        ("VaR(95%)", fmt_percent(m.var_95)),
        ("CVaR(95%)", fmt_percent(m.cvar_95)),
        ("Beta", fmt_unit(m.beta)),
        ("Alpha (annual)", fmt_percent(m.alpha)),
        ("R^2", fmt_r2(m.r2)),
        ("Avg Daily Value Traded", adv_label),
    ]

    for k, v in rows:
        t.add_row(k, v)

    grade_text = Text(f"Risk Grade: {grade}", style=f"bold {color}")

    panel = Panel.fit(summary, title=header)
    # combine summary panel and metrics table into one Group-like Panel
    from rich.console import Group

    group = Group(panel, t, grade_text)
    return Panel(group, box=box.ROUNDED)
