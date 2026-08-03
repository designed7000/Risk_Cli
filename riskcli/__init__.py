"""riskcli — a compact terminal risk report for a single ticker.

Run as `riskcli AAPL` after install, or `python -m riskcli AAPL` from a checkout.
Submodules are imported lazily so `riskcli.metrics` does not pull in yfinance.
"""
__all__ = ["cli", "data", "metrics", "report", "utils"]
__version__ = "0.1.0"
