"""
Scan engine — fetches 4H + daily OHLCV and runs the 2YC 4H indicator per symbol.

Universe is pre-screened by index membership (S&P 500/400 for US,
TSX Composite for Canada), so no market-cap API calls are made.
US stocks (NYSE + Nasdaq combined) are labelled as the "US" exchange.
"""

from __future__ import annotations

import time
import logging
import collections
import concurrent.futures
from dataclasses import dataclass, field

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
    error_reasons: dict[str, int] = field(default_factory=dict)
    failed_symbols: list[str] = field(default_factory=list)


# Pauses before each sequential retry round. Round 1 retries every failure;
# later rounds only retry rate-limited symbols (no-data failures are permanent).
RETRY_PAUSES_SECONDS = (30, 60, 120)


def error_reason(exc: Exception) -> str:
    """Bucket an exception into a short label for the Telegram report."""
    msg = str(exc)
    if "Rate" in msg or "Too Many" in msg or "429" in msg:
        return "rate limited"
    if "No OHLCV" in msg or "No completed" in msg:
        return "no data"
    return type(exc).__name__


def scan_exchange(exchange: str, symbols: list[tuple[str, str]]) -> ExchangeResult:
    """
    Runs Stage 2 on every (symbol, name) pair.
    OHLCV fetch + indicator check in each thread.
    """
    logger.info(f"[{exchange}] Starting scan — {len(symbols)} symbols")

    buy_signals: list[BuyResult] = []
    failed: dict[tuple[str, str], str] = {}

    def _scan_symbol(item: tuple[str, str]):
        sym, name = item
        try:
            df = get_ohlcv(sym)
            daily = get_ohlcv_daily(sym)
            result = compute_buy_signal(df, daily, sym, exchange, name)
            if result.buy_signal:
                logger.info(f"[{exchange}] BUY {sym} — {result.explanation}")
                return item, "buy", result
            return item, "ok", None
        except Exception as exc:
            logger.warning(f"[{exchange}] Error {sym}: {exc}")
            return item, "error", error_reason(exc)

    def _collect(item, status, payload):
        if status == "buy":
            buy_signals.append(payload)
        if status == "error":
            failed[item] = payload
        else:
            failed.pop(item, None)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=settings.max_workers_per_exchange
    ) as pool:
        for outcome in pool.map(_scan_symbol, symbols):
            _collect(*outcome)

    # Yahoo throttles cloud IPs under parallel load, so retry failures one at
    # a time after a pause, backing off longer each round.
    for round_no, pause in enumerate(RETRY_PAUSES_SECONDS, start=1):
        retry = [item for item, why in failed.items() if round_no == 1 or why == "rate limited"]
        if not retry:
            break
        logger.info(f"[{exchange}] Retry round {round_no}: {len(retry)} symbols after {pause}s…")
        time.sleep(pause)
        for item in retry:
            _collect(*_scan_symbol(item))

    reasons = dict(collections.Counter(failed.values()))
    logger.info(
        f"[{exchange}] Done — {len(buy_signals)} BUY signals, {len(failed)} errors {reasons or ''}"
    )
    if failed:
        logger.info(f"[{exchange}] Failed symbols: {', '.join(sorted(s for s, _ in failed))}")

    return ExchangeResult(
        exchange=exchange,
        symbols_checked=len(symbols),
        buy_signals=buy_signals,
        errors=len(failed),
        error_reasons=reasons,
        failed_symbols=sorted(sym for sym, _ in failed),
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
