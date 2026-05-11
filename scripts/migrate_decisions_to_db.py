"""Migrate all per-ticker analysis JSON files into the decisions DB table.

Scans outputs/portfolio_analysis_*/per_ticker/*.json, parses each
final_trade_decision markdown for rating + price fields + account_actions,
and writes one row per (trade_date, symbol). Later runs overwrite earlier
ones so the DB always reflects the latest analysis per ticker.

Usage:
  uv run python scripts/migrate_decisions_to_db.py
  uv run python scripts/migrate_decisions_to_db.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from tradingagents.portfolio_db import connect, init_db  # noqa: E402


RATING_RE = re.compile(r"\*\*Rating\*\*:\s*(\w+)", re.IGNORECASE)
PT_RE = re.compile(r"Price Target.*?\$?([\d.]+)")
TIME_HORIZON_RE = re.compile(r"\*\*Time Horizon\*\*:\s*([^\n]+)")
ACCT_ACTION_RE = re.compile(
    r"\*\*([^*]+?)\*\*\s*\[(\w+)\]\s*→\s*\*\*(\w+)\*\*"
    r"(?:\s*\((\d+)% of current\))?"
    r"\s*:\s*([^\n]+)",
)


def _parse_rating(md: str) -> str:
    m = RATING_RE.search(md or "")
    return m.group(1) if m else ""


def _parse_account_actions(md: str) -> list[dict]:
    if "Per-Account Actions" not in (md or ""):
        return []
    after = md.split("Per-Account Actions", 1)[1]
    out = []
    for m in ACCT_ACTION_RE.finditer(after):
        out.append({
            "account_name": m.group(1).strip(),
            "account_type": m.group(2),
            "action": m.group(3),
            "size_pct": float(m.group(4)) if m.group(4) else None,
            "rationale": m.group(5).strip(),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    root = Path("outputs")
    dirs = sorted(root.glob("portfolio_analysis_*/"))
    print(f"Found {len(dirs)} analysis dirs")

    # For each ticker, keep only the most recent dir (sorted lexically =
    # chronologically since dirs are timestamped YYYY-MM-DD_HHMMSS).
    ticker_to_latest: dict[str, Path] = {}
    for d in dirs:
        for jf in (d / "per_ticker").glob("*.json"):
            ticker_to_latest[jf.stem] = jf  # later wins

    print(f"Migrating {len(ticker_to_latest)} unique tickers (latest run per ticker)")

    inserted = updated = skipped = 0
    with connect() as conn:
        for ticker, jf in sorted(ticker_to_latest.items()):
            try:
                data = json.loads(jf.read_text())
            except Exception as e:
                print(f"  skip {ticker}: {e}")
                skipped += 1
                continue

            trade_date = data.get("trade_date", "")
            final_md = data.get("final_trade_decision", "")
            rating = _parse_rating(final_md)
            actions = _parse_account_actions(final_md)

            if not rating or not trade_date:
                print(f"  skip {ticker}: no rating/date")
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [dry-run] {ticker} {trade_date} {rating} ({len(actions)} actions) — src {jf.parent.parent.name}")
                continue

            # instruction may have been injected via USER_TAX_CONTEXT for this
            # run; store it so the cache check can distinguish runs that asked
            # different things.
            instruction = os.environ.get("USER_TAX_CONTEXT", "").strip() or None
            cur = conn.execute(
                """
                INSERT INTO decisions (trade_date, symbol, rating, final_decision, account_actions, instruction)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_date, symbol) DO UPDATE SET
                    rating = excluded.rating,
                    final_decision = excluded.final_decision,
                    account_actions = excluded.account_actions,
                    instruction = excluded.instruction,
                    created_at = datetime('now')
                """,
                (trade_date, ticker, rating, final_md, json.dumps(actions) if actions else None, instruction),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
            print(f"  {ticker} {trade_date} {rating} ({len(actions)} actions)")

    print(f"\nInserted: {inserted}, Updated: {updated}, Skipped: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
