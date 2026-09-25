#!/usr/bin/env python3
"""
Watchlist backtest runner — backtests the full universe and ranks by win rate.

Usage:
    python run_watchlist_backtest.py
"""

import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from scanner.universe import load_us_symbols, load_tsx_symbols
from scanner.backtest import backtest_universe
from scanner.notifier import send_watchlist_results

MIN_TRADES = 20
TOP_N = 50


def main():
    print("=" * 60)
    print("  2YC 4H WATCHLIST BACKTEST — manual run")
    print("=" * 60)

    print("\nLoading universes…")
    us = load_us_symbols()
    tsx = load_tsx_symbols()
    print(f"  US:  {len(us)} symbols (S&P 500 + S&P 400)")
    print(f"  TSX: {len(tsx)} symbols (S&P/TSX 60)")

    print("\nBacktesting US universe…")
    us_entries = backtest_universe(us, "US", min_trades=MIN_TRADES)
    print(f"  {len(us_entries)} US symbols qualified (>= {MIN_TRADES} closed trades)")

    print("\nBacktesting TSX universe…")
    tsx_entries = backtest_universe(tsx, "TSX", min_trades=MIN_TRADES)
    print(f"  {len(tsx_entries)} TSX symbols qualified (>= {MIN_TRADES} closed trades)")

    entries = us_entries + tsx_entries
    entries.sort(key=lambda e: (e.result.win_rate_pct, e.result.total_pnl_pct), reverse=True)
    top = entries[:TOP_N]

    print("\n" + "=" * 60)
    for i, e in enumerate(top, start=1):
        print(f"  {i:>2}. {e.symbol:<8} {e.result.win_rate_pct:>5.1f}%  ({e.result.wins}/{e.result.trades_used})")
    print("=" * 60)

    print("\nSending Telegram notification…")
    send_watchlist_results(top, total_backtested=len(entries), min_trades=MIN_TRADES)
    print("Done.")


if __name__ == "__main__":
    main()
