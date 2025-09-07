"""Data fetching helpers using yfinance.

Provides price series (adjusted) and metadata.
"""
from __future__ import annotations

from typing import Dict, Tuple, Optional
import pandas as pd

import yfinance as yf


def fetch_price_and_meta(ticker: str, period: str = "1y", interval: str = "1d") -> Tuple[pd.DataFrame, Dict]:
    """Download adjusted OHLCV for `ticker` and collect lightweight metadata.

    Returns (df, meta). df has DatetimeIndex and columns: Open, High, Low, Close, Adj Close, Volume.
    """
    if not ticker:
        raise ValueError("Ticker must be provided")

    tk = yf.Ticker(ticker)
    df = tk.history(period=period, interval=interval, auto_adjust=False)

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
