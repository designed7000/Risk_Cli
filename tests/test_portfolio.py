"""Portfolio aggregation tests.

Expected values are derived by hand in the comments. The orthogonal-pair
construction below is used repeatedly: two series with exactly zero sample
correlation and identical volatility, so the diversification arithmetic has a
closed form.
"""
import numpy as np
import pandas as pd
import pytest

from riskcli import portfolio

A = 0.01
# Exactly orthogonal, equal-vol return pairs (dot product a²-a²-a²+a² = 0)
ORTHOGONAL_1 = [A, A, -A, -A]
ORTHOGONAL_2 = [A, -A, A, -A]


def _frame(returns, index=None):
    """OHLCV frame whose pct_change is exactly `returns`."""
    rets = pd.Series(returns, dtype=float)
    prices = [100.0] + list(100.0 * (1 + rets).cumprod())
    idx = index if index is not None else pd.bdate_range("2022-01-03", periods=len(prices))
    s = pd.Series(prices, index=idx)
    return pd.DataFrame({"Close": s, "Adj Close": s, "Volume": 1_000}, index=idx)


def _returns_frame(*columns):
    return pd.DataFrame({name: vals for name, vals in columns})


# --- parsing -------------------------------------------------------------


def test_bare_tickers_get_equal_weights():
    assert portfolio.parse_positions(["AAPL", "MSFT", "TLT"]) == pytest.approx(
        {"AAPL": 1 / 3, "MSFT": 1 / 3, "TLT": 1 / 3}
    )


def test_explicit_weights_are_kept():
    assert portfolio.parse_positions(["AAPL=0.4", "MSFT=0.6"]) == pytest.approx(
        {"AAPL": 0.4, "MSFT": 0.6}
    )


def test_weights_are_normalized_to_sum_to_one():
    # 2:2 and 40:60 are the same portfolio; the report shows the normalized form
    assert portfolio.parse_positions(["AAPL=2", "MSFT=2"]) == pytest.approx(
        {"AAPL": 0.5, "MSFT": 0.5}
    )
    assert portfolio.parse_positions(["A=40", "B=60"]) == pytest.approx({"A": 0.4, "B": 0.6})


def test_short_positions_are_allowed():
    weights = portfolio.parse_positions(["SPY=1.3", "TLT=-0.3"])
    assert weights["TLT"] < 0
    assert sum(weights.values()) == pytest.approx(1.0)


def test_tickers_are_upper_cased():
    assert set(portfolio.parse_positions(["aapl=0.5", "msft=0.5"])) == {"AAPL", "MSFT"}


def test_mixing_weighted_and_bare_tickers_is_rejected():
    with pytest.raises(ValueError, match="all|none"):
        portfolio.parse_positions(["AAPL=0.4", "MSFT"])


def test_weights_summing_to_zero_are_rejected():
    with pytest.raises(ValueError, match="zero"):
        portfolio.parse_positions(["A=1", "B=-1"])


def test_a_non_numeric_weight_is_rejected():
    with pytest.raises(ValueError, match="weight"):
        portfolio.parse_positions(["AAPL=abc", "MSFT=1"])


def test_a_duplicate_ticker_is_rejected():
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        portfolio.parse_positions(["AAPL=0.5", "AAPL=0.5"])


# --- alignment -----------------------------------------------------------


def test_returns_are_aligned_on_dates_the_assets_share():
    long_idx = pd.bdate_range("2022-01-03", periods=11)
    short_idx = pd.bdate_range("2022-01-10", periods=6)
    frames = {
        "A": _frame([0.01] * 10, index=long_idx),
        "B": _frame([0.02] * 5, index=short_idx),
    }
    aligned = portfolio.align_returns(frames)

    assert list(aligned.columns) == ["A", "B"]
    assert len(aligned) == 5  # only the overlap, minus the bar lost to pct_change
    assert aligned.notna().all().all()


