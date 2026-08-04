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

    def fake_fetch(ticker, period="1y", interval="1d", with_meta=True):
        meta = {"name": f"{ticker} Inc", "currency": "USD"} if with_meta else {}
        return df.copy(), meta

    monkeypatch.setattr(data, "fetch_price_and_meta", fake_fetch)
    return df


@pytest.fixture
def offline_varied(monkeypatch):
    """Distinct series per ticker, so correlations are not degenerate."""
    import numpy as np

    idx = pd.bdate_range("2022-01-03", periods=301)
    rng = np.random.RandomState(4)

    def fake_fetch(ticker, period="1y", interval="1d", with_meta=True):
        rets = rng.normal(0.0004, 0.011, 300)
        prices = pd.Series([100.0] + list(100 * (1 + pd.Series(rets)).cumprod()), index=idx)
        df = pd.DataFrame(
            {"Close": prices, "Adj Close": prices, "Volume": 1_000}, index=idx
        )
        meta = {"name": f"{ticker} Inc", "currency": "USD"} if with_meta else {}
        return df, meta

    monkeypatch.setattr(data, "fetch_price_and_meta", fake_fetch)


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


# --- portfolio mode ------------------------------------------------------


def test_one_ticker_still_gives_the_single_name_report(offline, capsys):
    assert cli.main(["AAPL"]) == 0
    out = capsys.readouterr().out
    assert "Risk Grade" in out
    assert "Diversification" not in out


def test_several_bare_tickers_give_an_equal_weighted_portfolio(offline_varied, capsys):
    assert cli.main(["AAPL", "MSFT", "TLT"]) == 0
    out = capsys.readouterr().out
    assert "Diversification" in out
    assert "33.3" in out  # equal weights


def test_explicit_weights_are_reported_back(offline_varied, capsys):
    assert cli.main(["AAPL=0.5", "MSFT=0.5"]) == 0
    assert "50.0" in capsys.readouterr().out


def test_a_bad_weight_is_a_clean_error_not_a_traceback(offline_varied, capsys):
    assert cli.main(["AAPL=oops", "MSFT=1"]) == 2
    assert "weight" in capsys.readouterr().out.lower()


def test_mixing_weighted_and_bare_tickers_is_a_clean_error(offline_varied, capsys):
    assert cli.main(["AAPL=0.5", "MSFT"]) == 2
    assert capsys.readouterr().out.strip() != ""


def test_portfolio_json_export_includes_positions(offline_varied, tmp_path):
    out = tmp_path / "p.json"
    assert cli.main(["AAPL=0.6", "MSFT=0.4", "--export", str(out)]) == 0

    payload = json.loads(out.read_text())
    assert payload["diversification_ratio"] > 0
    assert {p["ticker"] for p in payload["positions"]} == {"AAPL", "MSFT"}
    assert payload["positions"][0]["risk_share"] > 0


def test_portfolio_csv_export_is_a_position_table(offline_varied, tmp_path):
    out = tmp_path / "p.csv"
    assert cli.main(["AAPL=0.6", "MSFT=0.4", "--export", str(out)]) == 0

    lines = out.read_text().splitlines()
    assert lines[0].startswith("ticker,weight")
    assert len(lines) == 4  # header + 2 positions + portfolio total
    assert lines[-1].startswith("PORTFOLIO")


def test_a_rate_limit_is_reported_once_not_per_holding(monkeypatch, capsys):
    def limited(*a, **k):
        raise data.RateLimited("Yahoo is rate limiting this client. Wait a few minutes.")

    monkeypatch.setattr(data, "fetch_price_and_meta", limited)
    assert cli.main(["AAPL", "MSFT", "TLT"]) == 2

    out = capsys.readouterr().out
    assert out.lower().count("rate limiting") == 1
    assert "could not fetch" not in out.lower()


def test_a_rate_limit_on_a_single_name_is_stated_plainly(monkeypatch, capsys):
    def limited(*a, **k):
        raise data.RateLimited("Yahoo is rate limiting this client. Wait a few minutes.")

    monkeypatch.setattr(data, "fetch_price_and_meta", limited)
    assert cli.main(["AAPL"]) == 2
    assert "wait a few minutes" in capsys.readouterr().out.lower()


def test_a_portfolio_run_makes_one_request_per_symbol(monkeypatch, capsys):
    """Metadata is two extra requests per ticker and the portfolio view does
    not use it. Against a rate-limited source that waste is the whole problem.
    """
    calls = []
    idx = pd.bdate_range(end="2026-08-04", periods=300)

    class CountingTicker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def fast_info(self):
            calls.append(f"meta:{self.symbol}")
            return {}

        def get_info(self):
            calls.append(f"meta:{self.symbol}")
            return {}

        def history(self, period=None, interval=None, auto_adjust=None):
            calls.append(f"history:{self.symbol}")
            px = pd.Series(range(100, 400), index=idx, dtype=float)
            return pd.DataFrame({"Close": px, "Volume": 1_000}, index=idx)

    monkeypatch.setattr(data.yf, "Ticker", CountingTicker)
    data.clear_cache()

    assert cli.main(["AAPL", "MSFT", "TLT"]) == 0

    assert calls == [
        "history:AAPL",
        "history:MSFT",
        "history:TLT",
        "history:^GSPC",
    ]


def test_portfolio_mode_ignores_compare(offline_varied, capsys):
    # --compare is a single-name feature; it should not crash a portfolio run
    assert cli.main(["AAPL", "MSFT", "--compare"]) == 0
