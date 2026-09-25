"""
Scan engine — fetches 4H + daily OHLCV and runs the 2YC 4H indicator per symbol.

Universe is pre-screened by index membership (S&P 500/400 for US,
TSX Composite for Canada), so no market-cap API calls are made.
US stocks (NYSE + Nasdaq combined) are labelled as the "US" exchange.
"""

from __future__ import annotations

import logging
import concurrent.futures
from dataclasses import dataclass

from scanner.config import settings
from scanner.data_provider import get_ohlcv, get_ohlcv_daily
from scanner.indicator import BuyResult, compute_buy_signal

logger = logging.getLogger(__name__)


@dataclass
class ExchangeResult:
    exchange: str
    symbols_checked: int
    buy_signals: list[BuyResult]
    errors: int


def scan_exchange(exchange: str, symbols: list[tuple[str, str]]) -> ExchangeResult:
    """
    Runs Stage 2 on every (symbol, name) pair.
    OHLCV fetch + indicator check in each thread.
    """
    logger.info(f"[{exchange}] Starting scan — {len(symbols)} symbols")

    buy_signals: list[BuyResult] = []
    errors = 0

    def _scan_symbol(item: tuple[str, str]) -> tuple[str, BuyResult | None]:
        sym, name = item
        try:
            df = get_ohlcv(sym)
            daily = get_ohlcv_daily(sym)
            result = compute_buy_signal(df, daily, sym, exchange, name)
            if result.buy_signal:
                logger.info(f"[{exchange}] BUY {sym} — {result.explanation}")
                return "buy", result
            return "ok", None
        except Exception as exc:
            logger.warning(f"[{exchange}] Error {sym}: {exc}")
            return "error", None

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.max_workers_per_exchange
    ) as pool:
        for status, result in pool.map(_scan_symbol, symbols):
            if status == "buy":
                buy_signals.append(result)
            elif status == "error":
                errors += 1

    logger.info(
        f"[{exchange}] Done — {len(buy_signals)} BUY signals, {errors} errors"
    )

    return ExchangeResult(
        exchange=exchange,
        symbols_checked=len(symbols),
        buy_signals=buy_signals,
        errors=errors,
    )


def run_all_exchanges(
    us_symbols: list[tuple[str, str]],
    tsx_symbols: list[tuple[str, str]],
) -> list[ExchangeResult]:
    """Submits US and TSX scans in parallel. Returns [US, TSX]."""
    exchange_map = {
        "US":  us_symbols,
        "TSX": tsx_symbols,
    }

    results: dict[str, ExchangeResult] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(scan_exchange, exch, syms): exch
            for exch, syms in exchange_map.items()
        }
        for future in concurrent.futures.as_completed(futures):
            exch = futures[future]
            try:
                results[exch] = future.result()
            except Exception as exc:
                logger.error(f"Exchange scan failed for {exch}: {exc}")
                results[exch] = ExchangeResult(
                    exchange=exch,
                    symbols_checked=len(exchange_map[exch]),
                    buy_signals=[],
                    errors=len(exchange_map[exch]),
                )

    return [results["US"], results["TSX"]]
