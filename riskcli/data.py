"""Data fetching helpers using yfinance.

Provides an adjusted OHLCV frame plus lightweight metadata.
"""
from __future__ import annotations

import random
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import yfinance as yf

# Per-process cache, so re-runs inside the interactive menu don't refetch.
# It does not survive between CLI invocations.
_CACHE: Dict[tuple, tuple] = {}
CACHE_TTL = 300.0  # seconds
MAX_ATTEMPTS = 3


def clear_cache() -> None:
    _CACHE.clear()


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
    """Make the index tz-naive and guarantee an Adj Close column."""
    if getattr(df.index, "tz", None) is not None:
        try:
            df.index = df.index.tz_convert(None)
        except (TypeError, AttributeError):
            df.index = df.index.tz_localize(None)

    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    return df


def _download(tk, period: str, interval: str) -> pd.DataFrame:
    """Call yfinance with a bounded exponential backoff on transient errors."""
    last_err: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return tk.history(period=period, interval=interval, auto_adjust=False)
        except Exception as e:
            last_err = e
            time.sleep(2**attempt + random.random() * 0.5)
    raise last_err


def fetch_price_and_meta(
    ticker: str, period: str = "1y", interval: str = "1d"
) -> Tuple[pd.DataFrame, Dict]:
    """Download OHLCV for `ticker` and collect lightweight metadata.

    Returns (df, meta). df has a tz-naive DatetimeIndex and columns
    Open, High, Low, Close, Adj Close, Volume.
    """
    if not ticker:
        raise ValueError("Ticker must be provided")

    key = (ticker.upper(), period, interval)
    cached = _CACHE.get(key)
    if cached is not None and time.time() - cached[0] < CACHE_TTL:
        _, df, meta = cached
        return df.copy(), dict(meta)

    tk = yf.Ticker(ticker)
    df = _download(tk, period, interval)

    if df is None or df.empty:
        raise ValueError(
            f"No data for '{ticker}' with period='{period}', interval='{interval}'. "
            "Try a longer period or a coarser interval (e.g., 1d)."
        )

    df = _prepare(df)
    meta = build_meta(tk, ticker)
    _CACHE[key] = (time.time(), df.copy(), dict(meta))
    return df, meta
