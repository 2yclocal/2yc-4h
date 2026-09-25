"""
On-demand symbol filter — used by the /check command only.

The full scan uses index membership (S&P 500/400, TSX Composite) as the
size and liquidity gate, so this module is not called during scheduled scans.

Rules:
  1. Market cap > $5B
  2. Average daily dollar volume > $1M
"""

from __future__ import annotations

import logging
from scanner.data_provider import SymbolMeta
from scanner.config import settings

logger = logging.getLogger(__name__)


def passes_base_filter(meta: SymbolMeta) -> tuple[bool, str]:
    """Returns (passes, reason). reason is non-empty only on failure."""
    if meta.market_cap is None:
        return False, "Market cap unavailable"

    if meta.market_cap < settings.min_market_cap:
        return False, (
            f"Mkt cap ${meta.market_cap/1e9:.2f}B < "
            f"${settings.min_market_cap/1e9:.0f}B threshold"
        )

    if meta.avg_daily_volume is None:
        return False, "Avg daily volume unavailable"

    if meta.avg_daily_volume < settings.min_avg_daily_volume:
        return False, (
            f"ADV ${meta.avg_daily_volume:,.0f} < "
            f"${settings.min_avg_daily_volume:,.0f} threshold"
        )

    return True, ""
