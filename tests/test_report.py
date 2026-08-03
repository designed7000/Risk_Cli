"""Report tests: the risk grade and the number formatting."""
import math

import pandas as pd
import pytest

from riskcli import report
from riskcli.metrics import Metrics


def _metrics(**overrides) -> Metrics:
    base = dict(
        annual_return=0.05,
        annual_vol=0.15,
        sharpe=0.5,
        sortino=0.7,
        max_drawdown=0.10,
        calmar=0.5,
        var_95=-0.015,
        cvar_95=-0.02,
        tail_ratio=1.0,
        skew=0.0,
        excess_kurtosis=0.0,
        beta=1.0,
        alpha=0.0,
        r2=0.5,
        avg_daily_dollar_vol=1e6,
        periods_per_year=252.0,
    )
    base.update(overrides)
    return Metrics(**base)


def test_quiet_name_grades_low():
    assert report.risk_grade(_metrics())[0] == "Low"


def test_severe_drawdown_alone_is_not_low_risk():
    # 72% peak-to-trough. max_drawdown is a positive magnitude.
    graded = report.risk_grade(_metrics(annual_vol=0.22, max_drawdown=0.72, var_95=-0.019))
    assert graded[0] == "High"


def test_moderate_drawdown_lifts_the_grade():
    assert report.risk_grade(_metrics(max_drawdown=0.30))[0] == "Medium"


def test_high_vol_and_fat_tail_grades_high():
    graded = report.risk_grade(_metrics(annual_vol=0.60, max_drawdown=0.85, var_95=-0.06))
    assert graded == ("High", "red")


def test_drawdown_renders_as_a_negative_percentage():
    assert report.fmt_percent(-0.3512) == "-35.12%"


def test_missing_values_render_as_a_dash():
    assert report.fmt_percent(None) == "—"
    assert report.fmt_percent(float("nan")) == "—"
    assert report.fmt_unit(float("inf")) == "—"


def test_panel_renders_without_a_benchmark():
    idx = pd.bdate_range("2022-01-03", periods=30)
    df = pd.DataFrame({"Close": 10.0, "Adj Close": 10.0, "Volume": 5}, index=idx)
    panel = report.build_report_panel("TEST", {"name": "Test Co"}, df, "1y", "^GSPC", _metrics(beta=None, alpha=None, r2=None))
    assert panel is not None
