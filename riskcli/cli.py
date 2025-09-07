"""CLI entrypoint for riskcli.

Handles argument parsing, calling data fetch, metrics, report and export.
"""
from __future__ import annotations

import argparse
import sys
import json
import csv
from pathlib import Path
from typing import Optional
import shutil
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()

from . import data, metrics, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="riskcli", description="Print a compact risk report for a ticker")
    p.add_argument("ticker", nargs="?", help="Ticker symbol to analyze (if omitted you'll be prompted)")
    p.add_argument("--period", default="1y", help="Period to download (e.g. 1mo,3mo,6mo,1y,2y,5y,10y,ytd,max)")
    p.add_argument("--interval", default="1d", help="Data interval (e.g. 1d)")
    p.add_argument("--benchmark", default="^GSPC", help="Benchmark ticker for beta (default: ^GSPC)")
    p.add_argument("--rf", type=str, default="0.0", help="Annual risk-free rate (decimal). Examples: 0.03 or 3% or 3 -> 0.03")
    p.add_argument("--export", help="Optional path to export metrics as .json or .csv")
    p.add_argument("--compare", action="store_true", help="Show a comparison report for --period and --compare-period (side-by-side when wide)")
    p.add_argument("--compare-period", default="3y", help="Second period used for comparison when --compare is set (default: 3y)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # normalize rf argument (accept '3%', '3' meaning 3%) -> decimal like 0.03
    def _parse_rf(val: str) -> float:
        if val is None:
            return 0.0
        s = str(val).strip()
        try:
            if s.endswith("%"):
                return float(s.rstrip("%")) / 100.0
            v = float(s)
            # If the user passed a value > 1, assume percent (3 -> 0.03)
            if v > 1.0:
                return v / 100.0
            return v
        except Exception:
            return 0.0

    args.rf = _parse_rf(args.rf)

    # If ticker wasn't provided, enter an interactive menu
    if not args.ticker:
        args = interactive_menu(args)

    try:
        asset_df, asset_meta = data.fetch_price_and_meta(args.ticker, period=args.period, interval=args.interval)
    except ValueError as e:
        console.print(f"[error] {e}")
        return 2
    except Exception as e:
        console.print(f"[error] Unexpected fetch error: {e}")
        return 2

    try:
        bench_df, bench_meta = data.fetch_price_and_meta(args.benchmark, period=args.period, interval=args.interval)
    except Exception:
        # benchmark optional — continue with empty
        bench_df = None
        bench_meta = {}

    try:
        m = metrics.compute_metrics(asset_df, bench_df, rf=args.rf)
    except Exception as e:
        console.print(f"[error] Error computing metrics: {e}")
        return 3

    # prepare spark data and meta
    term_cols = shutil.get_terminal_size((100, 20)).columns
    spark_width = max(20, min(60, term_cols - 60))
    try:
        prices = asset_df["Adj Close"].dropna().tolist()
    except Exception:
        prices = asset_df["Close"].dropna().tolist()
    spark_values = prices[-120:]
    asset_meta = dict(asset_meta)
    asset_meta["_spark_values"] = spark_values
    asset_meta["_spark_width"] = spark_width

    # if comparison requested, fetch and compute second period and render side-by-side when possible
    if args.compare:
        try:
            asset_df2, asset_meta2 = data.fetch_price_and_meta(args.ticker, period=args.compare_period, interval=args.interval)
        except Exception as e:
            console.print(f"[error] Could not fetch compare period '{args.compare_period}': {e}")
            return 2
        bench_df2 = None
        try:
            bench_df2, _ = data.fetch_price_and_meta(args.benchmark, period=args.compare_period, interval=args.interval)
        except Exception:
            bench_df2 = None
        try:
            m2 = metrics.compute_metrics(asset_df2, bench_df2, rf=args.rf)
        except Exception as e:
            console.print(f"[error] Error computing metrics for compare period: {e}")
            return 3

        # attach spark values for second
        try:
            prices2 = asset_df2["Adj Close"].dropna().tolist()
        except Exception:
            prices2 = asset_df2["Close"].dropna().tolist()
        asset_meta2 = dict(asset_meta2)
        asset_meta2["_spark_values"] = prices2[-120:]
        asset_meta2["_spark_width"] = spark_width

        # build panels
        panel1 = report.build_report_panel(args.ticker, asset_meta, asset_df, args.period, args.benchmark, m)
        panel2 = report.build_report_panel(args.ticker, asset_meta2, asset_df2, args.compare_period, args.benchmark, m2)

        from rich.columns import Columns

        if term_cols >= 140:
            console.print(Columns([panel1, panel2], equal=True, expand=True))
        else:
            console.print(panel1)
            console.print(panel2)

    else:
        panel = report.build_report_panel(args.ticker, asset_meta, asset_df, args.period, args.benchmark, m)
        console.print(panel)

    # export
    if args.export:
        p = Path(args.export)
        out = metrics.metrics_to_dict(m)
        out.update({"ticker": args.ticker, "period": args.period, "benchmark": args.benchmark})
        if p.suffix.lower() == ".json":
            p.write_text(json.dumps(out, default=str, indent=2))
        elif p.suffix.lower() == ".csv":
            with p.open("w", newline="") as fh:
                writer = csv.writer(fh)
                for k, v in out.items():
                    writer.writerow([k, v])
        else:
            console.print("Unknown export format: use .json or .csv")

    return 0

def interactive_menu(parsed_args: argparse.Namespace) -> argparse.Namespace:
    """Interactive menu for users who run without a ticker.

    Allows setting ticker, period, interval, benchmark, rf, and export before running.
    """
    # current state
    # always start with an empty ticker in interactive menu so the user must choose it
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

    def render_state() -> None:
        console.clear()
        t = Table(show_header=False, pad_edge=False)
        t.add_column("key", style="bold")
        t.add_column("value")
        for k, v in state.items():
            t.add_row(k, str(v))
        # show the state table inside the panel so inputs are visually inside the box
        panel = Panel(t, title="Risk Evaluation", subtitle="Interactive Menu", expand=False)
        console.print(panel)

    while True:
        render_state()
        console.print("\nMenu shortcuts:")
        shortcuts = [
            ("1", "ticker"),
            ("2", "period"),
            ("3", "interval"),
            ("4", "benchmark"),
            ("5", "rf"),
            ("6", "export"),
            ("7", "compare (toggle)"),
            ("8", "compare_period (set)"),
            ("9", "run"),
            ("0", "quit"),
        ]
        for k, desc in shortcuts:
            console.print(f"  [{k}] {desc}")

        choice = Prompt.ask(
            "Choose action (number, short key, or name)",
            choices=[
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "0",
                "compare",
                "compare_period",
                "ticker",
                "period",
                "interval",
                "benchmark",
                "rf",
                "export",
                "run",
                "quit",
                "help",
            ],
            default="ticker",
        )

        # map numeric shortcuts to action keywords
        mapping = {
            "1": "ticker",
            "2": "period",
            "3": "interval",
            "4": "benchmark",
            "5": "rf",
            "6": "export",
            "7": "compare",
            "8": "compare_period",
            "9": "run",
            "0": "quit",
            "h": "help",
        }
        action = mapping.get(choice, choice)

        if action == "ticker":
            val = Prompt.ask("Ticker (e.g. AAPL)", default=state["ticker"]) or state["ticker"]
            state["ticker"] = val.strip()
        elif action == "period":
            val = Prompt.ask("Period (1mo,3mo,6mo,1y,2y,5y,10y,ytd,max)", default=state["period"]) or state["period"]
            state["period"] = val.strip()
        elif action == "interval":
            val = Prompt.ask("Interval (e.g. 1d)", default=state["interval"]) or state["interval"]
            state["interval"] = val.strip()
        elif action == "benchmark":
            val = Prompt.ask("Benchmark ticker", default=state["benchmark"]) or state["benchmark"]
            state["benchmark"] = val.strip()
        elif action == "rf":
            val = Prompt.ask("Annual risk-free rate (decimal or percent, e.g. 0.03 or 3% or 3)", default=str(state["rf"]))
            s = str(val).strip()
            try:
                if s.endswith("%"):
                    state["rf"] = float(s.rstrip("%")) / 100.0
                else:
                    v = float(s)
                    state["rf"] = (v / 100.0) if v > 1.0 else v
            except Exception:
                console.print("Invalid number, keeping previous value.")
        elif action == "export":
            val = Prompt.ask("Export path (leave blank for none)", default=state["export"]) or ""
            state["export"] = val.strip()
        elif action == "compare":
            # toggle compare mode
            state["compare"] = not bool(state.get("compare"))
            console.print(f"Compare mode set to: {state['compare']}")
            Prompt.ask("Press Enter to continue", default="")
        elif action == "compare_period":
            val = Prompt.ask("Compare period (e.g. 3y, 5y)", default=state.get("compare_period", "3y")) or state.get("compare_period", "3y")
            state["compare_period"] = val.strip()
        elif action == "help":
            console.print("\nEnter each field to change it. When ready choose 'run' to fetch data and show the report. 'quit' exits.")
            Prompt.ask("Press Enter to continue", default="")
        elif action == "run":
            if not state["ticker"]:
                console.print("Please enter a ticker before running.")
                Prompt.ask("Press Enter to continue", default="")
                continue
            # build namespace to return
            ns = argparse.Namespace(
                ticker=state["ticker"],
                period=state["period"],
                interval=state["interval"],
                benchmark=state["benchmark"],
                rf=state["rf"],
                export=state["export"] or None,
                compare=bool(state.get("compare", False)),
                compare_period=state.get("compare_period", "3y"),
            )
            return ns
        elif action == "quit":
            if Confirm.ask("Quit without running?", default=False):
                console.print("Exiting.")
                raise SystemExit(0)
            continue

    try:
        asset_df, asset_meta = data.fetch_price_and_meta(args.ticker, period=args.period, interval=args.interval)
    except Exception as e:
        print(f"Error fetching {args.ticker}: {e}")
        return 2

    try:
        bench_df, bench_meta = data.fetch_price_and_meta(args.benchmark, period=args.period, interval=args.interval)
    except Exception:
        # benchmark optional — continue with empty
        bench_df = None
        bench_meta = {}

    try:
        m = metrics.compute_metrics(asset_df, bench_df, rf=args.rf)
    except Exception as e:
        print(f"Error computing metrics: {e}")
        return 3

    # render
    report.render_report(args.ticker, asset_meta, asset_df, args.period, args.benchmark, m)

    # export
    if args.export:
        p = Path(args.export)
        out = metrics.metrics_to_dict(m)
        out.update({"ticker": args.ticker, "period": args.period, "benchmark": args.benchmark})
        if p.suffix.lower() == ".json":
            p.write_text(json.dumps(out, default=str, indent=2))
        elif p.suffix.lower() == ".csv":
            with p.open("w", newline="") as fh:
                writer = csv.writer(fh)
                for k, v in out.items():
                    writer.writerow([k, v])
        else:
            print("Unknown export format: use .json or .csv")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
