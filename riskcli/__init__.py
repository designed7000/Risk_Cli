"""riskcli — a compact terminal risk report for a single ticker.

Run as `riskcli AAPL` after install, or `python -m riskcli AAPL` from a checkout.
No submodule is imported at package import time, so `from riskcli import
metrics` does not pull in yfinance.
"""
__all__ = ["cli", "data", "metrics", "report", "utils"]
__version__ = "0.1.0"
