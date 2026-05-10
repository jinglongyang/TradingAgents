"""Phase 1 execution helper — record per-account fills after manual trades.

Designed for the common flow: user reads PM advice (e.g. "CRWV Exit 100%"),
places six broker orders for CRWV across six accounts, then needs to log
each fill into the DB without typing eight CLI flags per row.

Usage:
  # Sell 100% of CRWV across all accounts (PM said Exit), all at $73.65
  uv run python scripts/phase1_helper.py CRWV --action SELL --price 73.65

  # Sell only the Taxable accounts (TLH targeting), at different fills
  uv run python scripts/phase1_helper.py CRWV --action SELL --account-types Taxable \\
      --price 73.65

  # Dry-run: show what would be recorded without writing
  uv run python scripts/phase1_helper.py CRWV --action SELL --price 73.65 --dry-run

  # Custom percentage (e.g. PM said Reduce 35%)
  uv run python scripts/phase1_helper.py AMD --action SELL --price 455.19 --pct 35
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from tradingagents.portfolio_db import (  # noqa: E402
    DEFAULT_DB_PATH,
    connect,
    latest_snapshot_date,
    record_execution,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol")
    parser.add_argument("--action", required=True, choices=["BUY", "SELL", "buy", "sell"])
    parser.add_argument("--price", type=float, required=True,
                        help="Fill price (assumed identical across accounts).")
    parser.add_argument("--pct", type=float, default=100.0,
                        help="Percentage of current shares to trade (default 100).")
    parser.add_argument("--account-types", nargs="*",
                        choices=["Roth", "TaxDeferred", "Taxable", "ChildEdu", "Unknown"],
                        help="Restrict to these account types only.")
    parser.add_argument("--accounts", nargs="*",
                        help="Restrict to these specific account names (substring match).")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--note", default=None, help="Free-text note attached to all rows.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without writing to DB.")
    parser.add_argument("--db", type=Path, help=f"DB path (default: {DEFAULT_DB_PATH})")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    action = args.action.upper()
    pct = args.pct / 100.0

    latest = latest_snapshot_date(args.db)
    if not latest:
        print("No snapshot in DB. Run portfolio_db.py import-csv first.", file=sys.stderr)
        return 1

    sql = """
        SELECT account_id, account_name, account_type, quantity, current_value
        FROM positions_snapshot
        WHERE import_date = ? AND symbol = ?
    """
    params: list = [latest, symbol]
    if args.account_types:
        sql += " AND account_type IN ({})".format(",".join(["?"] * len(args.account_types)))
        params.extend(args.account_types)
    sql += " ORDER BY current_value DESC"

    with connect(args.db) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if args.accounts:
        rows = [
            r for r in rows
            if any(needle.lower() in r["account_name"].lower() for needle in args.accounts)
        ]

    if not rows:
        print(f"No {symbol} positions match the filters.", file=sys.stderr)
        return 1

    print(f"\n=== {symbol} {action} {args.pct:.0f}% — Plan ({len(rows)} rows) ===\n")
    print(f"{'#':>3}  {'Account':<25} {'Type':<12} {'Cur Qty':>10} {'Trade Qty':>10} {'$ Impact':>12}")
    print("-" * 80)

    plan = []
    for i, r in enumerate(rows, 1):
        trade_qty = round(r["quantity"] * pct, 6)
        if trade_qty <= 0:
            continue
        dollar = trade_qty * args.price
        plan.append({"row": r, "qty": trade_qty, "dollar": dollar})
        sign = "-" if action == "SELL" else "+"
        print(f"{i:>3}  {r['account_name'][:25]:<25} {r['account_type']:<12} "
              f"{r['quantity']:>10.3f} {trade_qty:>10.3f} {sign}${abs(dollar):>10,.0f}")

    total = sum(p["dollar"] for p in plan)
    print("-" * 80)
    sign = "-" if action == "SELL" else "+"
    print(f"Total: {sign}${abs(total):,.2f} @ ${args.price:.2f}/share")

    if args.dry_run:
        print("\n[dry-run] Nothing written to DB.")
        return 0

    confirm = input("\nProceed to record these executions? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 0

    eids = []
    for p in plan:
        eid = record_execution(
            trade_date=args.trade_date,
            account_id=p["row"]["account_id"],
            account_name=p["row"]["account_name"],
            symbol=symbol,
            action=action,
            shares=p["qty"],
            price=args.price,
            note=args.note,
            db_path=args.db,
        )
        eids.append(eid)

    print(f"\nRecorded {len(eids)} executions: ids {eids[0]}..{eids[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
