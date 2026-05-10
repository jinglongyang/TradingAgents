"""Weekly portfolio review status report.

Designed to run on a cron / launchd schedule (e.g. every Friday afternoon
after market close). Pulls the current alpha-vs-SPY for every pending
decision, computes a hit rate, and writes a self-contained markdown
report to outputs/weekly_status_<date>.md.

Does NOT call any LLM — purely yfinance + DB. Cheap to run on a schedule.

Usage:
  uv run python scripts/weekly_status.py
  uv run python scripts/weekly_status.py --holding-days 30  # 30-day measurement window
  uv run python scripts/weekly_status.py --email you@example.com  # post-print stdout pipe target
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import yfinance as yf  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("weekly_status")


def fetch_alpha(ticker: str, trade_date: str, holding_days: int) -> tuple[float | None, float | None, int | None]:
    """N-day return + alpha vs SPY since trade_date."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end_str = (start + timedelta(days=holding_days + 7)).strftime("%Y-%m-%d")
        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        spy = yf.Ticker("SPY").history(start=trade_date, end=end_str)
        if len(stock) < 2 or len(spy) < 2:
            return None, None, None
        days = min(holding_days, len(stock) - 1, len(spy) - 1)
        raw = float((stock["Close"].iloc[days] - stock["Close"].iloc[0]) / stock["Close"].iloc[0])
        spy_ret = float((spy["Close"].iloc[days] - spy["Close"].iloc[0]) / spy["Close"].iloc[0])
        return raw, raw - spy_ret, days
    except Exception as e:
        log.warning("Failed to fetch %s: %s", ticker, e)
        return None, None, None


def classify_hit(rating: str, alpha: float | None) -> str:
    """Did the PM call go the right direction relative to SPY?"""
    if alpha is None:
        return "pending"
    bullish = rating in ("Buy", "Overweight")
    bearish = rating in ("Underweight", "Sell")
    if bullish and alpha > 0.005:  # +0.5% alpha threshold
        return "hit"
    if bearish and alpha < -0.005:
        return "hit"
    if rating == "Hold" and abs(alpha) < 0.02:
        return "hit"
    return "miss"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--holding-days", type=int, default=5,
                        help="Trading days to measure return over (default 5).")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()

    memory_log = TradingMemoryLog(DEFAULT_CONFIG)
    pending = memory_log.get_pending_entries()
    if not pending:
        print("No decisions to evaluate. Run analyze_holdings.py first.")
        return 0

    log.info("Evaluating %d pending decisions over %d trading days", len(pending), args.holding_days)

    rows = []
    for e in pending:
        raw, alpha, days = fetch_alpha(e["ticker"], e["date"], args.holding_days)
        rating = e.get("decision_summary", "?").strip()
        hit = classify_hit(rating, alpha)
        rows.append({
            "ticker": e["ticker"],
            "date": e["date"],
            "rating": rating,
            "raw": raw,
            "alpha": alpha,
            "days": days,
            "hit": hit,
        })

    today = date.today().isoformat()
    out_path = args.out_dir / f"weekly_status_{today}.md"
    args.out_dir.mkdir(parents=True, exist_ok=True)

    n_hit = sum(1 for r in rows if r["hit"] == "hit")
    n_miss = sum(1 for r in rows if r["hit"] == "miss")
    n_pending = sum(1 for r in rows if r["hit"] == "pending")
    settled = n_hit + n_miss
    hit_rate = (n_hit / settled * 100) if settled else 0

    lines = [
        f"# Weekly Status — {today}",
        "",
        f"**Window**: {args.holding_days} trading days post-decision",
        f"**Decisions tracked**: {len(rows)} (settled: {settled}, pending: {n_pending})",
        f"**Hit rate**: **{n_hit}/{settled} = {hit_rate:.0f}%**" if settled else "**Hit rate**: pending",
        "",
        "## Hit / Miss Summary",
        "",
        "| Ticker | Date | Rating | Raw | Alpha vs SPY | Days | Result |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["hit"] != "miss", -(x["alpha"] or 0))):
        raw_s = f"{r['raw']*100:+.2f}%" if r["raw"] is not None else "—"
        alpha_s = f"{r['alpha']*100:+.2f}%" if r["alpha"] is not None else "—"
        days_s = str(r["days"]) if r["days"] is not None else "—"
        result_emoji = {"hit": "✅", "miss": "❌", "pending": "⏳"}[r["hit"]]
        lines.append(f"| {r['ticker']} | {r['date']} | {r['rating']} | {raw_s} | {alpha_s} | {days_s} | {result_emoji} |")

    if n_miss:
        lines.extend([
            "",
            "## ❌ Misses (most negative alpha first)",
            "",
            "These decisions went against PM's call. Worth re-reading the original thesis.",
            "",
        ])

    if n_pending:
        lines.extend([
            "",
            f"## ⏳ Pending ({n_pending} decisions)",
            "",
            "Price data not yet available — will resolve in subsequent runs.",
        ])

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written: {out_path}")
    print(f"Hit rate: {n_hit}/{settled} = {hit_rate:.0f}%" if settled else "All decisions still pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
