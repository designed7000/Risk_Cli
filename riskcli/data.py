"""Data fetching helpers using yfinance.

Provides an adjusted OHLCV frame plus lightweight metadata.
"""
from __future__ import annotations

import random
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

try:  # present from yfinance 0.2.5x; fall back to message matching without it
    from yfinance.exceptions import YFRateLimitError
except ImportError:  # pragma: no cover - depends on the installed version
    YFRateLimitError = ()

# Per-process cache, so re-runs inside the interactive menu don't refetch.
# It does not survive between CLI invocations.
_CACHE: Dict[tuple, tuple] = {}
CACHE_TTL = 300.0  # seconds
MAX_ATTEMPTS = 3


class RateLimited(RuntimeError):
    """Yahoo is throttling this client. Retrying now only deepens it."""


def clear_cache() -> None:
    _CACHE.clear()


def _is_rate_limit(err: Exception) -> bool:
    if YFRateLimitError and isinstance(err, YFRateLimitError):
        return True
    text = str(err).lower()
    return "too many requests" in text or "rate limit" in text


def build_meta(tk, ticker: str) -> Dict[str, Optional[object]]:
    """Collect display metadata for `ticker`, degrading to just the symbol.

    `fast_info` is cheap but carries no company name, so the name comes from
    the fuller info payload; either lookup may fail on a rate limit.
    """
    meta: Dict[str, Optional[object]] = {
        "name": ticker,
        "currency": None,
        "exchange": None,
        "market_cap": None,
    }

    try:
        fast = tk.fast_info or {}
        meta["currency"] = fast.get("currency")
        meta["exchange"] = fast.get("exchange")
        meta["market_cap"] = fast.get("marketCap")
    except Exception:
        pass

    try:
        info = tk.get_info() or {}
        meta["name"] = info.get("longName") or info.get("shortName") or ticker
        meta["currency"] = meta["currency"] or info.get("currency")
        meta["exchange"] = meta["exchange"] or info.get("exchange")
        meta["market_cap"] = meta["market_cap"] or info.get("marketCap")
    except Exception:
        pass

    return meta


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a tz-naive index and a guaranteed Adj Close.

    The tz is *dropped*, not converted. yfinance stamps a daily bar at
    midnight in the exchange's own timezone, so converting to UTC first would
    push Frankfurt onto the previous calendar day and Tokyo onto a different
    hour than New York. The benchmark regression aligns asset and benchmark
    on the index, and under UTC a non-US ticker overlaps a US benchmark on
    exactly zero bars — beta, alpha and R² silently come back empty.
    """
    df = df.copy()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)

    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    return df


def _download(tk, period: str, interval: str) -> pd.DataFrame:
    """Call yfinance with a bounded exponential backoff on transient errors.

    A rate limit is not transient on this timescale: retrying it seconds later
    cannot succeed and spends more of the budget that is already exhausted, so
    it fails immediately instead.
    """
    last_err: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return tk.history(period=period, interval=interval, auto_adjust=False)
        except Exception as e:
            if _is_rate_limit(e):
                raise RateLimited(
                    "Yahoo is rate limiting this client. Wait a few minutes before "
                    "retrying — repeated attempts extend the cooldown. yfinance "
                    "scrapes a public endpoint, so the limit is not published."
                ) from e
            last_err = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2**attempt + random.random() * 0.5)
    raise last_err


def fetch_price_and_meta(
    ticker: str, period: str = "1y", interval: str = "1d", with_meta: bool = True
) -> Tuple[pd.DataFrame, Dict]:
    """Download OHLCV for `ticker`, optionally with display metadata.

    Returns (df, meta). df has a tz-naive DatetimeIndex and columns
    Open, High, Low, Close, Adj Close, Volume.

    `with_meta=False` skips the name/currency lookup, which costs two further
    requests per ticker. Callers that only need prices — the benchmark, and
    every holding in a portfolio — should skip it: against a rate-limited
    source, requests you throw away are the expensive kind.
    """
    if not ticker:
        raise ValueError("Ticker must be provided")

    key = (ticker.upper(), period, interval)
    tk: Optional[object] = None

    cached = _CACHE.get(key)
    if cached is not None and time.time() - cached[0] < CACHE_TTL:
        ts, df, meta = cached
        if not with_meta:
            return df.copy(), {}
        if meta is not None:
            return df.copy(), dict(meta)
        # Prices were cached without metadata; fill it in without refetching.
        tk = yf.Ticker(ticker)
        meta = build_meta(tk, ticker)
        _CACHE[key] = (ts, df, dict(meta))
        return df.copy(), meta

    tk = yf.Ticker(ticker)
    df = _download(tk, period, interval)

    if df is None or df.empty:
        raise ValueError(
            f"No data for '{ticker}' with period='{period}', interval='{interval}'. "
            "Try a longer period or a coarser interval (e.g., 1d)."
        )

    df = _prepare(df)
    meta = build_meta(tk, ticker) if with_meta else None
    _CACHE[key] = (time.time(), df.copy(), None if meta is None else dict(meta))
    return df, dict(meta) if meta is not None else {}