def test_disjoint_histories_are_rejected():
    frames = {
        "A": _frame([0.01] * 5, index=pd.bdate_range("2022-01-03", periods=6)),
        "B": _frame([0.01] * 5, index=pd.bdate_range("2023-01-03", periods=6)),
    }
    with pytest.raises(ValueError, match="overlap"):
        portfolio.align_returns(frames)


# --- correlation ---------------------------------------------------------


def test_correlation_matrix_is_symmetric_with_a_unit_diagonal():
    rets = _returns_frame(("A", ORTHOGONAL_1), ("B", ORTHOGONAL_2))
    corr = portfolio.correlation_matrix(rets)

    assert np.allclose(np.diag(corr.to_numpy()), 1.0)
    assert corr.loc["A", "B"] == pytest.approx(corr.loc["B", "A"])


def test_the_orthogonal_pair_has_zero_correlation():
    rets = _returns_frame(("A", ORTHOGONAL_1), ("B", ORTHOGONAL_2))
    assert portfolio.correlation_matrix(rets).loc["A", "B"] == pytest.approx(0.0)


def test_identical_assets_correlate_at_one():
    rets = _returns_frame(("A", ORTHOGONAL_1), ("B", ORTHOGONAL_1))
    assert portfolio.correlation_matrix(rets).loc["A", "B"] == pytest.approx(1.0)


# --- risk decomposition --------------------------------------------------


def test_contributions_sum_to_portfolio_volatility():
    """Euler's theorem: risk contributions must add back to total risk."""
    rng = np.random.RandomState(3)
    rets = pd.DataFrame(rng.normal(0, 0.012, size=(300, 4)), columns=list("ABCD"))
    weights = np.array([0.4, 0.3, 0.2, 0.1])

    sigma, contributions = portfolio.risk_contributions(rets, weights)

    assert contributions.sum() == pytest.approx(sigma)


def test_contributions_hold_for_a_portfolio_with_a_short():
    rng = np.random.RandomState(5)
    rets = pd.DataFrame(rng.normal(0, 0.01, size=(300, 3)), columns=list("ABC"))
    weights = np.array([1.3, -0.4, 0.1])

    sigma, contributions = portfolio.risk_contributions(rets, weights)

    assert contributions.sum() == pytest.approx(sigma)


def test_equal_uncorrelated_assets_contribute_equally():
    rets = _returns_frame(("A", ORTHOGONAL_1), ("B", ORTHOGONAL_2))
    _, contributions = portfolio.risk_contributions(rets, np.array([0.5, 0.5]))
    assert contributions[0] == pytest.approx(contributions[1])


def test_a_concentrated_position_carries_more_risk_than_its_weight():
    # Two uncorrelated equal-vol assets at 90/10. Covariance is diagonal (v), so
    #   CCR_A ∝ 0.9² = 0.81, CCR_B ∝ 0.1² = 0.01
    #   share_A = 0.81 / 0.82 = 98.8%  -- a 90% position is 98.8% of the risk
    rets = _returns_frame(("A", ORTHOGONAL_1), ("B", ORTHOGONAL_2))
    sigma, contributions = portfolio.risk_contributions(rets, np.array([0.9, 0.1]))

    assert contributions[0] / sigma == pytest.approx(0.81 / 0.82)


# --- diversification -----------------------------------------------------


def test_two_uncorrelated_equal_vol_assets_halve_variance():
    # 50/50 of orthogonal equal-vol series: sigma_p = sigma / sqrt(2),
    # so the diversification ratio is exactly sqrt(2).
    frames = {"A": _frame(ORTHOGONAL_1), "B": _frame(ORTHOGONAL_2)}
    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.5})

    assert p.diversification_ratio == pytest.approx(np.sqrt(2))
    assert p.metrics.annual_vol == pytest.approx(p.weighted_avg_vol / np.sqrt(2))


def test_identical_assets_give_no_diversification_benefit():
    frames = {"A": _frame(ORTHOGONAL_1), "B": _frame(ORTHOGONAL_1)}
    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.5})

    assert p.diversification_ratio == pytest.approx(1.0)
    assert p.metrics.annual_vol == pytest.approx(p.weighted_avg_vol)


