"""CLI entrypoint for riskcli.

Handles argument parsing, then delegates to data fetch, metrics and report.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import data, metrics, portfolio, report

console = Console()

SPARK_POINTS = 120
SIDE_BY_SIDE_COLUMNS = 120
MIN_SPARK, MAX_SPARK = 20, 60
SPARK_MARGIN = 40  # room for the label column and panel borders


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="riskcli",
        description="Print a compact risk report for a ticker or a portfolio",
        epilog=(
            "examples:\n"
            "  riskcli AAPL                        single-name report\n"
            "  riskcli AAPL MSFT TLT               portfolio, equal weights\n"
            "  riskcli AAPL=0.4 MSFT=0.4 TLT=0.2   portfolio, explicit weights\n"
            "  riskcli SPY=1.3 TLT=-0.3            shorts are allowed"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "tickers",
        nargs="*",
        metavar="TICKER[=WEIGHT]",
        help="One ticker for a single-name report, or several for a portfolio "
        "(weights optional; equal if omitted). Omit entirely to be prompted.",
    )
    p.add_argument("--period", default="1y", help="Period to download (e.g. 1mo,3mo,6mo,1y,2y,5y,10y,ytd,max)")
    p.add_argument("--interval", default="1d", help="Data interval (e.g. 1d, 1wk, 1h). Annualization follows the interval.")
    p.add_argument("--benchmark", default="^GSPC", help="Benchmark ticker for beta (default: ^GSPC)")
    p.add_argument("--rf", default="0.0", help="Annual risk-free rate. Accepts 0.03, 3%% or 3 -> 0.03")
    p.add_argument("--export", help="Optional path to export metrics as .json or .csv")
    p.add_argument("--compare", action="store_true", help="Also report --compare-period (side-by-side when wide)")
    p.add_argument("--compare-period", default="3y", help="Second period used with --compare (default: 3y)")
    return p


def parse_rf(val: Optional[str]) -> float:
    """Normalize a risk-free input to an annual decimal. '3', '3%' -> 0.03."""
    if val is None:
        return 0.0
    s = str(val).strip()
    try:
        if s.endswith("%"):
            return float(s.rstrip("%")) / 100.0
        v = float(s)
    except ValueError:
        return 0.0
    return v / 100.0 if v > 1.0 else v


def layout(term_cols: int, compare: bool) -> tuple[bool, int]:
    """Decide the compare layout and the sparkline width that fits it.

    Side by side, each panel gets about half the terminal, so the sparkline
    has to be sized against one panel's share rather than the full width.
    """
    side_by_side = compare and term_cols >= SIDE_BY_SIDE_COLUMNS
    panel_cols = term_cols // 2 if side_by_side else term_cols
    return side_by_side, max(MIN_SPARK, min(MAX_SPARK, panel_cols - SPARK_MARGIN))


def _error(msg: str) -> None:
    console.print(f"error: {msg}", style="red", markup=False)


def _with_spark(meta: dict, df, width: int) -> dict:
    col = "Adj Close" if "Adj Close" in df.columns else "Close"
    meta = dict(meta)
    meta["_spark_values"] = df[col].dropna().tolist()[-SPARK_POINTS:]
    meta["_spark_width"] = width
    return meta


def _load(ticker: str, period: str, interval: str, benchmark: str, rf: float):
    """Fetch a ticker and its benchmark, returning (df, meta, metrics)."""
    df, meta = data.fetch_price_and_meta(ticker, period=period, interval=interval)
    try:
        bench_df, _ = data.fetch_price_and_meta(
            benchmark, period=period, interval=interval, with_meta=False
        )
    except Exception:
        bench_df = None  # benchmark is optional; beta/alpha come back blank
    return df, meta, metrics.compute_metrics(df, bench_df, rf=rf)


def _export(path: Path, m: metrics.Metrics, ticker: str, period: str, benchmark: str) -> int:
    out = metrics.metrics_to_dict(m)
    out.update({"ticker": ticker, "period": period, "benchmark": benchmark})

    suffix = path.suffix.lower()
    if suffix == ".json":
        path.write_text(json.dumps(out, default=str, indent=2))
    elif suffix == ".csv":
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["metric", "value"])
            writer.writerows(out.items())
    else:
        _error(f"unknown export format '{path.suffix}': use .json or .csv")
        return 2
    return 0


def _portfolio_export(path: Path, p, period: str, benchmark: str) -> int:
    """JSON carries the whole structure; CSV is the position table."""
    suffix = path.suffix.lower()
    positions = [asdict(pos) for pos in p.positions]

    if suffix == ".json":
        out = {
            "period": period,
            "benchmark": benchmark,
            "positions": positions,
            "correlation": p.correlation.to_dict(),
            "weighted_avg_vol": p.weighted_avg_vol,
            "diversification_ratio": p.diversification_ratio,
            "parametric_var_95": p.parametric_var_95,
            "observations": p.observations,
            "portfolio": metrics.metrics_to_dict(p.metrics),
        }
        path.write_text(json.dumps(out, default=str, indent=2))
    elif suffix == ".csv":
        fields = ["ticker", "weight", "annual_vol", "corr_to_portfolio",
                  "component_var", "risk_share"]
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(positions)
            writer.writerow({
                "ticker": "PORTFOLIO",
                "weight": 1.0,
                "annual_vol": p.metrics.annual_vol,
                "corr_to_portfolio": 1.0,
                "component_var": p.parametric_var_95,
                "risk_share": 1.0,
            })
    else:
        _error(f"unknown export format '{path.suffix}': use .json or .csv")
        return 2
    return 0


def run_portfolio(args) -> int:
    """Fetch every holding, aggregate, and render the portfolio report."""
    try:
        weights = portfolio.parse_positions(args.tickers)
    except ValueError as e:
        _error(str(e))
        return 2

    frames = {}
    for ticker in weights:
        try:
            frames[ticker], _ = data.fetch_price_and_meta(
                ticker, period=args.period, interval=args.interval, with_meta=False
            )
        except data.RateLimited as e:
            # Naming a ticker here is noise: the limit is on the client, not
            # on the symbol, and the remaining holdings would all fail too.
            _error(str(e))
            return 2
        except Exception as e:
            _error(f"could not fetch {ticker}: {e}")
            return 2

    try:
        bench_df, _ = data.fetch_price_and_meta(
            args.benchmark, period=args.period, interval=args.interval, with_meta=False
        )
    except Exception:
        bench_df = None

    try:
        p = portfolio.compute_portfolio(frames, weights, bench_df=bench_df, rf=args.rf)
    except ValueError as e:
        _error(str(e))
        return 3

    console.print(report.build_portfolio_panel(p, args.period, args.benchmark))

    if args.export:
        return _portfolio_export(Path(args.export), p, args.period, args.benchmark)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.rf = parse_rf(args.rf)

    if not args.tickers:
        args = interactive_menu(args)

    # One instrument is the single-name report; more than one is a portfolio.
    if len(args.tickers) > 1 or any("=" in t for t in args.tickers):
        return run_portfolio(args)

    args.ticker = args.tickers[0]
    term_cols = shutil.get_terminal_size((100, 20)).columns
    side_by_side, spark_width = layout(term_cols, args.compare)

    try:
        asset_df, asset_meta, m = _load(args.ticker, args.period, args.interval, args.benchmark, args.rf)
    except (ValueError, data.RateLimited) as e:
        _error(str(e))
        return 2
    except Exception as e:
        _error(f"could not fetch {args.ticker}: {e}")
        return 2

    panels = [
        report.build_report_panel(
            args.ticker, _with_spark(asset_meta, asset_df, spark_width),
            asset_df, args.period, args.benchmark, m,
        )
    ]

    if args.compare:
        try:
            df2, meta2, m2 = _load(args.ticker, args.compare_period, args.interval, args.benchmark, args.rf)
        except Exception as e:
            _error(f"could not fetch compare period '{args.compare_period}': {e}")
            return 2
        panels.append(
            report.build_report_panel(
                args.ticker, _with_spark(meta2, df2, spark_width),
                df2, args.compare_period, args.benchmark, m2,
            )
        )

    if side_by_side and len(panels) > 1:
        console.print(Columns(panels, equal=True, padding=(0, 1)))
    else:
        for panel in panels:
            console.print(panel)

    if args.export:
        return _export(Path(args.export), m, args.ticker, args.period, args.benchmark)
    return 0


MENU = [
    ("1", "ticker", "Ticker, or several for a portfolio (AAPL MSFT=0.4)"),
    ("2", "period", "Period (1mo,3mo,6mo,1y,2y,5y,10y,ytd,max)"),
    ("3", "interval", "Interval (e.g. 1d, 1wk)"),
    ("4", "benchmark", "Benchmark ticker"),
    ("5", "rf", "Annual risk-free rate (0.03, 3% or 3)"),
    ("6", "export", "Export path (blank for none)"),
    ("7", "compare", "Compare mode (toggle)"),
    ("8", "compare_period", "Compare period (e.g. 3y)"),
    ("9", "run", "Fetch data and show the report"),
    ("0", "quit", "Exit"),
]


def interactive_menu(parsed_args: argparse.Namespace) -> argparse.Namespace:
    """Prompt for settings when the CLI is run without a ticker."""
    state = {
        "ticker": "",
        "period": parsed_args.period,
        "interval": parsed_args.interval,
        "benchmark": parsed_args.benchmark,
        "rf": parsed_args.rf,
        "export": parsed_args.export or "",
        "compare": getattr(parsed_args, "compare", False),
        "compare_period": getattr(parsed_args, "compare_period", "3y"),
    }
    actions = {key: name for key, name, _ in MENU}
    prompts = {name: label for _, name, label in MENU}

    def render() -> None:
        console.clear()
        t = Table(show_header=False, pad_edge=False)
        t.add_column("key", style="bold")
        t.add_column("value")
        for k, v in state.items():
            t.add_row(k, str(v))
        console.print(Panel(t, title="Risk Evaluation", subtitle="Interactive Menu", expand=False))

    def pause() -> None:
        Prompt.ask("Press Enter to continue", default="", show_default=False)

    while True:
        render()
        console.print("\nMenu shortcuts:")
        for key, name, label in MENU:
            console.print(f"  [{key}] {name} — {label}")

        raw = Prompt.ask(
            "Choose an action, or just type tickers",
            default="ticker",
            show_default=False,
        ).strip()

        # Anything that isn't a menu action is taken as the holdings. Typing
        # "NVDA" at this prompt is what people actually try first, and being
        # told to "select one of the available options" teaches them nothing.
        key = raw.lower()
        action = actions.get(key) or (key if key in prompts else None)
        if action is None:
            state["ticker"] = raw
            continue

        if action == "quit":
            if Confirm.ask("Quit without running?", default=False):
                raise SystemExit(0)
        elif action == "compare":
            state["compare"] = not state["compare"]
        elif action == "rf":
            state["rf"] = parse_rf(Prompt.ask(prompts["rf"], default=str(state["rf"])))
        elif action == "run":
            if not state["ticker"]:
                console.print("Please enter a ticker before running.")
                pause()
                continue
            # The ticker field doubles as a portfolio spec: "AAPL MSFT TLT".
            settings = {k: v for k, v in state.items() if k != "ticker"}
            return argparse.Namespace(
                tickers=state["ticker"].split(),
                **{**settings, "export": state["export"] or None},
            )
        else:
            current = str(state[action])
            state[action] = (Prompt.ask(prompts[action], default=current) or current).strip()


if __name__ == "__main__":
    raise SystemExit(main())
