"""Metric calculations for riskcli.

Small, testable functions for returns, drawdown, VaR, CVaR, beta, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Tuple

import numpy as np
import pandas as pd

TRADING_DAYS = 252


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
    alpha: Optional[float]  # annualized alpha
    r2: Optional[float]
    avg_daily_dollar_vol: float


def _daily_returns(df: pd.DataFrame) -> pd.Series:
    # Use adjusted close if available
    price = df.get("Adj Close") if "Adj Close" in df.columns else df["Close"]
    return price.pct_change().dropna()


def annualized_return(returns: pd.Series) -> float:
    """Geometric annualized return from daily simple returns."""
    r = returns.dropna()
    n = len(r)
    if n == 0:
        return float("nan")
    gross = float((1.0 + r).prod())
    return gross ** (TRADING_DAYS / n) - 1.0


def annualized_vol(returns: pd.Series) -> float:
    return returns.std(ddof=1) * np.sqrt(TRADING_DAYS)


def max_drawdown(series: pd.Series) -> float:
    """Return positive magnitude max drawdown.

    Accepts either price levels or return series.
    """
    s = series.dropna()
    if s.empty:
        return 0.0

    # Heuristic: treat as returns if values mostly within [-1, 1]
    is_returns_like = (s.abs().quantile(0.95) <= 1.0)
    wealth = (1 + s).cumprod() if is_returns_like else (s / s.iloc[0])

    peak = wealth.cummax()
    drawdown = (peak - wealth) / peak
    m = float(drawdown.max())
    return m if m >= 0 else abs(m)


def historical_var_cvar(returns: pd.Series, alpha: float = 0.95) -> Tuple[float, float]:
    r = returns.dropna()
    if r.size < 100:
        return float("nan"), float("nan")
    # percentile for 1-day VaR: (1 - alpha) lower tail
    var = np.nanpercentile(r, 100.0 * (1.0 - alpha))
    tail = r[r <= var]
    cvar = float(np.nanmean(tail)) if tail.size > 0 else float(var)
    return float(var), float(cvar)


def tail_ratio(returns: pd.Series, p_high: float = 0.95, p_low: float = 0.05) -> float:
    high = float(returns.quantile(p_high))
    low = float(returns.quantile(p_low))
    denom = abs(low)
    return high / denom if denom > 0 else float("nan")


def beta_alpha_r2(asset_rets: pd.Series, bench_rets: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if bench_rets is None or bench_rets.empty:
        return None, None, None
    df = pd.concat([asset_rets, bench_rets], axis=1).dropna()
    if df.shape[0] < 2:
        return None, None, None

    x = df.iloc[:, 1].to_numpy()
    y = df.iloc[:, 0].to_numpy()

    xm = x.mean()
    ym = y.mean()
    denom = float(((x - xm) ** 2).sum())
    if denom == 0.0:
        return None, None, None

    beta = float(((x - xm) * (y - ym)).sum() / denom)
    alpha_daily = float(ym - beta * xm)

    # Annualize alpha multiplicatively
    alpha_annual = (1.0 + alpha_daily) ** TRADING_DAYS - 1.0

    y_hat = alpha_daily + beta * x
    ss_res = float(((y - y_hat) ** 2).sum())
    ss_tot = float(((y - ym) ** 2).sum())
    r2 = (1.0 - ss_res / ss_tot) if ss_tot != 0.0 else None

    return beta, alpha_annual, r2


def compute_metrics(asset_df: pd.DataFrame, bench_df: Optional[pd.DataFrame] = None, rf: float = 0.0) -> Metrics:
    ar = _daily_returns(asset_df)
    if ar.empty:
        raise ValueError("Not enough asset returns to compute metrics")

    br = _daily_returns(bench_df) if bench_df is not None else None

    # Daily risk-free from annual rf
    rf_daily = (1.0 + rf) ** (1.0 / TRADING_DAYS) - 1.0
    excess = ar - rf_daily

    # Geometric annual return from start/end prices (transparent and robust)
    price_series = asset_df.get("Adj Close") if "Adj Close" in asset_df.columns else asset_df["Close"]
    prices = price_series.dropna()
    n = len(prices)
    if n >= 2:
        gross = float(prices.iloc[-1] / prices.iloc[0])
        annual_return = gross ** (TRADING_DAYS / n) - 1.0
    else:
        annual_return = float("nan")

    ann_vol = annualized_vol(ar)

    # Sharpe: annualized excess mean over annualized vol
    sharpe = (excess.mean() * TRADING_DAYS) / ann_vol if ann_vol != 0 else float("nan")

    # Sortino: use true lower partial standard deviation vs MAR = rf_daily
    shortfall = np.minimum(0.0, ar - rf_daily)
    lpsd_daily = float(np.sqrt((shortfall ** 2).mean()))  # population LPSD
    downside_ann = lpsd_daily * np.sqrt(TRADING_DAYS) if np.isfinite(lpsd_daily) else 0.0
    sortino = (excess.mean() * TRADING_DAYS) / downside_ann if downside_ann != 0 else float("nan")

    mdd = max_drawdown(prices)
    calmar = (annual_return / mdd) if (mdd and mdd > 0) else None

    var95, cvar95 = historical_var_cvar(ar, 0.95)
    tr = tail_ratio(ar)

    skew = float(pd.Series(ar).skew())
    kurt = float(pd.Series(ar).kurt())  # excess kurtosis (Fisher)

    beta, alpha, r2 = beta_alpha_r2(ar, br) if br is not None else (None, None, None)

    # Liquidity proxy: average daily traded value
    vol = asset_df.get("Volume")
    if vol is None or vol.dropna().empty:
        avg_dollar = 0.0
    else:
        avg_dollar = float((prices * vol.loc[prices.index]).dropna().mean())

    return Metrics(
        annual_return=annual_return,
        annual_vol=ann_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=mdd,
        calmar=calmar,
        var_95=var95,
        cvar_95=cvar95,
        tail_ratio=tr,
        skew=skew,
        excess_kurtosis=kurt,
        beta=beta,
        alpha=alpha,
        r2=r2,
        avg_daily_dollar_vol=avg_dollar,
    )


def metrics_to_dict(m: Metrics) -> Dict[str, object]:
    return {
        "annual_return": m.annual_return,
        "annual_vol": m.annual_vol,
        "sharpe": m.sharpe,
        "sortino": m.sortino,
        "max_drawdown": m.max_drawdown,
        "calmar": m.calmar,
        "var_95": m.var_95,
        "cvar_95": m.cvar_95,
        "tail_ratio": m.tail_ratio,
        "skew": m.skew,
        "excess_kurtosis": m.excess_kurtosis,
        "beta": m.beta,
        "alpha": m.alpha,
        "r2": m.r2,
        "avg_daily_dollar_vol": m.avg_daily_dollar_vol,
    }
