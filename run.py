#!/usr/bin/env python3
"""
Manual one-shot scan runner.

Usage:
    python run.py
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
from scanner.engine import run_all_exchanges
from scanner.notifier import send_scan_results


def main():
    print("=" * 60)
    print("  2YC 4H 20/50/200 SCAN — manual run")
    print("=" * 60)

    print("\nLoading universes…")
    us  = load_us_symbols()
    tsx = load_tsx_symbols()

    print(f"  US:  {len(us)} symbols (S&P 500 + S&P 400)")
    print(f"  TSX: {len(tsx)} symbols (S&P/TSX 60)")
    print(f"\nRunning US + TSX in parallel…\n")

    results = run_all_exchanges(us, tsx)

    print("\n" + "=" * 60)
    for r in results:
        print(f"  {r.exchange}: {len(r.buy_signals)} BUY signals  ({r.errors} errors)")
        for sig in r.buy_signals:
            print(f"    → {sig.symbol} | ${sig.values.get('close')} | {sig.explanation}")
    print("=" * 60)

    print("\nSending Telegram notification…")
    send_scan_results(results)
    print("Done.")


if __name__ == "__main__":
    main()
