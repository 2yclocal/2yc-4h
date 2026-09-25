"""
Telegram notifier — sends a scan summary message via the Telegram Bot API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests

_MT = ZoneInfo("America/Denver")
_NY = ZoneInfo("America/New_York")   # US + TSX 4H bars are stamped in Eastern time

from scanner.config import settings
from scanner.engine import ExchangeResult
from scanner.backtest import UniverseBacktestEntry

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text: str) -> bool:
    """Send a message to the configured Telegram chat. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram credentials not configured — skipping notification")
        return False

    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram notification sent")
        return True
    except Exception as exc:
        logger.error(f"Telegram send failed: {exc}")
        return False


def _format_exchange_block(result: ExchangeResult) -> str:
    lines = [f"<b>── {result.exchange} ──</b>"]
    if result.buy_signals:
        for r in sorted(result.buy_signals, key=lambda x: x.symbol):
            lines.append(f"  {r.symbol} — {r.company_name}" if r.company_name else f"  {r.symbol}")
    else:
        lines.append("  No BUY signals")
    error_note = ""
    if result.errors:
        breakdown = ", ".join(f"{n} {why}" for why, n in sorted(result.error_reasons.items(), key=lambda x: -x[1]))
        error_note = f" · {result.errors} errors: {breakdown}" if breakdown else f" · {result.errors} errors"
    lines.append(
        f"  (Scanned {result.symbols_checked} → {len(result.buy_signals)} BUY{error_note})"
    )
    if result.failed_symbols:
        shown = result.failed_symbols[:40]
        more = f" +{len(result.failed_symbols) - 40} more" if len(result.failed_symbols) > 40 else ""
        lines.append(f"  Not checked: {', '.join(shown)}{more}")
    return "\n".join(lines)


def _format_bar_mt(bar: str) -> str:
    """'2026-09-24 13:30' (Eastern bar open) → '2026-09-24 11:30 AM MDT'."""
    start = datetime.strptime(bar, "%Y-%m-%d %H:%M").replace(tzinfo=_NY).astimezone(_MT)
    return start.strftime("%Y-%m-%d %-I:%M %p %Z")


def send_scan_results(results: list[ExchangeResult]) -> None:
    """Build and send the full scan report to Telegram."""
    now = datetime.now(timezone.utc).astimezone(_MT).strftime("%Y-%m-%d %-I:%M %p %Z")
    total_buy = sum(len(r.buy_signals) for r in results)

    header = (
        f"<b>2YC 4H 20/50/200 SCAN</b>\n"
        f"<b>{now}</b>\n"
        f"Universe: S&amp;P 500 + S&amp;P 400 | TSX Composite\n"
        f"Signal: 4H EMA20/EMA50/SMA200 + RSI &lt; 80 + D1 open &gt; D1 SMA200\n"
        f"Total BUY signals: <b>{total_buy}</b>\n"
    )
    bars = [r.bar for r in results if r.bar]
    if bars:
        header += f"4H bar: {_format_bar_mt(max(set(bars), key=bars.count))}\n"

    blocks = [_format_exchange_block(r) for r in results]
    body = "\n\n".join(blocks)

    # Telegram max message length is 4096 chars
    message = header + "\n" + body
    if len(message) > 4000:
        message = message[:3990] + "\n…(truncated)"

    _send(message)


def send_watchlist_results(entries: list[UniverseBacktestEntry], total_backtested: int, min_trades: int) -> None:
    """Build and send the ranked watchlist backtest report to Telegram."""
    now = datetime.now(timezone.utc).astimezone(_MT).strftime("%Y-%m-%d %-I:%M %p %Z")

    header = (
        f"<b>2YC 4H WATCHLIST BACKTEST</b>\n"
        f"<b>{now}</b>\n"
        f"Universe: S&amp;P 500 + S&amp;P 400 | TSX Composite\n"
        f"Ranked by win rate · min {min_trades} closed trades required\n"
        f"Top {len(entries)} of {total_backtested} qualifying symbols\n"
    )

    lines = []
    for i, e in enumerate(entries, start=1):
        r = e.result
        name_part = f" — {e.company_name}" if e.company_name else ""
        lines.append(f"  {i}. {e.symbol} — {r.win_rate_pct:.1f}% ({r.wins}/{r.trades_used}){name_part}")

    message = header + "\n" + "\n".join(lines)
    if len(message) > 4000:
        message = message[:3990] + "\n…(truncated)"

    _send(message)
