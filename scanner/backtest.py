"""
2YC 4H 20/50/200 backtester — replays the full buy/sell state machine
(the alternating long/flat logic from the Pine strategy) bar by bar
over historical 4H data and reports P&L over the last N closed trades.

Buys also require the D1 filter (previous day's daily open above the daily
SMA200). Sells are unchanged and use 4H bars only.

History is limited to what Yahoo serves for 4H bars (~730 days).

Mirrors the Pine script's signal logic exactly:
  - process_orders_on_close: entries/exits fill at the signal bar's close
  - alternation: no buy,buy or sell,sell — must flip state each time

Position sizing here deliberately diverges from the Pine strategy's
percent_of_equity=100: each trade is sized at a fixed $100K notional
(TRADE_SIZE) rather than compounding, so P&L is a plain sum across trades.
"""

from __future__ import annotations

import time
import logging
import concurrent.futures
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

from scanner.config import settings
from scanner.data_provider import get_ohlcv, get_ohlcv_daily
from scanner.engine import RETRY_PAUSES_SECONDS
from scanner.indicator import _calc_ma, _calc_rsi_wilder, daily_filter

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    pnl_pct: float


TRADE_SIZE = 100_000  # fixed notional per trade — not compounded


@dataclass
class BacktestResult:
    symbol: str
    total_trades_all: int
    trades_used: int
    wins: int
    win_rate_pct: float
    total_pnl_pct: float
    total_pnl_dollars: float
    avg_pnl_pct: float
    best_pct: float
    worst_pct: float
    first_trade_date: str | None
    last_trade_date: str | None
    open_trade: dict | None = field(default=None)


def run_backtest(symbol: str, exchange: str = "US", max_trades: int = 100) -> BacktestResult:
    df = get_ohlcv(symbol)
    daily = get_ohlcv_daily(symbol)

    min_bars = settings.ma_direction_period + 2
    if len(df) < min_bars:
        raise ValueError(f"Insufficient data: {len(df)} bars (need >= {min_bars})")

    df = df.copy()
    df["ma_fast"] = _calc_ma(df["close"], settings.ma_fast_period, settings.ma_fast_type)
    df["ma_slow"] = _calc_ma(df["close"], settings.ma_slow_period, settings.ma_slow_type)
    df["ma_dir"] = _calc_ma(df["close"], settings.ma_direction_period, settings.ma_direction_type)
    df["rsi"] = _calc_rsi_wilder(df["close"], settings.rsi_period)

    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    close = df["close"].to_numpy()
    ma_fast = df["ma_fast"].to_numpy()
    ma_slow = df["ma_slow"].to_numpy()
    ma_dir = df["ma_dir"].to_numpy()
    rsi = df["rsi"].to_numpy()
    d1_ok = daily_filter(df, daily)["d1_ok"].to_numpy()
    dates = df.index

    trades: list[Trade] = []
    last_was_buy = False
    in_long = False
    entry_price = 0.0
    entry_date = None

    for i in range(1, len(df)):
        if np.isnan(ma_dir[i]) or np.isnan(ma_dir[i - 1]) or np.isnan(ma_fast[i - 1]) or np.isnan(rsi[i]):
            continue

        cond1 = settings.buy_use_open_cross and (open_[i] > ma_dir[i] and open_[i - 1] < ma_dir[i - 1])
        cond2 = settings.buy_use_ma_cross and (
            ma_fast[i] > ma_slow[i] and ma_fast[i - 1] <= ma_slow[i - 1] and
            ma_fast[i] > ma_dir[i] and ma_slow[i] > ma_dir[i]
        )
        rsi_ok = (rsi[i] < settings.rsi_buy_threshold) if settings.rsi_enabled else True
        buy_condition = (cond1 or cond2) and rsi_ok and d1_ok[i]

        sell_ma_cross = ma_fast[i] < ma_slow[i] and ma_fast[i - 1] >= ma_slow[i - 1]
        sell_high_dir = high[i] < ma_dir[i] and high[i - 1] >= ma_dir[i - 1]
        sell_high_fast = high[i] < ma_fast[i] and high[i - 1] >= ma_fast[i - 1]
        sell_condition = sell_ma_cross or sell_high_dir or sell_high_fast

        buy_signal = buy_condition and not last_was_buy
        sell_signal = sell_condition and last_was_buy

        if buy_signal:
            last_was_buy = True
        if sell_signal:
            last_was_buy = False

        if buy_signal and not in_long:
            in_long = True
            entry_price = close[i]
            entry_date = dates[i]

        if sell_signal and in_long:
            exit_price = close[i]
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            trades.append(Trade(entry_date, entry_price, dates[i], exit_price, pnl_pct))
            in_long = False

    open_trade = None
    if in_long:
        unrealized = (close[-1] - entry_price) / entry_price * 100
        open_trade = {
            "entry_date": entry_date.strftime("%Y-%m-%d %H:%M"),
            "entry_price": round(float(entry_price), 2),
            "unrealized_pct": round(float(unrealized), 2),
        }

    if not trades:
        return BacktestResult(
            symbol=symbol,
            total_trades_all=0,
            trades_used=0,
            wins=0,
            win_rate_pct=0.0,
            total_pnl_pct=0.0,
            total_pnl_dollars=0.0,
            avg_pnl_pct=0.0,
            best_pct=0.0,
            worst_pct=0.0,
            first_trade_date=None,
            last_trade_date=None,
            open_trade=open_trade,
        )

    used = trades[-max_trades:]
    pnl_pcts = [t.pnl_pct for t in used]

    # Each trade is sized at a fixed $100K notional (not compounded) —
    # dollar P&L per trade is independent, so totals are a plain sum.
    dollar_pnls = [p / 100 * TRADE_SIZE for p in pnl_pcts]
    total_pnl_dollars = sum(dollar_pnls)
    total_pnl_pct = total_pnl_dollars / TRADE_SIZE * 100

    wins = sum(1 for p in pnl_pcts if p > 0)

    return BacktestResult(
        symbol=symbol,
        total_trades_all=len(trades),
        trades_used=len(used),
        wins=wins,
        win_rate_pct=round(100 * wins / len(used), 1),
        total_pnl_pct=round(total_pnl_pct, 2),
        total_pnl_dollars=round(total_pnl_dollars, 2),
        avg_pnl_pct=round(sum(pnl_pcts) / len(used), 2),
        best_pct=round(max(pnl_pcts), 2),
        worst_pct=round(min(pnl_pcts), 2),
        first_trade_date=used[0].entry_date.strftime("%Y-%m-%d"),
        last_trade_date=used[-1].exit_date.strftime("%Y-%m-%d"),
        open_trade=open_trade,
    )


