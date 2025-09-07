"""riskcli package entry.

Provide module-level CLI entry (`python -m riskcli`).
"""
__all__ = ["cli", "data", "metrics", "report", "utils"]

from . import cli  # expose for -m entry
