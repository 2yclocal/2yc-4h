"""
Telegram notifier — sends a scan summary message via the Telegram Bot API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import requests

_MT = timezone(timedelta(hours=-6), "MDT")   # Alberta: permanent UTC-6, no DST

from scanner.bartime import bar_close_mt
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


def _by_rs(signals: list) -> list:
    """Strongest 3-month RS first; signals without an RS go last, alphabetically."""
    return sorted(signals, key=lambda x: (x.rs_pct is None, -(x.rs_pct or 0), x.symbol))


def _now() -> str:
    return datetime.now(timezone.utc).astimezone(_MT).strftime("%Y-%m-%d %H:%M %Z")


def _bar_close(results: list[ExchangeResult]) -> str | None:
    bars = [r.bar for r in results if r.bar]
    return bar_close_mt(max(set(bars), key=bars.count)) if bars else None


def _error_lines(results: list[ExchangeResult]) -> list[str]:
    """Error breakdown + symbols not checked, per exchange (only when there are errors)."""
    lines = []
    for r in results:
        if not r.errors:
            continue
        breakdown = ", ".join(f"{n} {why}" for why, n in sorted(r.error_reasons.items(), key=lambda x: -x[1]))
        lines.append(f"{r.exchange}: {r.errors} errors" + (f" ({breakdown})" if breakdown else ""))
        if r.failed_symbols:
            shown = r.failed_symbols[:40]
            more = f" +{len(r.failed_symbols) - 40} more" if len(r.failed_symbols) > 40 else ""
            lines.append(f"  Not checked: {', '.join(shown)}{more}")
    return lines


def _short_block(result: ExchangeResult, signals: list) -> str:
    """One line per exchange: ── US ──  AAA (RS 90) · BBB (RS 75)"""
    items = [f"{r.symbol} (RS {r.rs_pct:.0f})" if r.rs_pct is not None else f"{r.symbol} (RS n/a)"
             for r in _by_rs(signals)]
    return f"<b>── {result.exchange} ──</b>  " + (" · ".join(items) if items else "none")


# Rank order — a ticker appears only in the first list it qualifies for
_SCAN_LISTS = [
    ("rising_signals",  "RISING SMA200"),
    ("buy_signals",     f"SMA200 &lt; {settings.rising_sma_min_pct:g}% RISE"),
    ("nogreen_signals", "NO GREEN CANDLE"),
]


def build_scan_messages(results: list[ExchangeResult]) -> list[str]:
    """
    Scan messages in rank order, empty lists skipped. The first message sent
    carries the time / bar close / universe / sort header and any errors.
    If every list is empty, a single NO SIGNALS message is returned instead.
    """
    bar = _bar_close(results)
    context = [f"<b>{_now()}</b>"]
    if bar:
        context.append(f"4H bar close: {bar}")
    context += [
        "Universe: S&amp;P 500 + S&amp;P 400 | TSX Composite",
        "Sorted by RS (3-month return percentile within each exchange, 100 = strongest)",
    ]
    errors = _error_lines(results)

    messages = []
    for attr, title in _SCAN_LISTS:
        total = sum(len(getattr(r, attr)) for r in results)
        if total == 0:
            continue
        first = not messages
        head = [f"<b>{title}</b>"] + (context if first else []) + [f"Total: <b>{total}</b>"]
        body = [_short_block(r, getattr(r, attr)) for r in results]
        if first and errors:
            body += [""] + errors
        message = "\n".join(head) + "\n\n" + "\n".join(body)
        # Telegram max message length is 4096 chars
        if len(message) > 4000:
            message = message[:3990] + "\n…(truncated)"
        messages.append(message)

    if not messages:
        scanned = " · ".join(f"{r.exchange} {r.symbols_checked}" for r in results)
        lines = ["<b>2YC 4H — NO SIGNALS</b>", f"<b>{_now()}</b>"]
        if bar:
            lines.append(f"4H bar close: {bar}")
        lines.append(f"Scanned: {scanned}")
        messages.append("\n".join(lines + errors))
    return messages


def send_scan_results(results: list[ExchangeResult]) -> None:
    """Send the scan lists to Telegram (one message per non-empty list)."""
    for message in build_scan_messages(results):
        _send(message)


def send_watchlist_results(entries: list[UniverseBacktestEntry], total_backtested: int, min_trades: int) -> None:
    """Build and send the ranked watchlist backtest report to Telegram."""
    now = datetime.now(timezone.utc).astimezone(_MT).strftime("%Y-%m-%d %H:%M %Z")

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
