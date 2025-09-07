"""Data fetching helpers using yfinance.

Provides price series (adjusted) and metadata.
"""
from __future__ import annotations

from typing import Dict, Tuple, Optional
import time
import random
import pandas as pd

import yfinance as yf

# Simple in-memory cache for interactive sessions: (ticker, period, interval) -> (timestamp, df, meta)
_CACHE: Dict[tuple, tuple] = {}
_CACHE_TTL = 300.0  # seconds


def fetch_price_and_meta(ticker: str, period: str = "1y", interval: str = "1d") -> Tuple[pd.DataFrame, Dict]:
    """Download adjusted OHLCV for `ticker` and collect lightweight metadata.

    Returns (df, meta). df has DatetimeIndex and columns: Open, High, Low, Close, Adj Close, Volume.
    """
    if not ticker:
        raise ValueError("Ticker must be provided")

    key = (ticker.upper(), period, interval)
    now = time.time()
    # return cached if fresh
    cached = _CACHE.get(key)
    if cached is not None:
        ts, cdf, cmeta = cached
        if now - ts < _CACHE_TTL:
            return cdf.copy(), dict(cmeta)

    tk = yf.Ticker(ticker)
    # retry with exponential backoff on transient errors (e.g., rate limits)
    last_err = None
    for attempt in range(1, 4):
        try:
            df = tk.history(period=period, interval=interval, auto_adjust=False)
            last_err = None
            break
        except Exception as e:
            last_err = e
            sleep = (2 ** (attempt - 1)) + random.random() * 0.5
            time.sleep(sleep)
    else:
        # all attempts failed
        raise last_err

    if df is None or df.empty:
        raise ValueError(f"No data for '{ticker}' with period='{period}', interval='{interval}'. Try a longer period or a coarser interval (e.g., 1d).")

    # make tz-naive index for downstream consistency
    if hasattr(df.index, "tz") and df.index.tz is not None:
        try:
            df.index = df.index.tz_convert(None)
        except Exception:
            try:
                df.index = df.index.tz_localize(None)
            except Exception:
                pass

    # Ensure Adj Close present
    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]

    # lightweight meta
    meta: Dict[str, Optional[str]] = {}
    try:
        info = tk.fast_info if hasattr(tk, "fast_info") else {}
        meta["name"] = info.get("longName") or info.get("shortName") or ticker
        meta["currency"] = info.get("currency")
        meta["exchange"] = info.get("exchange")
        meta["market_cap"] = info.get("marketCap")
    except Exception:
        # try get_info fallback
        try:
            info = tk.get_info() or {}
            meta["name"] = info.get("longName") or info.get("shortName") or ticker
            meta["currency"] = info.get("currency")
            meta["exchange"] = info.get("exchange")
            meta["market_cap"] = info.get("marketCap")
        except Exception:
            meta["name"] = ticker

    return df, meta
    
