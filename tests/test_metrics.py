"""Metric tests.

Expected values here are derived by hand (shown in comments), not by running
the implementation, so a regression in the formula fails the test.
"""
import numpy as np
import pandas as pd
import pytest

from riskcli import metrics


# --- annualization factor ------------------------------------------------


def test_daily_bars_annualize_at_252():
    idx = pd.bdate_range("2022-01-03", periods=252)
    assert metrics.infer_periods_per_year(idx) == pytest.approx(252.0)


def test_calendar_day_bars_annualize_at_365():
    # A 24/7 tape (crypto) has the same one-day median spacing as an exchange
    # tape, so only the weekend bars distinguish them. At 252 a coin that
    # doubles in a year would report a 61% CAGR.
    idx = pd.date_range("2022-01-01", periods=365, freq="D")
    assert metrics.infer_periods_per_year(idx) == pytest.approx(365.25)


def test_hourly_crypto_bars_annualize_on_calendar_days():
    # 24 bars a day, every day -> 365.25 * 24
    days = pd.date_range("2022-01-01", periods=30, freq="D")
    idx = pd.DatetimeIndex([d + pd.Timedelta(hours=h) for d in days for h in range(24)])
    assert metrics.infer_periods_per_year(idx) == pytest.approx(365.25 * 24)


def test_hourly_bars_annualize_at_bars_per_session_times_252():
    # 7 bars a day for 60 sessions -> 252 * 7 = 1764 periods a year
    days = pd.bdate_range("2022-01-03", periods=60)
    idx = pd.DatetimeIndex(
        [d + pd.Timedelta(hours=h) for d in days for h in range(10, 17)]
    )
    assert metrics.infer_periods_per_year(idx) == pytest.approx(1764.0)


def test_weekly_bars_annualize_at_52():
    idx = pd.date_range("2022-01-03", periods=104, freq="W")
    assert metrics.infer_periods_per_year(idx) == pytest.approx(52.0)


def test_monthly_bars_annualize_at_12():
    idx = pd.date_range("2022-01-31", periods=36, freq="ME")
    assert metrics.infer_periods_per_year(idx) == pytest.approx(12.0)


@pytest.mark.parametrize("unit", ["ns", "us", "ms", "s"])
def test_annualization_ignores_the_index_time_resolution(unit):
    # pandas hands back datetime64 in varying units depending on version and
    # constructor; the annualization factor must not depend on which.
    idx = pd.date_range("2022-01-03", periods=104, freq="W").as_unit(unit)
    assert metrics.infer_periods_per_year(idx) == pytest.approx(52.0)


# --- drawdown ------------------------------------------------------------


def test_max_drawdown_is_positive_magnitude():
    # peak 120, trough 60 -> 50%
    prices = pd.Series([100.0, 120.0, 60.0, 90.0])
    assert metrics.max_drawdown(prices) == pytest.approx(0.5)


def test_max_drawdown_handles_sub_dollar_prices():
    # peak 0.90, trough 0.40 -> (0.90 - 0.40) / 0.90 = 0.5555...
    prices = pd.Series([0.80, 0.90, 0.40, 0.50, 0.60])
    assert metrics.max_drawdown(prices) == pytest.approx(0.5 / 0.9)


def test_max_drawdown_of_monotonic_series_is_zero():
    assert metrics.max_drawdown(pd.Series([1.0, 2.0, 3.0])) == pytest.approx(0.0)


def test_max_drawdown_counts_a_drop_to_zero_in_full():
    # Filtering the zero out would splice the 100 peak onto the 80 recovery
    # and report 50%, hiding a total loss.
    assert metrics.max_drawdown(pd.Series([100.0, 50.0, 0.0, 80.0])) == pytest.approx(1.0)


def test_max_drawdown_of_an_all_zero_series_is_zero_not_nan():
    assert metrics.max_drawdown(pd.Series([0.0, 0.0, 0.0])) == pytest.approx(0.0)


def test_a_zero_tick_does_not_blank_the_whole_report():
    # A zero mid-series makes the recovery bar an infinite return, which turns
    # the compounded return into NaN and slips past the `gross <= 0` guard.
    # The report used to show "—" for return, vol, Sharpe and skew, a -20%
    # drawdown, and a green "Low" risk grade.
    idx = pd.bdate_range("2022-01-03", periods=201)
    prices = pd.Series([100.0] * 100 + [0.0] + [80.0] * 100, index=idx)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 1_000}, index=idx)

    m = metrics.compute_metrics(df, None)

    assert np.isfinite(m.annual_return)
    assert np.isfinite(m.annual_vol)
    assert m.max_drawdown == pytest.approx(0.2)  # the bad tick is dropped, 100 -> 80


# --- return / vol --------------------------------------------------------


def test_annualized_return_over_exactly_one_year():
    # 252 returns compounding to 1.21x over one year -> 21%
    r = pd.Series([1.21 ** (1 / 252) - 1] * 252)
    assert metrics.annualized_return(r, 252) == pytest.approx(0.21)