def test_a_single_holding_has_no_diversification_and_all_the_risk():
    frames = {"A": _frame(ORTHOGONAL_1)}
    p = portfolio.compute_portfolio(frames, {"A": 1.0})

    assert p.diversification_ratio == pytest.approx(1.0)
    assert p.positions[0].risk_share == pytest.approx(1.0)


# --- component VaR -------------------------------------------------------


def test_component_var_sums_to_parametric_var():
    rng = np.random.RandomState(9)
    idx = pd.bdate_range("2022-01-03", periods=301)
    frames = {
        name: _frame(rng.normal(0, 0.011, 300), index=idx) for name in ("A", "B", "C")
    }
    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.3, "C": 0.2})

    assert sum(pos.component_var for pos in p.positions) == pytest.approx(
        p.parametric_var_95
    )


def test_parametric_var_is_a_negative_return():
    frames = {"A": _frame(ORTHOGONAL_1), "B": _frame(ORTHOGONAL_2)}
    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.5})
    assert p.parametric_var_95 < 0


def test_risk_shares_sum_to_one():
    rng = np.random.RandomState(11)
    idx = pd.bdate_range("2022-01-03", periods=301)
    frames = {name: _frame(rng.normal(0, 0.01, 300), index=idx) for name in ("A", "B", "C")}
    p = portfolio.compute_portfolio(frames, {"A": 0.6, "B": 0.3, "C": 0.1})

    assert sum(pos.risk_share for pos in p.positions) == pytest.approx(1.0)


# --- portfolio metrics ---------------------------------------------------


def test_a_portfolio_of_one_matches_the_single_name_report():
    from riskcli import metrics

    frame = _frame([0.01, -0.005, 0.02, -0.01] * 30)
    p = portfolio.compute_portfolio({"A": frame}, {"A": 1.0})
    single = metrics.compute_metrics(frame)

    assert p.metrics.annual_vol == pytest.approx(single.annual_vol)
    assert p.metrics.annual_return == pytest.approx(single.annual_return)
    assert p.metrics.max_drawdown == pytest.approx(single.max_drawdown)


def test_portfolio_returns_are_the_weighted_sum_of_the_legs():
    frames = {"A": _frame([0.10, 0.10]), "B": _frame([0.00, 0.00])}
    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.5})
    # constant 50/50 rebalanced each bar -> 5% a bar, so vol is zero and the
    # wealth index compounds at 1.05
    assert p.metrics.annual_vol == pytest.approx(0.0)
    assert p.metrics.max_drawdown == pytest.approx(0.0)


def test_positions_carry_their_weight_and_vol():
    frames = {"A": _frame(ORTHOGONAL_1), "B": _frame(ORTHOGONAL_2)}
    p = portfolio.compute_portfolio(frames, {"A": 0.7, "B": 0.3})

    by_ticker = {pos.ticker: pos for pos in p.positions}
    assert by_ticker["A"].weight == pytest.approx(0.7)
    assert by_ticker["A"].annual_vol > 0
    assert by_ticker["A"].risk_share > by_ticker["B"].risk_share


def test_positions_are_ordered_by_risk_share():
    frames = {"A": _frame(ORTHOGONAL_1), "B": _frame(ORTHOGONAL_2)}
    p = portfolio.compute_portfolio(frames, {"A": 0.2, "B": 0.8})
    assert [pos.ticker for pos in p.positions] == ["B", "A"]


def test_benchmark_gives_the_portfolio_a_beta():
    rng = np.random.RandomState(13)
    idx = pd.bdate_range("2022-01-03", periods=301)
    bench = _frame(rng.normal(0, 0.008, 300), index=idx)
    frames = {n: _frame(rng.normal(0, 0.012, 300), index=idx) for n in ("A", "B")}

    p = portfolio.compute_portfolio(frames, {"A": 0.5, "B": 0.5}, bench_df=bench)
    assert p.metrics.beta is not None
