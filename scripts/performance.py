"""Forward-test PM decisions against actual market returns.

Walks every (trade_date, symbol) in the decisions table, pulls the
realized N-day return and SPY-relative alpha from yfinance, classifies
hit/miss vs the rating direction, and outputs aggregate hit rates by:
- rating bucket (Buy/Overweight/Hold/Underweight/Sell)
- holding window (5d, 30d, 180d configurable)

This is "forward-test" not true backtest — decisions are real, returns
are real, no LLM time-travel. The output is what we'd ultimately use
to know whether the PM is adding value.

Usage:
  uv run python scripts/performance.py
  uv run python scripts/performance.py --holding-days 30
  uv run python scripts/performance.py --windows 5,30,90  # multiple windows
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import yfinance as yf  # noqa: E402

from tradingagents.portfolio_db import connect, init_db  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("performance")


def alpha_for(ticker: str, trade_date: str, days: int) -> tuple[float | None, float | None]:
    """N-day raw return + alpha vs SPY. None if data not yet available."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end_str = (start + timedelta(days=days + 7)).strftime("%Y-%m-%d")
        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        spy = yf.Ticker("SPY").history(start=trade_date, end=end_str)
        if len(stock) < 2 or len(spy) < 2:
            return None, None
        d = min(days, len(stock) - 1, len(spy) - 1)
        raw = float((stock["Close"].iloc[d] - stock["Close"].iloc[0]) / stock["Close"].iloc[0])
        spy_ret = float((spy["Close"].iloc[d] - spy["Close"].iloc[0]) / spy["Close"].iloc[0])
        return raw, raw - spy_ret
    except Exception as e:
        log.warning("Failed %s: %s", ticker, e)
        return None, None


def classify(rating: str, alpha: float | None, threshold: float = 0.005) -> str:
    if alpha is None:
        return "pending"
    bullish = rating in ("Buy", "Overweight")
    bearish = rating in ("Underweight", "Sell")
    if bullish and alpha > threshold:
        return "hit"
    if bearish and alpha < -threshold:
        return "hit"
    if rating == "Hold" and abs(alpha) < 0.02:
        return "hit"
    return "miss"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", default="5,30", help="Comma-separated holding-day windows")
    args = parser.parse_args()

    windows = [int(w) for w in args.windows.split(",")]
    init_db()

    with connect() as conn:
        decisions = conn.execute(
            "SELECT trade_date, symbol, rating FROM decisions ORDER BY trade_date, symbol"
        ).fetchall()

    if not decisions:
        print("No decisions yet — run analyze_holdings.py first.")
        return 0

    print(f"Evaluating {len(decisions)} decisions across {len(windows)} windows...\n")

    # Per-decision per-window
    results: list[dict] = []
    for d in decisions:
        row = {"trade_date": d["trade_date"], "symbol": d["symbol"], "rating": d["rating"]}
        for w in windows:
            raw, alpha = alpha_for(d["symbol"], d["trade_date"], w)
            row[f"raw_{w}d"] = raw
            row[f"alpha_{w}d"] = alpha
            row[f"hit_{w}d"] = classify(d["rating"], alpha)
        results.append(row)

    # Print per-decision table for shortest window
    short_w = windows[0]
    print(f"=== Per-decision ({short_w}-day window) ===")
    print(f"{'Ticker':<8}{'Date':<12}{'Rating':<13}{'Raw':>9}{'Alpha':>9}  Result")
    print("-" * 70)
    for r in results:
        raw = r.get(f"raw_{short_w}d")
        alpha = r.get(f"alpha_{short_w}d")
        raw_s = f"{raw*100:+.2f}%" if raw is not None else "—"
        alpha_s = f"{alpha*100:+.2f}%" if alpha is not None else "—"
        hit = r.get(f"hit_{short_w}d", "?")
        emoji = {"hit": "✅", "miss": "❌", "pending": "⏳"}[hit]
        print(f"{r['symbol']:<8}{r['trade_date']:<12}{r['rating']:<13}{raw_s:>9}{alpha_s:>9}  {emoji}")

    # Aggregate hit rate per rating × window
    print(f"\n=== Aggregate hit rate ===")
    print(f"{'Rating':<13}" + "".join(f"{w}d{'':>7}" for w in windows))
    print("-" * 60)
    for rating in ["Buy", "Overweight", "Hold", "Underweight", "Sell"]:
        rows_r = [r for r in results if r["rating"] == rating]
        if not rows_r:
            continue
        line = f"{rating:<13}"
        for w in windows:
            settled = [r for r in rows_r if r[f"hit_{w}d"] != "pending"]
            hits = sum(1 for r in settled if r[f"hit_{w}d"] == "hit")
            if not settled:
                line += f"{'pending':<10}"
            else:
                hr = hits / len(settled) * 100
                line += f"{hits}/{len(settled)} ({hr:.0f}%)".ljust(10)
        print(line)

    # Overall
    print(f"{'-'*60}")
    line = f"{'TOTAL':<13}"
    for w in windows:
        settled = [r for r in results if r[f"hit_{w}d"] != "pending"]
        hits = sum(1 for r in settled if r[f"hit_{w}d"] == "hit")
        if not settled:
            line += f"{'pending':<10}"
        else:
            hr = hits / len(settled) * 100
            line += f"{hits}/{len(settled)} ({hr:.0f}%)".ljust(10)
    print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
