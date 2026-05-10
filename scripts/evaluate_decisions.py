"""Evaluate all pending decisions in the memory log against actual returns.

This is a manual trigger of the same logic that runs automatically when
``analyze_holdings.py`` is re-invoked for a ticker — useful for periodic
"how well did the PM do?" reviews without waiting for the next natural run.

For each pending memory-log entry:
1. Pulls actual N-day return + SPY-relative alpha from yfinance.
2. Generates a 2-4 sentence reflection via the Reflector LLM.
3. Writes the resolved record back to the memory log.

Usage:
  uv run python scripts/evaluate_decisions.py
  uv run python scripts/evaluate_decisions.py --holding-days 30
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import yfinance as yf  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402
from tradingagents.graph.reflection import Reflector  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("evaluate_decisions")


def fetch_alpha(ticker: str, trade_date: str, holding_days: int) -> tuple[float | None, float | None, int | None]:
    """Mirror of TradingAgentsGraph._fetch_returns, broken out for reuse."""
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d")
        end = start + timedelta(days=holding_days + 7)
        end_str = end.strftime("%Y-%m-%d")
        stock = yf.Ticker(ticker).history(start=trade_date, end=end_str)
        spy = yf.Ticker("SPY").history(start=trade_date, end=end_str)
        if len(stock) < 2 or len(spy) < 2:
            return None, None, None
        actual_days = min(holding_days, len(stock) - 1, len(spy) - 1)
        raw = float(
            (stock["Close"].iloc[actual_days] - stock["Close"].iloc[0])
            / stock["Close"].iloc[0]
        )
        spy_ret = float(
            (spy["Close"].iloc[actual_days] - spy["Close"].iloc[0])
            / spy["Close"].iloc[0]
        )
        return raw, raw - spy_ret, actual_days
    except Exception as e:
        log.warning("Failed to fetch %s on %s: %s", ticker, trade_date, e)
        return None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--holding-days", type=int, default=5, help="Trading days to measure return over (default 5).")
    parser.add_argument("--quick-model", default="gpt-5.4-mini")
    parser.add_argument("--dry-run", action="store_true", help="Show pending entries and current alpha without writing reflections.")
    args = parser.parse_args()

    memory_log = TradingMemoryLog(DEFAULT_CONFIG)
    pending = memory_log.get_pending_entries()
    if not pending:
        log.info("No pending entries in the memory log.")
        return 0
    log.info("Found %d pending entries", len(pending))

    if args.dry_run:
        print(f"\n{'Ticker':<8}{'Date':<12}{'Decision':<14}{'Raw Return':>12}{'Alpha vs SPY':>14}{'Days':>6}")
        print("-" * 70)
        for e in pending:
            raw, alpha, days = fetch_alpha(e["ticker"], e["date"], args.holding_days)
            if raw is None:
                print(f"{e['ticker']:<8}{e['date']:<12}{e.get('decision_summary', '?')[:14]:<14}  (no data yet)")
            else:
                print(f"{e['ticker']:<8}{e['date']:<12}{e.get('decision_summary', '?')[:14]:<14}"
                      f"{raw*100:>+11.2f}%{alpha*100:>+13.2f}%{days:>6}")
        return 0

    # Real evaluation: fetch returns + LLM reflection + write back
    config = DEFAULT_CONFIG.copy()
    config["quick_think_llm"] = args.quick_model
    quick_client = create_llm_client(
        provider=config["llm_provider"],
        model=config["quick_think_llm"],
        base_url=config.get("backend_url"),
    )
    reflector = Reflector(quick_client.get_llm())

    updates = []
    for e in pending:
        raw, alpha, days = fetch_alpha(e["ticker"], e["date"], args.holding_days)
        if raw is None:
            log.info("Skipping %s @ %s — no price data yet", e["ticker"], e["date"])
            continue
        log.info("Resolving %s @ %s: raw=%.2f%% alpha=%.2f%%", e["ticker"], e["date"], raw * 100, alpha * 100)
        reflection = reflector.reflect_on_final_decision(
            final_decision=e.get("decision", ""),
            raw_return=raw,
            alpha_return=alpha,
        )
        updates.append({
            "ticker": e["ticker"],
            "trade_date": e["date"],
            "raw_return": raw,
            "alpha_return": alpha,
            "holding_days": days,
            "reflection": reflection,
        })

    if updates:
        memory_log.batch_update_with_outcomes(updates)
        log.info("Wrote %d reflections to memory log", len(updates))
    else:
        log.info("No entries had usable price data yet — try again in a few days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
