"""Data-layer tests. No network: yfinance is stubbed."""
import pandas as pd
import pytest

from riskcli import data


class FakeTicker:
    """Stands in for yfinance.Ticker."""

    def __init__(self, frame=None, fast=None, info=None, fail_times=0, error=None):
        self._frame = frame
        self._fast = fast if fast is not None else {}
        self._info = info if info is not None else {}
        self._fail_times = fail_times
        self._error = error or RuntimeError("connection reset")
        self.calls = 0
        self.meta_calls = 0

    @property
    def fast_info(self):
        self.meta_calls += 1
        return self._fast

    def get_info(self):
        self.meta_calls += 1
        return self._info

    def history(self, period=None, interval=None, auto_adjust=None):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        return self._frame.copy()


def _frame(tz=None, with_adj=True):
    idx = pd.date_range("2022-01-03", periods=5, freq="D", tz=tz)
    cols = {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 10.0, "Volume": 100}
    if with_adj:
        cols["Adj Close"] = 9.5
    return pd.DataFrame(cols, index=idx)


@pytest.fixture(autouse=True)
def clear_cache():
    data.clear_cache()
    yield
    data.clear_cache()


# --- metadata ------------------------------------------------------------


def test_name_comes_from_info_because_fast_info_has_no_long_name():
    tk = FakeTicker(
        fast={"currency": "USD", "exchange": "NMS", "marketCap": 3_000_000_000},
        info={"longName": "Apple Inc."},
    )
    meta = data.build_meta(tk, "AAPL")
    assert meta["name"] == "Apple Inc."
    assert meta["currency"] == "USD"
    assert meta["exchange"] == "NMS"
    assert meta["market_cap"] == 3_000_000_000


def test_name_falls_back_to_short_name():
    tk = FakeTicker(info={"shortName": "Apple"})
    assert data.build_meta(tk, "AAPL")["name"] == "Apple"


def test_name_falls_back_to_the_ticker_when_lookups_fail():
    class Broken(FakeTicker):
        @property
        def fast_info(self):
            raise RuntimeError("rate limited")

        def get_info(self):
            raise RuntimeError("rate limited")

    meta = data.build_meta(Broken(), "ZZZZ")
    assert meta["name"] == "ZZZZ"
    assert meta["currency"] is None


# --- frame preparation ---------------------------------------------------


def test_timezone_aware_index_is_made_naive(monkeypatch):
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(_frame(tz="America/New_York")))
    df, _ = data.fetch_price_and_meta("AAPL")
    assert df.index.tz is None


def test_adj_close_is_synthesized_when_missing(monkeypatch):
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(_frame(with_adj=False)))
    df, _ = data.fetch_price_and_meta("AAPL")
    assert (df["Adj Close"] == df["Close"]).all()


def test_empty_response_raises_a_useful_error(monkeypatch):
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(pd.DataFrame()))
    with pytest.raises(ValueError, match="No data for 'ZZZZ'"):
        data.fetch_price_and_meta("ZZZZ")


def test_blank_ticker_raises():
    with pytest.raises(ValueError):
        data.fetch_price_and_meta("")


# --- retry and cache -----------------------------------------------------


def test_transient_failures_are_retried(monkeypatch):
    tk = FakeTicker(_frame(), fail_times=2)
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)
    monkeypatch.setattr(data.time, "sleep", lambda s: None)

    df, _ = data.fetch_price_and_meta("AAPL")
    assert tk.calls == 3
    assert not df.empty


def test_giving_up_reraises_the_last_error(monkeypatch):
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(_frame(), fail_times=99))
    monkeypatch.setattr(data.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="connection reset"):
        data.fetch_price_and_meta("AAPL")


def test_second_fetch_within_ttl_does_not_hit_the_network(monkeypatch):
    tk = FakeTicker(_frame())
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    data.fetch_price_and_meta("AAPL", period="1y")
    data.fetch_price_and_meta("aapl", period="1y")  # same key, different case
    assert tk.calls == 1


def test_a_different_period_is_a_different_cache_key(monkeypatch):
    tk = FakeTicker(_frame())
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    data.fetch_price_and_meta("AAPL", period="1y")
    data.fetch_price_and_meta("AAPL", period="3y")
    assert tk.calls == 2


# --- rate limiting -------------------------------------------------------


def test_a_rate_limit_is_not_retried(monkeypatch):
    """Retrying a 429 within seconds cannot succeed and deepens the throttle."""
    tk = FakeTicker(_frame(), fail_times=99, error=RuntimeError("Too Many Requests. Rate limited."))
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)
    monkeypatch.setattr(data.time, "sleep", lambda s: pytest.fail("slept on a rate limit"))

    with pytest.raises(data.RateLimited):
        data.fetch_price_and_meta("AAPL")

    assert tk.calls == 1


def test_the_rate_limit_error_says_what_to_do(monkeypatch):
    tk = FakeTicker(_frame(), fail_times=99, error=RuntimeError("Too Many Requests"))
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    with pytest.raises(data.RateLimited, match="(?i)wait a few minutes"):
        data.fetch_price_and_meta("AAPL")


def test_yfinance_rate_limit_exception_is_recognised(monkeypatch):
    from yfinance.exceptions import YFRateLimitError

    tk = FakeTicker(_frame(), fail_times=99, error=YFRateLimitError())
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)
    monkeypatch.setattr(data.time, "sleep", lambda s: pytest.fail("slept on a rate limit"))

    with pytest.raises(data.RateLimited):
        data.fetch_price_and_meta("AAPL")
    assert tk.calls == 1


def test_backoff_does_not_sleep_after_the_final_attempt(monkeypatch):
    sleeps = []
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(_frame(), fail_times=99))
    monkeypatch.setattr(data.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError):
        data.fetch_price_and_meta("AAPL")

    # Sleeping after the last failure buys nothing but delay before the error.
    assert len(sleeps) == data.MAX_ATTEMPTS - 1


# --- optional metadata ---------------------------------------------------


def test_metadata_lookups_are_skipped_when_not_wanted(monkeypatch):
    """The name/currency lookup is two extra requests; skip it when unused."""
    tk = FakeTicker(_frame(), info={"longName": "Apple Inc."})
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    df, meta = data.fetch_price_and_meta("AAPL", with_meta=False)

    assert not df.empty
    assert tk.meta_calls == 0
    assert meta == {}


def test_metadata_is_fetched_by_default(monkeypatch):
    tk = FakeTicker(_frame(), info={"longName": "Apple Inc."})
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    _, meta = data.fetch_price_and_meta("AAPL")

    assert meta["name"] == "Apple Inc."
    assert tk.meta_calls > 0


def test_a_metaless_cache_hit_still_provides_metadata_when_asked(monkeypatch):
    tk = FakeTicker(_frame(), info={"longName": "Apple Inc."})
    monkeypatch.setattr(data.yf, "Ticker", lambda t: tk)

    data.fetch_price_and_meta("AAPL", with_meta=False)
    _, meta = data.fetch_price_and_meta("AAPL", with_meta=True)

    assert meta["name"] == "Apple Inc."
    assert tk.calls == 1  # the price history was not refetched


def test_cached_frame_is_a_copy(monkeypatch):
    monkeypatch.setattr(data.yf, "Ticker", lambda t: FakeTicker(_frame()))

    first, _ = data.fetch_price_and_meta("AAPL")
    first.loc[first.index[0], "Close"] = -1.0
    second, _ = data.fetch_price_and_meta("AAPL")

    assert second["Close"].iloc[0] == 10.0
