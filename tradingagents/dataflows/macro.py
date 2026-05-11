"""Current macro context for injection into PM prompts.

Pulls the latest values of widely-watched macro indicators so the PM can
calibrate its decisions against the current regime instead of analyzing
each ticker in a vacuum. yfinance handles all five symbols.

The output is a short markdown block that fits neatly into the PM prompt
header. Kept under 30 lines to avoid bloating the prompt for every ticker.

Indicators:
- ^VIX  : equity vol fear gauge
- ^TNX  : 10-year Treasury yield (rate-sensitivity)
- DX-Y.NYB : DXY dollar index (FX regime)
- ^GSPC : S&P 500 (broad market)
- ^IXIC : Nasdaq Composite (tech tilt)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf


log = logging.getLogger(__name__)


_INDICATORS = [
    ("^VIX", "VIX 恐慌指数"),
    ("^TNX", "10年期国债收益率"),
    ("DX-Y.NYB", "DXY 美元指数"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq Composite"),
]


@dataclass(frozen=True)
class MacroSnapshot:
    """Latest value + 30-day change for each indicator."""

    rows: list[tuple[str, str, float, float]]  # (symbol, label, latest, pct_30d)


def fetch_macro_snapshot() -> MacroSnapshot:
    """Pull the most recent value + 30-day percentage change for each indicator."""
    rows: list[tuple[str, str, float, float]] = []
    for symbol, label in _INDICATORS:
        try:
            hist = yf.Ticker(symbol).history(period="60d")
            if len(hist) < 2:
                continue
            latest = float(hist["Close"].iloc[-1])
            past_idx = max(0, len(hist) - 22)  # ~30 calendar days = ~22 trading
            past = float(hist["Close"].iloc[past_idx])
            pct_30d = (latest - past) / past * 100 if past else 0
            rows.append((symbol, label, latest, pct_30d))
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to fetch %s: %s", symbol, e)
            continue
    return MacroSnapshot(rows=rows)


def render_macro_block(snap: MacroSnapshot) -> str:
    """Format the snapshot as a markdown block for prompt injection."""
    if not snap.rows:
        return ""
    lines = ["**Current Macro Context (for regime calibration):**"]
    for symbol, label, latest, pct in snap.rows:
        arrow = "↑" if pct > 0.5 else "↓" if pct < -0.5 else "—"
        lines.append(f"- {label} ({symbol}): {latest:.2f} ({arrow} {pct:+.1f}% over past 30 days)")
    lines.append(
        "\nUse this for regime context only — e.g. elevated VIX favors defensive "
        "positioning, rising 10-year yield pressures long-duration growth multiples, "
        "strong DXY headwinds for multinational FX-exposed revenue. Do not override "
        "ticker-specific fundamentals based on macro alone."
    )
    return "\n".join(lines)


def get_macro_context() -> str:
    """One-shot helper: fetch + render. Returns empty string on total failure."""
    try:
        snap = fetch_macro_snapshot()
        return render_macro_block(snap)
    except Exception as e:  # noqa: BLE001
        log.warning("Macro context unavailable: %s", e)
        return ""
