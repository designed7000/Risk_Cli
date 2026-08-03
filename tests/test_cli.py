"""CLI tests. No network: the fetch layer is stubbed."""
import json

import pandas as pd
import pytest

from riskcli import cli, data


@pytest.fixture
def offline(monkeypatch):
    idx = pd.bdate_range("2022-01-03", periods=300)
    prices = pd.Series(range(100, 400), index=idx, dtype=float)
    df = pd.DataFrame({"Close": prices, "Adj Close": prices, "Volume": 1_000}, index=idx)

    def fake_fetch(ticker, period="1y", interval="1d"):
        return df.copy(), {"name": f"{ticker} Inc", "currency": "USD"}

    monkeypatch.setattr(data, "fetch_price_and_meta", fake_fetch)
    return df


@pytest.mark.parametrize(
    "given,expected",
    [("0.03", 0.03), ("3%", 0.03), ("3", 0.03), ("0", 0.0), ("", 0.0), ("abc", 0.0)],
)
def test_risk_free_rate_parsing(given, expected):
    assert cli.parse_rf(given) == pytest.approx(expected)


def test_single_report_never_stretches_past_the_spark_cap():
    side_by_side, spark = cli.layout(term_cols=300, compare=False)
    assert side_by_side is False
    assert spark == 60


def test_narrow_terminal_still_gets_a_usable_spark():
    _, spark = cli.layout(term_cols=40, compare=False)
    assert spark == 20


def test_compare_goes_side_by_side_only_when_wide():
    assert cli.layout(term_cols=200, compare=True)[0] is True
    assert cli.layout(term_cols=100, compare=True)[0] is False


def test_side_by_side_sizes_the_spark_to_half_the_terminal():
    # 150 cols -> each panel gets ~75, so the spark must be well under the
    # 60 it would get if it were sized off the full width.
    _, spark = cli.layout(term_cols=150, compare=True)
    assert spark < 60
    assert spark == cli.layout(term_cols=75, compare=False)[1]


def test_report_run_exits_zero(offline, capsys):
    assert cli.main(["AAPL", "--period", "1y"]) == 0
    assert "AAPL" in capsys.readouterr().out


def test_export_json_writes_every_metric(offline, tmp_path):
    out = tmp_path / "m.json"
    assert cli.main(["AAPL", "--export", str(out)]) == 0

    payload = json.loads(out.read_text())
    assert payload["ticker"] == "AAPL"
    assert payload["period"] == "1y"
    assert "sharpe" in payload and "max_drawdown" in payload


def test_export_csv_writes_a_header_row(offline, tmp_path):
    out = tmp_path / "m.csv"
    assert cli.main(["AAPL", "--export", str(out)]) == 0
    assert out.read_text().splitlines()[0] == "metric,value"


def test_unknown_export_suffix_is_an_error(offline, tmp_path):
    assert cli.main(["AAPL", "--export", str(tmp_path / "m.txt")]) == 2


def test_fetch_failure_exits_nonzero(monkeypatch, capsys):
    def boom(*a, **k):
        raise ValueError("no data for 'ZZZZ'")

    monkeypatch.setattr(data, "fetch_price_and_meta", boom)
    assert cli.main(["ZZZZ"]) == 2
    assert "ZZZZ" in capsys.readouterr().out


def test_compare_mode_renders_both_periods(offline, capsys):
    assert cli.main(["AAPL", "--compare", "--compare-period", "3y"]) == 0
    assert "AAPL" in capsys.readouterr().out