@dataclass
class UniverseBacktestEntry:
    symbol: str
    company_name: str
    exchange: str
    result: BacktestResult


def backtest_universe(
    symbols: list[tuple[str, str]],
    exchange: str,
    min_trades: int = 10,
    max_workers: int | None = None,
) -> list[UniverseBacktestEntry]:
    """
    Backtests every (symbol, name) pair in a universe.
    Symbols with fewer than `min_trades` closed trades are dropped —
    a win rate from 2-3 trades is not a meaningful ranking signal.
    """
    workers = max_workers or settings.max_workers_per_exchange
    logger.info(f"[{exchange}] Starting universe backtest — {len(symbols)} symbols")

    entries: list[UniverseBacktestEntry] = []
    too_few = 0
    failed: list[tuple[str, str]] = []

    def _backtest_symbol(item: tuple[str, str]):
        sym, name = item
        try:
            result = run_backtest(sym, exchange)
            if result.trades_used < min_trades:
                return item, "too_few", None
            return item, "ok", UniverseBacktestEntry(symbol=sym, company_name=name, exchange=exchange, result=result)
        except Exception as exc:
            logger.warning(f"[{exchange}] Backtest error {sym}: {exc}")
            return item, "error", None

    def _collect(item, status, entry):
        nonlocal too_few
        if status == "ok":
            entries.append(entry)
        elif status == "too_few":
            too_few += 1
        else:
            failed.append(item)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(_backtest_symbol, symbols):
            _collect(*outcome)

    # Second chance for failures, one at a time — see engine.scan_exchange
    if failed:
        retry, failed[:] = list(failed), []
        logger.info(f"[{exchange}] Retrying {len(retry)} failed symbols sequentially…")
        time.sleep(RETRY_PAUSES_SECONDS[0])
        for item in retry:
            _collect(*_backtest_symbol(item))

    logger.info(
        f"[{exchange}] Done — {len(entries)} ranked, {too_few} below {min_trades} trades, "
        f"{len(failed)} errors"
    )
    return entries
