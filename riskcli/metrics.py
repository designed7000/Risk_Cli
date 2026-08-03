"""Metric calculations for riskcli.

Small, testable functions for returns, drawdown, VaR, CVaR, beta, etc.

Conventions, so the numbers are unambiguous:

* Returns are simple (not log) and come from adjusted closes, so dividends
  are included.
* Annualization uses the bar frequency inferred from the index, not a fixed
  252 — weekly, monthly and intraday series annualize correctly.
* ``annual_return`` is geometric (CAGR). The Sharpe and Sortino numerators
  use the *arithmetic* mean excess return, which is the standard convention
  and is not the same number as the CAGR.
* ``max_drawdown`` is a positive magnitude: 0.35 means a 35% peak-to-trough
  loss. The report renders it negative.
* VaR/CVaR are single-bar historical (non-parametric) figures at the given
  confidence, expressed as negative returns.
* ``alpha`` is Jensen's alpha: the intercept of excess asset returns
  regressed on excess benchmark returns, then annualized.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MIN_VAR_OBSERVATIONS = 100


@dataclass
class Metrics:
    annual_return: float
    annual_vol: float
    sharpe: Optional[float]
    sortino: Optional[float]
    max_drawdown: float  # positive magnitude
    calmar: Optional[float]
    var_95: float
    cvar_95: float
    tail_ratio: float
    skew: float
    excess_kurtosis: float
    beta: Optional[float]
    alpha: Optional[float]  # annualized Jensen's alpha
    r2: Optional[float]
    avg_daily_dollar_vol: float
    periods_per_year: float


def infer_periods_per_year(index) -> float:
    """Annualization factor implied by the spacing of `index`.

    Daily bars give 252, weekly 52, monthly 12; intraday bars give 252 times
    the number of bars in a session. Falls back to 252 when the index is too
    short to tell.
    """
    idx = pd.DatetimeIndex(index)
    if len(idx) < 3:
        return float(TRADING_DAYS)

    # Convert explicitly: `asi8` is in whatever unit the index happens to
    # carry, which varies by pandas version and constructor.
    gaps = np.diff(idx.values).astype("timedelta64[ms]").astype(np.float64)
    spacing = float(np.median(gaps)) / 86_400_000.0  # milliseconds -> days
    if spacing <= 0:
        return float(TRADING_DAYS)

    if spacing < 1.0:
        # Intraday. Count the bars in a session rather than assuming its
        # length, so a 6.5h US session and a 24h crypto tape both work.
        sessions = idx.normalize().nunique()
        return float(TRADING_DAYS) * (len(idx) / sessions)
    if spacing <= 4.0:
        return float(TRADING_DAYS)  # daily bars; weekend gaps are the minority
    if spacing <= 10.0:
        return 52.0
    if spacing <= 45.0:
        return 12.0
    return 365.25 / spacing


def _prices(df: pd.DataFrame) -> pd.Series:
    """Adjusted close if present, otherwise close."""
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    return pd.Series(df[col]).dropna().astype(float)


def annualized_return(returns: pd.Series, periods_per_year: float = TRADING_DAYS) -> float:
    """Geometric annualized return (CAGR) from simple periodic returns."""
    r = pd.Series(returns).dropna()
    if r.empty:
        return float("nan")
    gross = float((1.0 + r).prod())
    if gross <= 0.0:
        return -1.0
    return gross ** (periods_per_year / len(r)) - 1.0


def annualized_vol(returns: pd.Series, periods_per_year: float = TRADING_DAYS) -> float:
    r = pd.Series(returns).dropna()
    if len(r) < 2:
        return float("nan")
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def max_drawdown(prices: pd.Series) -> float:
    """Worst peak-to-trough loss over a *price* series, as a positive fraction.

    Takes prices, never returns — guessing which one it was given produced
    silently wrong answers on sub-$1 series.
    """
    p = pd.Series(prices).dropna().astype(float)
    p = p[p > 0]
    if p.empty:
        return 0.0
    peak = p.cummax()
    return float(((peak - p) / peak).max())


def historical_var_cvar(returns: pd.Series, alpha: float = 0.95) -> Tuple[float, float]:
    """Single-bar historical VaR and CVaR, returned as negative fractions."""
    r = pd.Series(returns).dropna()
    if r.size < MIN_VAR_OBSERVATIONS:
        return float("nan"), float("nan")
    var = float(np.nanpercentile(r, 100.0 * (1.0 - alpha)))
    tail = r[r <= var]
    cvar = float(np.nanmean(tail)) if tail.size > 0 else var
    return var, cvar


def tail_ratio(returns: pd.Series, p_high: float = 0.95, p_low: float = 0.05) -> float:
    r = pd.Series(returns).dropna()
    if r.empty:
        return float("nan")
    high = float(r.quantile(p_high))
    low = float(r.quantile(p_low))
    return high / abs(low) if low != 0 else float("nan")


def beta_alpha_r2(
    asset_rets: pd.Series,
    bench_rets: Optional[pd.Series],
    rf_period: float = 0.0,
    periods_per_year: float = TRADING_DAYS,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """CAPM regression of excess asset returns on excess benchmark returns.

    Returns (beta, annualized Jensen's alpha, R²). `rf_period` is the
    risk-free rate over one bar, not per year.
    """
    if bench_rets is None or len(bench_rets) == 0:
        return None, None, None

    df = pd.concat([asset_rets, bench_rets], axis=1).dropna()
    if df.shape[0] < 2:
        return None, None, None

    y = df.iloc[:, 0].to_numpy(dtype=float) - rf_period  # excess asset
    x = df.iloc[:, 1].to_numpy(dtype=float) - rf_period  # excess benchmark

    xm, ym = x.mean(), y.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom == 0.0:
        return None, None, None

    beta = float(((x - xm) * (y - ym)).sum() / denom)
    alpha_period = float(ym - beta * xm)
    alpha_annual = (1.0 + alpha_period) ** periods_per_year - 1.0

    resid = y - (alpha_period + beta * x)
    ss_tot = float(((y - ym) ** 2).sum())
    r2 = (1.0 - float((resid**2).sum()) / ss_tot) if ss_tot != 0.0 else None

    return beta, alpha_annual, r2


def compute_metrics(
    asset_df: pd.DataFrame,
    bench_df: Optional[pd.DataFrame] = None,
    rf: float = 0.0,
) -> Metrics:
    """Full metric set for one instrument. `rf` is an annual decimal rate."""
    prices = _prices(asset_df)
    if len(prices) < 2:
        raise ValueError("Need at least two price observations to compute metrics")

    ppy = infer_periods_per_year(prices.index)
    ar = prices.pct_change().dropna()
    if ar.empty:
        raise ValueError("Not enough asset returns to compute metrics")

    br = None
    if bench_df is not None and not bench_df.empty:
        br = _prices(bench_df).pct_change().dropna()

    return metrics_from_returns(
        ar,
        wealth=prices,
        bench_returns=br,
        rf=rf,
        periods_per_year=ppy,
        avg_value_traded=_avg_value_traded(asset_df),
    )


def metrics_from_returns(
    returns: pd.Series,
    wealth: pd.Series,
    bench_returns: Optional[pd.Series] = None,
    rf: float = 0.0,
    periods_per_year: float = TRADING_DAYS,
    avg_value_traded: float = 0.0,
) -> Metrics:
    """Metric set for any return stream, given its wealth index for drawdown.

    Shared by the single-name path and the portfolio path so both report the
    same quantities computed the same way.
    """
    ar = returns
    br = bench_returns
    ppy = periods_per_year
    prices = wealth

    rf_period = (1.0 + rf) ** (1.0 / ppy) - 1.0
    excess = ar - rf_period

    ann_return = annualized_return(ar, ppy)
    ann_vol = annualized_vol(ar, ppy)
    sharpe = (excess.mean() * ppy) / ann_vol if ann_vol else float("nan")

    # Sortino: lower partial standard deviation below the risk-free MAR.
    shortfall = np.minimum(0.0, ar - rf_period)
    downside_ann = float(np.sqrt((shortfall**2).mean())) * np.sqrt(ppy)
    sortino = (excess.mean() * ppy) / downside_ann if downside_ann else float("nan")

    mdd = max_drawdown(prices)
    calmar = (ann_return / mdd) if mdd > 0 else None

    var95, cvar95 = historical_var_cvar(ar, 0.95)
    beta, alpha, r2 = beta_alpha_r2(ar, br, rf_period, ppy)

    return Metrics(
        annual_return=ann_return,
        annual_vol=ann_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=mdd,
        calmar=calmar,
        var_95=var95,
        cvar_95=cvar95,
        tail_ratio=tail_ratio(ar),
        skew=float(ar.skew()),
        excess_kurtosis=float(ar.kurt()),  # Fisher: 0 is normal
        beta=beta,
        alpha=alpha,
        r2=r2,
        avg_daily_dollar_vol=avg_value_traded,
        periods_per_year=ppy,
    )


def _avg_value_traded(df: pd.DataFrame) -> float:
    """Average value traded per bar, from *unadjusted* close times volume."""
    if "Volume" not in df.columns or "Close" not in df.columns:
        return 0.0
    traded = (df["Close"].astype(float) * df["Volume"].astype(float)).dropna()
    return float(traded.mean()) if not traded.empty else 0.0


def metrics_to_dict(m: Metrics) -> Dict[str, object]:
    return asdict(m)