def test_annualized_return_scales_by_periods_per_year():
    # 12 monthly returns compounding to 1.21x -> still 21% a year
    r = pd.Series([1.21 ** (1 / 12) - 1] * 12)
    assert metrics.annualized_return(r, 12) == pytest.approx(0.21)


def test_annualized_vol_uses_sample_stdev_and_sqrt_time():
    # +1%/-1% alternating: sample stdev is exactly 0.01 when the mean is 0
    r = pd.Series([0.01, -0.01] * 50)
    expected = 0.01 * np.sqrt(252) * np.sqrt(100 / 99)
    assert metrics.annualized_vol(r, 252) == pytest.approx(expected)


# --- VaR / CVaR ----------------------------------------------------------


def test_historical_var_and_cvar_at_95():
    # 20 losses of -10% out of 200 obs: the 5th percentile sits inside the
    # loss block, so VaR = -0.10 and CVaR is the mean of that block.
    r = pd.Series([-0.10] * 20 + [0.01] * 180)
    var, cvar = metrics.historical_var_cvar(r, 0.95)
    assert var == pytest.approx(-0.10)
    assert cvar == pytest.approx(-0.10)


def test_var_is_nan_on_short_sample():
    var, cvar = metrics.historical_var_cvar(pd.Series([0.01, -0.02, 0.005]))
    assert np.isnan(var) and np.isnan(cvar)


# --- beta / alpha --------------------------------------------------------


def test_beta_and_jensen_alpha_recovered_exactly():
    # Build asset returns so the CAPM regression has a known answer:
    #   r_a - rf = 0.0002 + 1.5 * (r_b - rf)
    rf_annual = 0.04
    rf_daily = (1 + rf_annual) ** (1 / 252) - 1
    bench = pd.Series(np.random.RandomState(7).normal(0, 0.01, 252))
    asset = rf_daily + 0.0002 + 1.5 * (bench - rf_daily)

    beta, alpha, r2 = metrics.beta_alpha_r2(asset, bench, rf_daily, 252)

    assert beta == pytest.approx(1.5)
    # Scaled, not compounded, so it matches how the Sharpe numerator is
    # annualized and stays stable at intraday frequencies.
    assert alpha == pytest.approx(0.0002 * 252)
    assert r2 == pytest.approx(1.0)


def test_alpha_is_net_of_the_risk_free_rate():
    # Same series, but told rf is zero: alpha must come out different.
    rf_daily = (1.04) ** (1 / 252) - 1
    bench = pd.Series(np.random.RandomState(7).normal(0, 0.01, 252))
    asset = rf_daily + 0.0002 + 1.5 * (bench - rf_daily)

    _, alpha_with_rf, _ = metrics.beta_alpha_r2(asset, bench, rf_daily, 252)
    _, alpha_no_rf, _ = metrics.beta_alpha_r2(asset, bench, 0.0, 252)

    assert alpha_with_rf != pytest.approx(alpha_no_rf)


def test_beta_is_none_without_a_benchmark():
    assert metrics.beta_alpha_r2(pd.Series([0.01, 0.02]), None) == (None, None, None)


def test_regression_is_none_on_too_short_an_overlap():
    # Two points define a line exactly, so the old n >= 2 guard reported
    # R² = 100%. Reachable from the CLI via --period 3mo --interval 1mo.
    asset = pd.Series([0.04, -0.064815])
    bench = pd.Series([0.02, -0.019231])
    assert metrics.beta_alpha_r2(asset, bench, 0.0, 12.0) == (None, None, None)


def test_regression_survives_a_long_enough_overlap():
    rs = np.random.RandomState(3)
    bench = pd.Series(rs.normal(0, 0.01, metrics.MIN_REGRESSION_OBSERVATIONS))
    beta, alpha, r2 = metrics.beta_alpha_r2(bench * 1.2, bench, 0.0, 252)
    assert beta == pytest.approx(1.2)
    assert r2 == pytest.approx(1.0)


# --- sortino -------------------------------------------------------------


def test_sortino_uses_lower_partial_stdev():
    # Half +2%, half -1%, rf = 0.
    #   mean excess       = 0.005      -> 1.26 annualized
    #   LPSD              = sqrt(0.5 * 0.01^2) = 0.00707107
    #   downside vol      = 0.00707107 * sqrt(252) = 0.1122494
    #   sortino           = 1.26 / 0.1122494 = 11.2250...
    r = pd.Series([0.02, -0.01] * 100)
    # 201 prices so pct_change yields exactly the 200 returns above
    idx = pd.bdate_range("2022-01-03", periods=201)
    prices = pd.Series([100.0] + list(100 * (1 + r).cumprod()), index=idx)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 0}, index=idx)

    m = metrics.compute_metrics(df, None, rf=0.0)

    expected = (0.005 * 252) / (np.sqrt(0.5 * 0.01**2) * np.sqrt(252))
    assert m.sortino == pytest.approx(expected)


# --- liquidity -----------------------------------------------------------


