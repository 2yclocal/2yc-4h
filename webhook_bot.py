#!/usr/bin/env python3
"""
Telegram webhook bot — runs on Render free tier.

Telegram pushes each message instantly to the /webhook/<token> endpoint.
Commands are processed in a background thread so Telegram gets a 200 immediately.
The /fullscan command triggers the GitHub Actions full-scan.yml workflow via API.

Environment variables (set in Render dashboard):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
    GITHUB_TOKEN          — Personal Access Token with Actions: Read/Write scope
    GITHUB_REPOSITORY     — e.g. 2yclocal/2yc-4h
    MIN_MARKET_CAP        — 5000000000
    MIN_AVG_DAILY_VOLUME  — 1000000
"""

import sys
import os
import logging
import threading
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify, abort

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webhook_bot")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set")
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
_MT = ZoneInfo("America/Denver")

app = Flask(__name__)


# ── Telegram helpers ───────────────────────────────────────────────────────────

def _api(method: str, **kwargs) -> dict:
    resp = requests.post(f"{BASE}/{method}", json=kwargs, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _send(chat_id, text: str):
    _api("sendMessage", chat_id=chat_id, text=text,
         parse_mode="HTML", disable_web_page_preview=True)


# ── Command parsing ────────────────────────────────────────────────────────────

def _base_command(text: str) -> str:
    word = text.split()[0] if text.split() else text
    return word.split("@")[0].lower()


def _parse_symbols(text: str) -> list[str]:
    parts = text.split()
    if parts and parts[0].startswith("/"):
        parts = parts[1:]
    return [s.upper() for s in " ".join(parts).replace(",", " ").split() if s]


def _exchange_for(symbol: str) -> str:
    return "TSX" if symbol.endswith(".TO") else "US"


# ── Scanner ────────────────────────────────────────────────────────────────────

def _run_scan(symbols: list[str]) -> str:
    from scanner.data_provider import get_meta, get_ohlcv, get_ohlcv_daily
    from scanner.filters import passes_base_filter
    from scanner.indicator import compute_buy_signal

    start = datetime.now(timezone.utc)
    passed_s1, failed_s1, errors = [], [], []

    for sym in symbols:
        try:
            meta = get_meta(sym, _exchange_for(sym))
            ok, reason = passes_base_filter(meta)
            if ok:
                passed_s1.append(meta)
            else:
                failed_s1.append((sym, reason))
        except Exception as exc:
            errors.append((sym, str(exc)[:80]))

    buy_signals, no_signal = [], []

    for meta in passed_s1:
        try:
            df = get_ohlcv(meta.symbol)
            daily = get_ohlcv_daily(meta.symbol)
            result = compute_buy_signal(df, daily, meta.symbol, meta.exchange, meta.company_name)
            if result.buy_signal:
                buy_signals.append(result)
            else:
                no_signal.append(meta.symbol)
        except Exception as exc:
            errors.append((meta.symbol, str(exc)[:80]))

    lines = [f"<b>2YC 4H Scan — {len(symbols)} symbol(s)</b>"]

    if buy_signals:
        lines.append(f"\n<b>✅ BUY signals ({len(buy_signals)})</b>")
        for r in sorted(buy_signals, key=lambda x: x.symbol):
            lines.append(f"  {r.symbol} — {r.company_name}" if r.company_name else f"  {r.symbol}")
    else:
        lines.append("\n❌ No BUY signals")

    if no_signal:
        lines.append(f"\n<b>Passed filter — no signal:</b> {', '.join(sorted(no_signal))}")
    if failed_s1:
        lines.append("\n<b>Failed Stage 1:</b>")
        for sym, reason in failed_s1:
            lines.append(f"  {sym} — {reason}")
    if errors:
        lines.append("\n<b>Errors:</b>")
        for sym, err in errors:
            lines.append(f"  {sym} — {err}")

    elapsed = (datetime.now(timezone.utc) - start).seconds
    lines.append(f"\n<i>Completed in {elapsed}s</i>")
    return "\n".join(lines)


# ── Backtest ───────────────────────────────────────────────────────────────────

def _run_backtest(symbol: str) -> str:
    from scanner.backtest import run_backtest

    try:
        result = run_backtest(symbol, _exchange_for(symbol))
    except Exception as exc:
        return f"⚠️ Backtest failed for {symbol}: {str(exc)[:150]}"

    if result.trades_used == 0:
        msg = f"<b>2YC 4H Backtest — {result.symbol}</b>\n\nNo closed trades found in available history."
        if result.open_trade:
            ot = result.open_trade
            msg += (f"\n\n📌 Currently in an open position since {ot['entry_date']} "
                    f"@ {ot['entry_price']} — unrealized {ot['unrealized_pct']:+.2f}%")
        return msg

    lines = [f"<b>2YC 4H Backtest — {result.symbol}</b>"]
    lines.append(f"Trades analyzed: {result.trades_used} of {result.total_trades_all} total closed trades")
    lines.append(f"\n<b>Total P&amp;L: ${result.total_pnl_dollars:+,.0f} ({result.total_pnl_pct:+.2f}%)</b> "
                 f"(${100_000:,} per trade, not compounded)")
    lines.append(f"Win rate: {result.wins}/{result.trades_used} ({result.win_rate_pct:.1f}%)")
    lines.append(f"Avg trade: {result.avg_pnl_pct:+.2f}% | Best: {result.best_pct:+.2f}% | Worst: {result.worst_pct:+.2f}%")
    lines.append(f"Range: {result.first_trade_date} → {result.last_trade_date}")

    if result.open_trade:
        ot = result.open_trade
        lines.append(f"\n📌 Currently in an open position since {ot['entry_date']} "
                     f"@ {ot['entry_price']} — unrealized {ot['unrealized_pct']:+.2f}%")

    return "\n".join(lines)


# ── Full scan trigger ──────────────────────────────────────────────────────────

def _trigger_full_scan() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "2yclocal/2yc-4h")

    if not token:
        return "⚠️ GITHUB_TOKEN not set — cannot trigger scan remotely."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/full-scan.yml/dispatches"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main"},
        timeout=10,
    )

    if resp.status_code == 204:
        return (
            "🚀 <b>Full scan launched!</b>\n\n"
            "Universe: S&amp;P 500 + S&amp;P 400 + TSX Composite\n"
            "Signal: 4H EMA20/EMA50/SMA200 crossover + RSI &lt; 80 + D1 open &gt; D1 SMA200\n\n"
        )
    return f"⚠️ Failed to trigger scan (HTTP {resp.status_code}): {resp.text[:120]}"


