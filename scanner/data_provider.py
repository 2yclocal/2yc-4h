"""
yfinance data provider — fetches metadata and OHLCV per symbol.
"""

from __future__ import annotations

import time
import logging
import pandas as pd
import yfinance as yf
from dataclasses import dataclass, field
from scanner.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SymbolMeta:
    symbol: str
    exchange: str
    company_name: str = ""
    market_cap: float | None = None
    avg_daily_volume: float | None = None   # dollar volume


def _retry(fn, max_attempts: int = 3, delay: float = 2.0):
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_attempts:
                raise
            logger.warning(f"Attempt {attempt} failed: {exc}. Retrying in {delay}s…")
            time.sleep(delay * attempt)


def get_meta(symbol: str, exchange: str) -> SymbolMeta:
    def _fetch():
        time.sleep(settings.yfinance_rate_limit_delay)
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        market_cap = info.get("marketCap")
        avg_volume = (
            info.get("averageDailyVolume10Day")
            or info.get("averageVolume")
        )
        price = (
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose", 0)
        )
        avg_dollar_vol = (avg_volume * price) if avg_volume and price else None

        # Empty info dict means Yahoo returned nothing — likely rate limited.
        # Raise so _retry picks it up with backoff.
        if market_cap is None and avg_dollar_vol is None:
            raise ValueError(f"Empty info for {symbol} — likely rate limited")

        return SymbolMeta(
            symbol=symbol,
            exchange=exchange,
            company_name=info.get("shortName", info.get("longName", symbol)),
            market_cap=market_cap,
            avg_daily_volume=avg_dollar_vol,
        )

    return _retry(_fetch)


def _drop_incomplete_bar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop the last 4H bar if it is still forming.

    Yahoo 4H bars for US/TSX start at 09:30 and 13:30 exchange time, so a bar
    ends 4 hours after it opens or at the 16:00 close, whichever is first.
    Scans only evaluate completed bars, matching a TradingView bar-close alert.
    """
    last = df.index[-1]
    bar_end = min(last + pd.Timedelta(hours=4), last.normalize() + pd.Timedelta(hours=16))
    if pd.Timestamp.now(tz=last.tz) < bar_end:
        return df.iloc[:-1]
    return df


def _clean(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError(f"No OHLCV data for {symbol}")
    df.columns = [c.lower() for c in df.columns]
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.sort_index(inplace=True)
    df.dropna(subset=["close"], inplace=True)
    if df.empty:
        raise ValueError(f"No OHLCV data for {symbol}")
    return df


def get_ohlcv(symbol: str) -> pd.DataFrame:
    """
    Completed 4H bars — Yahoo's full intraday history (~730 days).
    Used by both the scanner and the backtester.
    Index is exchange-local wall time (tz stripped).
    """
    def _fetch():
        time.sleep(settings.yfinance_rate_limit_delay)
        ticker = yf.Ticker(symbol)

        df = ticker.history(period=settings.intraday_period, interval="4h", auto_adjust=False)
        if df.empty:
            # For listings younger than the period, yfinance moves the start
            # back to the listing date, which Yahoo rejects as older than
            # 730 days (seen on GEV, RDDT, SOLV). An explicit start inside
            # the window works for every symbol.
            start = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=729)).date()
            df = ticker.history(start=str(start), interval="4h", auto_adjust=False)
        df = _drop_incomplete_bar(_clean(df, symbol))
        df.index = pd.to_datetime(df.index).tz_localize(None)
        if df.empty:
            raise ValueError(f"No completed 4H bars for {symbol}")
        return df

    return _retry(_fetch)


def get_ohlcv_daily(symbol: str, years: int = 5) -> pd.DataFrame:
    """
    Daily bars for the D1 SMA200 filter. Includes today's partial bar —
    the filter only ever reads the previous completed day, so it is ignored.
    5 years covers the 200-day warm-up before the start of the 4H history.
    """
    def _fetch():
        time.sleep(settings.yfinance_rate_limit_delay)
        ticker = yf.Ticker(symbol)

        start = (pd.Timestamp.today() - pd.DateOffset(years=years)).date()

        df = _clean(ticker.history(start=str(start), auto_adjust=False), symbol)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df

    return _retry(_fetch)
