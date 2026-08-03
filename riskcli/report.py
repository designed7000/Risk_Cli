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


def _metrics_table(m: Metrics, currency: str, show_liquidity: bool = True) -> Table:
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
    ]
    if show_liquidity:
        # Per-instrument only; there is no meaningful portfolio-level figure.
        rows.append(
            ("Avg Value Traded", f"{utils.human_number(m.avg_daily_dollar_vol)} {currency}".strip())
        )
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


# --- portfolio ------------------------------------------------------------


def _positions_table(p) -> Table:
    """Weight against risk share — the whole point of the portfolio view."""
    t = Table(title="Positions", box=box.SIMPLE)
    t.add_column("Ticker")
    t.add_column("Weight", justify="right")
    t.add_column("Ann Vol", justify="right")
    t.add_column("Corr", justify="right")
    t.add_column("Comp VaR", justify="right")
    t.add_column("% of Risk", justify="right")

    for pos in p.positions:
        # Flag positions carrying materially more risk than their weight.
        over = pos.risk_share > abs(pos.weight) + 0.05
        t.add_row(
            pos.ticker,
            fmt_percent(pos.weight),
            fmt_percent(pos.annual_vol),
            fmt_unit(pos.corr_to_portfolio),
            fmt_percent(pos.component_var),
            Text(fmt_percent(pos.risk_share), style="yellow" if over else ""),
        )
    return t


def _correlation_table(corr) -> Table:
    tickers = list(corr.columns)
    t = Table(title="Correlation", box=box.SIMPLE)
    t.add_column("")
    for name in tickers:
        t.add_column(name, justify="right")

    for row in tickers:
        cells = []
        for col in tickers:
            value = float(corr.loc[row, col])
            style = "dim" if row == col else ("red" if value > 0.8 else "")
            cells.append(Text(fmt_unit(value), style=style))
        t.add_row(Text(row, style="bold"), *cells)
    return t


def _diversification_line(p) -> Text:
    """State the benefit in plain terms, or say there isn't one."""
    ratio = p.diversification_ratio
    if ratio < 1.02:
        return Text(
            f"Diversification: {ratio:.2f}x — holdings move together; "
            "this is one bet in several tickers",
            style="bold red",
        )
    saved = 1.0 - (p.metrics.annual_vol / p.weighted_avg_vol) if p.weighted_avg_vol else 0.0
    return Text(
        f"Diversification: {ratio:.2f}x — {fmt_percent(saved)} less vol than "
        f"holding these separately ({fmt_percent(p.weighted_avg_vol)} weighted avg)",
        style="bold green" if ratio > 1.15 else "bold yellow",
    )


def build_portfolio_panel(p, period: str, benchmark: str) -> Panel:
    """Renderable report for a multi-position portfolio."""
    names = ", ".join(pos.ticker for pos in p.positions)
    header = Text(f"Portfolio — {len(p.positions)} positions", style="bold")

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim")
    summary.add_column()
    summary.add_row("Holdings", names)
    summary.add_row("Period", f"{period} vs {benchmark}")
    summary.add_row("Bars", f"{p.observations} ({p.metrics.periods_per_year:g}/yr)")
    summary.add_row("Parametric VaR 95%", fmt_percent(p.parametric_var_95))

    grade, color = risk_grade(p.metrics)
    body = Group(
        Panel.fit(summary, title=header),
        _positions_table(p),
        _correlation_table(p.correlation),
        _metrics_table(p.metrics, "", show_liquidity=False),
        _diversification_line(p),
        Text(f"Risk Grade: {grade}", style=f"bold {color}"),
    )
    return Panel.fit(body, box=box.ROUNDED)