def _trigger_watchlist_backtest() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "2yclocal/2yc-4h")

    if not token:
        return "⚠️ GITHUB_TOKEN not set — cannot trigger backtest remotely."

    url = f"https://api.github.com/repos/{repo}/actions/workflows/watchlist-backtest.yml/dispatches"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"ref": "main"},
        timeout=10,
    )

    if resp.status_code == 204:
        return (
            "🚀 <b>Watchlist backtest launched!</b>\n\n"
            "Universe: S&amp;P 500 + S&amp;P 400 + TSX Composite\n"
            "Ranking: win rate over up to the last 100 closed trades per symbol\n"
            "This runs in GitHub Actions and can take up to a couple hours — "
            "results post here automatically when done.\n"
        )
    return f"⚠️ Failed to trigger backtest (HTTP {resp.status_code}): {resp.text[:120]}"


# ── Help text ──────────────────────────────────────────────────────────────────

_HELP = (
    "<b>2YC 4H 20/50/200 Scanner Bot</b>\n\n"
    "<b>On-demand symbol check:</b>\n"
    "<code>/check AAPL MSFT NVDA</code>\n"
    "<code>/check RY.TO ENB.TO CP.TO</code>\n"
    "<code>/check AAPL NVDA RY.TO</code>\n"
    "<b>Full S&amp;P 500 + S&amp;P 400 + TSX Composite scan:</b>\n"
    "<code>/fullscan</code>\n"
    "Universe: S&amp;P 500 + S&amp;P 400 + TSX Composite · 4H EMA20/EMA50/SMA200 + RSI &lt; 80 + D1 open &gt; D1 SMA200\n\n"
    "<b>Backtest a symbol (4H, ~2 yrs of history):</b>\n"
    "<code>/backtest AAPL</code>\n\n"
    "<b>Backtest &amp; rank the whole watchlist by win rate:</b>\n"
    "<code>/watchlist</code>\n"
    "Runs in the background (up to ~2 hrs) — posts top 50 here when done"
)


# ── Message handler ────────────────────────────────────────────────────────────

def _handle(msg: dict):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if not text or not text.startswith("/"):
        return

    cmd = _base_command(text)
    sent_at = datetime.fromtimestamp(msg["date"], tz=timezone.utc).astimezone(_MT).strftime("%H:%M %Z")
    picked_up = datetime.now(timezone.utc).astimezone(_MT).strftime("%H:%M %Z")

    if cmd in ("/start", "/help"):
        _send(chat_id, _HELP)
        return

    if cmd in ("/fullscan", "/runscan", "/daily"):
        logger.info("Full scan requested via Telegram")
        result = _trigger_full_scan()
        _send(chat_id, f"📨 Received at {picked_up} (sent {sent_at})\n\n{result}")
        return

    if cmd in ("/watchlist", "/backtestall"):
        logger.info("Watchlist backtest requested via Telegram")
        result = _trigger_watchlist_backtest()
        _send(chat_id, f"📨 Received at {picked_up} (sent {sent_at})\n\n{result}")
        return

    if cmd in ("/check", "/scan", "/filter"):
        symbols = _parse_symbols(text)
        if not symbols:
            _send(chat_id, "Include at least one symbol — e.g.\n/check AAPL MSFT RY.TO")
            return
        logger.info(f"Scanning: {symbols}")
        _send(chat_id,
            f"📨 Received at {picked_up} (sent {sent_at})\n"
            f"⏳ Scanning {len(symbols)} symbol(s): {', '.join(symbols)}"
        )
        _send(chat_id, _run_scan(symbols))
        return

    if cmd == "/backtest":
        symbols = _parse_symbols(text)
        if len(symbols) != 1:
            _send(chat_id, "Include exactly one symbol — e.g.\n/backtest AAPL")
            return
        sym = symbols[0]
        logger.info(f"Backtesting: {sym}")
        _send(chat_id, f"📨 Received at {picked_up} (sent {sent_at})\n⏳ Backtesting {sym}…")
        _send(chat_id, _run_backtest(sym))


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(silent=True)
    if not update:
        abort(400)

    def _process():
        try:
            msg = update.get("message") or update.get("channel_post")
            if msg:
                _handle(msg)
        except Exception as exc:
            logger.error(f"Handler error: {exc}")

    threading.Thread(target=_process, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
