from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Schedule
    scan_hour: int = 14
    scan_minute: int = 0
    scan_timezone: str = "America/Denver"  # MST/MDT

    # /check command filter (not used in full scan — index membership is the size gate)
    min_market_cap: float = 5_000_000_000     # $5B
    min_avg_daily_volume: float = 1_000_000   # $1M ADV

    # 2YC 20/50/200 indicator parameters (applied to 4H bars)
    ma_fast_period: int = 20
    ma_fast_type: Literal["EMA", "SMA"] = "EMA"
    ma_slow_period: int = 50
    ma_slow_type: Literal["EMA", "SMA"] = "EMA"
    ma_direction_period: int = 200
    ma_direction_type: Literal["EMA", "SMA"] = "SMA"
    rsi_period: int = 14
    rsi_buy_threshold: float = 80.0
    rsi_enabled: bool = True
    buy_use_open_cross: bool = True
    buy_use_ma_cross: bool = True

    # D1 trend filter — the previous completed daily candle must have
    # opened above its daily SMA200 for a 4H buy to count
    daily_filter_enabled: bool = True
    daily_ma_period: int = 200
    daily_ma_type: Literal["EMA", "SMA"] = "SMA"

    # Yahoo caps 4H history at ~730 days
    intraday_period: str = "730d"

    # Data fetching
    yfinance_rate_limit_delay: float = 0.1
    max_workers_per_exchange: int = 15


settings = Settings()
