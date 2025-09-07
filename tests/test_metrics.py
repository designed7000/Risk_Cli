import math
import pandas as pd
import numpy as np

from riskcli import metrics


def test_drawdown_and_var():
    # simple price series with a drawdown
    prices = pd.Series([100, 110, 90, 95, 120])
    returns = prices.pct_change().dropna()
    mdd = metrics.max_drawdown(returns)
    # now max_drawdown returns positive magnitude
    assert mdd > 0
    # short sample -> VaR/CVaR should be NaN per new policy
    v, c = metrics.historical_var_cvar(returns, 0.95)
    import numpy as _np
    assert _np.isnan(v) and _np.isnan(c)


def test_beta_alpha():
    # asset returns = 2 * bench + noise
    rng = np.random.RandomState(0)
    bench = pd.Series(rng.normal(0, 0.01, size=252))
    asset = 2 * bench + rng.normal(0, 0.001, size=252)
    b, a, r2 = metrics.beta_alpha_r2(asset, bench)
    assert pytest_approx(b, 2, tol=0.1)
    assert a is not None


def pytest_approx(x, target, tol=1e-6):
    return abs(x - target) <= tol


def test_end_to_end_compute():
    # synthetic dataframe
    dates = pd.date_range("2020-01-01", periods=260, freq="B")
    prices = pd.Series(100 + np.cumsum(np.random.RandomState(1).normal(0, 1, size=len(dates))), index=dates)
    df = pd.DataFrame({"Adj Close": prices, "Close": prices, "Volume": 1000}, index=dates)
    m = metrics.compute_metrics(df, None, rf=0.0)
    assert isinstance(m.annual_return, float)
    assert isinstance(m.annual_vol, float)


def test_mdd_is_positive_magnitude():
    import pandas as pd
    from riskcli.metrics import max_drawdown

    s = pd.Series([100, 90, 80, 120, 110], dtype=float)
    assert max_drawdown(s) > 0


def test_var_nan_on_short_sample():
    import numpy as np
    import pandas as pd
    from riskcli.metrics import historical_var_cvar

    v, c = historical_var_cvar(pd.Series([0.01, -0.02, 0.005]), alpha=0.95)
    assert np.isnan(v) and np.isnan(c)
