"""
2YC 4H 20/50/200 indicator — runs on 4H bars with a daily trend filter.

Buy conditions on the 4H chart (either triggers a signal):
  1. Current bar opens above SMA200 AND previous bar opened below SMA200 (crossover)
  2. EMA20 crosses above EMA50, both above SMA200

Both conditions require RSI14 < 80 AND the D1 filter: the previous completed
daily candle opened above the daily SMA200. Signals fire on the last
completed 4H bar only — no sell tracking, no alternating state.

Parameters (overridable via .env):
  MA Fast      : EMA 20
  MA Slow      : EMA 50
  MA Direction : SMA 200
  RSI          : period 14, threshold < 80
  D1 filter    : daily SMA 200, previous day's open
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from scanner.config import settings


@dataclass
class BuyResult:
    symbol: str
    exchange: str
    company_name: str
    buy_signal: bool
    cond_open_cross: bool = False
    cond_ma_cross: bool = False
    rsi_ok: bool = False
    daily_ok: bool = False
    explanation: str = ""
    values: dict = field(default_factory=dict)


def _calc_ma(series: pd.Series, period: int, ma_type: str) -> pd.Series:
    if ma_type == "EMA":
        return series.ewm(span=period, adjust=False).mean()
    return series.rolling(period).mean()


def _calc_rsi_wilder(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def _fill_daily_gaps(df: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """
    Yahoo occasionally drops a day from its daily history that its 4H bars
    still have (seen on RY.TO). Rebuild any such day from the 4H bars so the
    daily SMA200 and "yesterday" stay correct. The 09:30 4H open equals the
    daily open, so the rebuilt candle matches what Yahoo would have served.
    """
    agg = df.groupby(df.index.normalize()).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    )
    missing = agg.index.difference(daily.index.normalize())
    if missing.empty:
        return daily
    return pd.concat([daily, agg.loc[missing]]).sort_index()


def daily_filter(df: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """
    D1 filter aligned to the 4H bars in df. For each 4H bar, looks up the
    last daily candle dated strictly before that bar's day (yesterday, never
    today's partial candle) and checks that it opened above the daily SMA200.

    Returns a frame indexed like df with d1_date, d1_open, d1_sma200, d1_ok.
    """
    bar_times = df.index
    daily = _fill_daily_gaps(df, daily)
    sma = _calc_ma(daily["close"], settings.daily_ma_period, settings.daily_ma_type)
    days = daily.index.normalize()
    pos = days.searchsorted(bar_times.normalize(), side="left") - 1
    has_prev = pos >= 0
    pos = np.clip(pos, 0, None)

    d1_open = np.where(has_prev, daily["open"].to_numpy()[pos], np.nan)
    d1_sma = np.where(has_prev, sma.to_numpy()[pos], np.nan)
    d1_ok = d1_open > d1_sma      # NaN (no history / SMA warm-up) compares False
    if not settings.daily_filter_enabled:
        d1_ok = np.ones(len(bar_times), dtype=bool)

    return pd.DataFrame(
        {"d1_date": days[pos].where(has_prev),
         "d1_open": d1_open, "d1_sma200": d1_sma, "d1_ok": d1_ok},
        index=bar_times,
    )


def compute_buy_signal(
    df: pd.DataFrame,
    daily: pd.DataFrame,
    symbol: str,
    exchange: str,
    company_name: str,
) -> BuyResult:
    """
    Apply the 2YC 20/50/200 indicator to a 4H OHLCV DataFrame, gated by the
    D1 filter computed from `daily`.
    Returns a BuyResult — buy_signal=True means conditions are met on the last bar.

    Requires at least 252 4H bars for reliable MA warm-up.
    """
    min_bars = 252
    if len(df) < min_bars:
        return BuyResult(
            symbol=symbol, exchange=exchange, company_name=company_name,
            buy_signal=False,
            explanation=f"Insufficient data: {len(df)} bars (need ≥ {min_bars})",
        )

    df = df.copy()

    # Moving averages
    df["ma_fast"] = _calc_ma(df["close"], settings.ma_fast_period, settings.ma_fast_type)
    df["ma_slow"] = _calc_ma(df["close"], settings.ma_slow_period, settings.ma_slow_type)
    df["ma_dir"]  = _calc_ma(df["close"], settings.ma_direction_period, settings.ma_direction_type)

    # RSI
    df["rsi"] = _calc_rsi_wilder(df["close"], settings.rsi_period)

    # Condition 1: current bar opens above SMA200, previous bar opened below it
    if settings.buy_use_open_cross:
        cond1 = bool(
            (df["open"].iloc[-1] > df["ma_dir"].iloc[-1]) and
            (df["open"].iloc[-2] < df["ma_dir"].iloc[-2])
        )
    else:
        cond1 = False

    # Condition 2: EMA Fast crossed above EMA Slow, both above Direction MA
    if settings.buy_use_ma_cross:
        cond2 = bool(
            _crossover(df["ma_fast"], df["ma_slow"]).iloc[-1] and
            df["ma_fast"].iloc[-1] > df["ma_dir"].iloc[-1] and
            df["ma_slow"].iloc[-1] > df["ma_dir"].iloc[-1]
        )
    else:
        cond2 = False

    # RSI gate
    rsi_ok = bool(df["rsi"].iloc[-1] < settings.rsi_buy_threshold) if settings.rsi_enabled else True

    # D1 gate: yesterday's daily candle opened above the daily SMA200
    d1 = daily_filter(df, daily).iloc[-1]
    daily_ok = bool(d1["d1_ok"])

    buy_signal = (cond1 or cond2) and rsi_ok and daily_ok

    last = df.iloc[-1]
    return BuyResult(
        symbol=symbol,
        exchange=exchange,
        company_name=company_name,
        buy_signal=buy_signal,
        cond_open_cross=cond1,
        cond_ma_cross=cond2,
        rsi_ok=rsi_ok,
        daily_ok=daily_ok,
        explanation=_build_explanation(last, d1, cond1, cond2, buy_signal),
        values={
            "bar":    df.index[-1].strftime("%Y-%m-%d %H:%M"),
            "close":  round(float(last["close"]),   2),
            "ema20":  round(float(last["ma_fast"]), 4),
            "ema50":  round(float(last["ma_slow"]), 4),
            "sma200": round(float(last["ma_dir"]),  4),
            "rsi14":  round(float(last["rsi"]),     2),
            "d1_open":   round(float(d1["d1_open"]),   2),
            "d1_sma200": round(float(d1["d1_sma200"]), 4),
        },
    )


def _build_explanation(row, d1, cond1: bool, cond2: bool, buy_signal: bool) -> str:
    if not buy_signal:
        return "No BUY signal on last bar"
    parts = []
    if cond1:
        parts.append(f"4H open ({row['open']:.2f}) above 4H SMA200 ({row['ma_dir']:.2f})")
    if cond2:
        parts.append(
            f"4H EMA20 ({row['ma_fast']:.2f}) crossed above EMA50 ({row['ma_slow']:.2f}), "
            f"both above 4H SMA200 ({row['ma_dir']:.2f})"
        )
    parts.append(f"RSI14={row['rsi']:.1f} < 80")
    if settings.daily_filter_enabled:
        parts.append(f"D1 open ({d1['d1_open']:.2f}) above D1 SMA200 ({d1['d1_sma200']:.2f})")
    return " | ".join(parts)