def test_avg_value_traded_uses_unadjusted_close():
    # Adjusted close understates traded value; value traded wants raw close.
    idx = pd.bdate_range("2022-01-03", periods=10)
    df = pd.DataFrame(
        {"Close": 100.0, "Adj Close": 90.0, "Volume": 1_000},
        index=idx,
    )
    m = metrics.compute_metrics(df, None)
    assert m.avg_value_traded_per_bar == pytest.approx(100_000.0)


def test_avg_value_traded_is_per_bar_not_per_day():
    # Weekly bars carry a week of volume; the field name has to say "per bar"
    # because an export consumer cannot tell the interval from the number.
    idx = pd.date_range("2022-01-03", periods=10, freq="W")
    df = pd.DataFrame({"Close": 100.0, "Adj Close": 100.0, "Volume": 5_000}, index=idx)
    assert metrics.compute_metrics(df, None).avg_value_traded_per_bar == pytest.approx(500_000.0)


# --- sharpe, tail ratio and the shape statistics -------------------------


def test_sharpe_is_mean_excess_over_vol():
    # +1%/-1% alternating, rf = 0: mean excess is exactly 0, so Sharpe is 0.
    r = pd.Series([0.01, -0.01] * 100)
    idx = pd.bdate_range("2022-01-03", periods=201)
    prices = pd.Series([100.0] + list(100 * (1 + r).cumprod()), index=idx)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 0}, index=idx)
    assert metrics.compute_metrics(df, None, rf=0.0).sharpe == pytest.approx(0.0, abs=1e-12)


def test_sharpe_is_net_of_the_risk_free_rate():
    # A flat +0.05%/bar series: mean excess = 0.0005 - rf_period, and vol is
    # 0, so Sharpe is NaN. Use a series with real dispersion instead.
    r = pd.Series([0.02, -0.01] * 100)
    idx = pd.bdate_range("2022-01-03", periods=201)
    prices = pd.Series([100.0] + list(100 * (1 + r).cumprod()), index=idx)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 0}, index=idx)

    rf = 0.04
    rf_period = (1 + rf) ** (1 / 252) - 1
    m = metrics.compute_metrics(df, None, rf=rf)

    expected = ((0.005 - rf_period) * 252) / m.annual_vol
    assert m.sharpe == pytest.approx(expected)


def test_tail_ratio_compares_the_two_tails_in_magnitude():
    # 101 observations, so the 5th and 95th percentiles land exactly on the
    # values at positions 5 and 95: -0.02 and +0.04 -> 2.0
    r = pd.Series([-0.02] * 6 + [0.0] * 89 + [0.04] * 6)
    assert metrics.tail_ratio(r) == pytest.approx(2.0)


def test_tail_ratio_stays_positive_on_a_losing_series():
    # Same layout, both tails negative: q05 = -0.20, q95 = -0.02. Taking the
    # tails in magnitude gives 0.1; the signed form returned -0.1.
    r = pd.Series([-0.20] * 6 + [-0.10] * 89 + [-0.02] * 6)
    assert metrics.tail_ratio(r) == pytest.approx(0.1)


def test_skew_and_excess_kurtosis_of_a_symmetric_series():
    # Symmetric two-point series: zero skew, and excess kurtosis of -2
    # (a Bernoulli's kurtosis is 1, Fisher subtracts 3).
    idx = pd.bdate_range("2022-01-03", periods=401)
    r = pd.Series([0.01, -0.01] * 200)
    prices = pd.Series([100.0] + list(100 * (1 + r).cumprod()), index=idx)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 0}, index=idx)

    m = metrics.compute_metrics(df, None)

    assert m.skew == pytest.approx(0.0, abs=1e-9)
    assert m.excess_kurtosis == pytest.approx(-2.0, abs=0.05)


# --- end to end ----------------------------------------------------------


def test_compute_metrics_on_a_flat_series():
    idx = pd.bdate_range("2022-01-03", periods=300)
    df = pd.DataFrame({"Close": 50.0, "Adj Close": 50.0, "Volume": 10}, index=idx)

    m = metrics.compute_metrics(df, None)

    assert m.annual_return == pytest.approx(0.0)
    assert m.annual_vol == pytest.approx(0.0)
    assert m.max_drawdown == pytest.approx(0.0)
    assert m.calmar is None


def test_compute_metrics_rejects_an_empty_frame():
    df = pd.DataFrame({"Close": [], "Adj Close": [], "Volume": []})
    with pytest.raises(ValueError):
        metrics.compute_metrics(df, None)


def test_metrics_to_dict_round_trips_every_field():
    idx = pd.bdate_range("2022-01-03", periods=300)
    prices = pd.Series(
        100 + np.cumsum(np.random.RandomState(1).normal(0, 1, 300)), index=idx
    )
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 1000}, index=idx)

    d = metrics.metrics_to_dict(metrics.compute_metrics(df, None))

    assert set(d) == set(metrics.Metrics.__dataclass_fields__)
