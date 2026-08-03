"""Portfolio aggregation: correlation, diversification and risk attribution.

The point of this module is that portfolio risk is not the weighted average of
position risk. Two things follow from that and neither is visible in a
single-name report:

* **Diversification ratio** — weighted-average volatility divided by actual
  portfolio volatility. 1.0 means the holdings move together and you own one
  bet in several tickers; higher means the correlations are doing work.
* **Risk attribution** — how much of the portfolio's risk each position is
  actually responsible for, which is not its weight. A 90% position in a
  two-asset uncorrelated book carries 98.8% of the risk.

Conventions:

* Weights are constant and rebalanced every bar. A buy-and-hold book drifts
  away from its starting weights; this does not model that.
* Weights are normalized to sum to 1. Shorts (negative weights) are allowed,
  so the normalized weights are reported back in the output.
* Risk contributions use Euler decomposition on the covariance matrix, so they
  sum exactly to portfolio volatility.
* Component VaR is parametric (normal, zero drift) so that it decomposes
  additively. The headline VaR stays historical, matching the single-name
  report — the two will not agree when returns are fat-tailed, which is the
  point of showing both.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import metrics

# Normal 95% quantile. Hardcoded to avoid a scipy dependency for one constant.
Z_95 = 1.6448536269514722


@dataclass
class Position:
    ticker: str
    weight: float
    annual_vol: float
    corr_to_portfolio: float
    component_var: float  # parametric, sums to parametric_var_95
    risk_share: float  # fraction of portfolio volatility, sums to 1


@dataclass
class PortfolioMetrics:
    positions: List[Position]  # ordered by risk share, largest first
    correlation: pd.DataFrame
    metrics: metrics.Metrics  # the portfolio treated as one instrument
    weighted_avg_vol: float
    diversification_ratio: float
    parametric_var_95: float
    periods_per_year: float
    observations: int  # aligned bars the whole book shares


def parse_positions(specs: List[str]) -> Dict[str, float]:
    """Turn `["AAPL=0.4", "MSFT=0.6"]` or `["AAPL", "MSFT"]` into weights.

    Bare tickers are weighted equally. Explicit weights are normalized to sum
    to 1, so `A=2 B=2` and `A=0.5 B=0.5` describe the same portfolio.
    """
    if not specs:
        raise ValueError("No positions given")

    weighted = [s for s in specs if "=" in s]
    if weighted and len(weighted) != len(specs):
        raise ValueError(
            "Give a weight for all positions or none of them: "
            "'AAPL=0.4 MSFT=0.6' or 'AAPL MSFT'"
        )

    raw: Dict[str, float] = {}
    for spec in specs:
        ticker, _, weight = spec.partition("=")
        ticker = ticker.strip().upper()
        if not ticker:
            raise ValueError(f"Empty ticker in '{spec}'")
        if ticker in raw:
            raise ValueError(f"Duplicate ticker '{ticker}'")
        if weight:
            try:
                raw[ticker] = float(weight)
            except ValueError:
                raise ValueError(f"Bad weight '{weight}' for {ticker}") from None
        else:
            raw[ticker] = 1.0

    total = sum(raw.values())
    if abs(total) < 1e-9:
        raise ValueError("Weights sum to zero; the portfolio has no net exposure")
    return {t: w / total for t, w in raw.items()}


def align_returns(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-bar returns for each holding, on the dates they all share."""
    series = {t: metrics._prices(df).pct_change() for t, df in frames.items()}
    aligned = pd.DataFrame(series).dropna()
    if aligned.empty:
        raise ValueError(
            "Holdings have no overlapping price history; try a shorter period"
        )
    return aligned


def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.corr()


def _safe_corr(a: pd.Series, b: pd.Series) -> float:
    """Correlation, or NaN when either leg never moves.

    A holding with constant returns — a halted name, or a cash leg — has no
    correlation to anything. numpy would divide by a zero standard deviation
    and warn; the answer is simply undefined.
    """
    if a.std(ddof=1) == 0 or b.std(ddof=1) == 0:
        return float("nan")
    return float(a.corr(b))


def risk_contributions(
    returns: pd.DataFrame, weights: np.ndarray
) -> Tuple[float, np.ndarray]:
    """Euler decomposition of portfolio volatility (per bar).

    Returns (portfolio vol, per-asset contributions). The contributions sum to
    the portfolio vol by construction, so each one is that position's share of
    total risk rather than its standalone risk.
    """
    cov = returns.cov().to_numpy()
    variance = float(weights @ cov @ weights)
    sigma = float(np.sqrt(max(variance, 0.0)))
    if sigma == 0.0:
        return 0.0, np.zeros_like(weights, dtype=float)

    marginal = (cov @ weights) / sigma  # d(sigma)/d(w_i)
    return sigma, weights * marginal


def compute_portfolio(
    frames: Dict[str, pd.DataFrame],
    weights: Dict[str, float],
    bench_df: Optional[pd.DataFrame] = None,
    rf: float = 0.0,
) -> PortfolioMetrics:
    """Aggregate `frames` into one portfolio and attribute its risk."""
    returns = align_returns(frames)
    tickers = list(returns.columns)
    w = np.array([weights[t] for t in tickers], dtype=float)

    ppy = metrics.infer_periods_per_year(returns.index)
    sqrt_ppy = np.sqrt(ppy)

    port_returns = returns.to_numpy() @ w
    port_returns = pd.Series(port_returns, index=returns.index)

    sigma, contributions = risk_contributions(returns, w)
    port_vol_annual = sigma * sqrt_ppy

    # Standalone annualized vol per holding, and the naive sum of them.
    vols = returns.std(ddof=1).to_numpy() * sqrt_ppy
    weighted_avg_vol = float(np.abs(w) @ vols)
    diversification = (weighted_avg_vol / port_vol_annual) if port_vol_annual else 1.0

    parametric_var = -Z_95 * sigma
    shares = contributions / sigma if sigma else np.zeros_like(w)

    corr_to_port = np.array(
        [_safe_corr(returns[t], port_returns) for t in tickers], dtype=float
    )

    positions = [
        Position(
            ticker=t,
            weight=float(w[i]),
            annual_vol=float(vols[i]),
            corr_to_portfolio=corr_to_port[i],
            component_var=float(-Z_95 * contributions[i]),
            risk_share=float(shares[i]),
        )
        for i, t in enumerate(tickers)
    ]
    positions.sort(key=lambda p: p.risk_share, reverse=True)

    # A wealth index starting at 1.0 before the first return, so the drawdown
    # sees the whole path.
    wealth = pd.Series([1.0] + list((1.0 + port_returns).cumprod()))

    bench_returns = None
    if bench_df is not None and not bench_df.empty:
        bench_returns = metrics._prices(bench_df).pct_change().dropna()

    return PortfolioMetrics(
        positions=positions,
        correlation=correlation_matrix(returns),
        metrics=metrics.metrics_from_returns(
            port_returns,
            wealth=wealth,
            bench_returns=bench_returns,
            rf=rf,
            periods_per_year=ppy,
        ),
        weighted_avg_vol=weighted_avg_vol,
        diversification_ratio=float(diversification),
        parametric_var_95=float(parametric_var),
        periods_per_year=ppy,
        observations=len(returns),
    )
