"""Portfolio DB CLI.

Subcommands:
  init                              Create tables (idempotent).
  import-csv <path>... [--date Y]   Import broker CSV(s) as a snapshot.
  status                            Show snapshot freshness + execution counts.
  list-positions [--ticker T]       Show latest snapshot for a ticker.
  record-trade <args>               Log a manual execution.
  list-trades [--ticker T]          Show recent executions.
  reconcile [--since YYYY-MM-DD]    Compare PM decisions vs actual trades.

Examples:
  uv run python scripts/portfolio_db.py init
  uv run python scripts/portfolio_db.py import-csv \\
      ~/Downloads/Portfolio_Positions_May-10-2026.csv \\
      ~/Downloads/Portfolio_Positions_May-10-2026\\(1\\).csv
  uv run python scripts/portfolio_db.py status
  uv run python scripts/portfolio_db.py record-trade \\
      --symbol AMD --action SELL --shares 7 --price 455.19 \\
      --account-id 650001994 --account-name BrokerageLink-UP \\
      --note "PM Phase 2: BrokerageLink-UP 35% reduce"
  uv run python scripts/portfolio_db.py reconcile
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
    import_csv_snapshot,
    init_db,
    latest_snapshot_date,
    list_executions,
    reconcile_decisions,
    record_execution,
    snapshot_age_days,
)


def cmd_init(args) -> int:
    path = init_db(args.db)
    print(f"Initialized DB at {path}")
    return 0


def cmd_import_csv(args) -> int:
    if not args.csv:
        print("Need at least one --csv path", file=sys.stderr)
        return 2
    result = import_csv_snapshot(
        csv_paths=[Path(p) for p in args.csv],
        import_date=args.date,
        db_path=args.db,
    )
    print(f"Imported {result['files']} file(s) → "
          f"{result['inserted']} new + {result['updated']} updated rows")
    return 0


def cmd_status(args) -> int:
    latest = latest_snapshot_date(args.db)
    age = snapshot_age_days(args.db)
    with connect(args.db) as conn:
        n_pos = conn.execute("SELECT COUNT(*) c FROM positions_snapshot").fetchone()["c"]
        n_dec = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        n_exe = conn.execute("SELECT COUNT(*) c FROM executions").fetchone()["c"]
        n_tickers = conn.execute(
            "SELECT COUNT(DISTINCT symbol) c FROM positions_snapshot WHERE import_date = ?",
            (latest,),
        ).fetchone()["c"] if latest else 0

    print(f"DB:            {args.db or DEFAULT_DB_PATH}")
    print(f"Latest import: {latest or '(none)'} ({age} days ago)" if latest else "Latest import: (none)")
    print(f"Positions:     {n_pos:,} rows · {n_tickers} unique tickers in latest snapshot")
    print(f"Decisions:     {n_dec}")
    print(f"Executions:    {n_exe}")
    if age is not None and age > 30:
        print(f"\n⚠️  Snapshot is {age} days old. Re-import a fresh CSV before running analysis.")
    return 0


def cmd_list_positions(args) -> int:
    latest = latest_snapshot_date(args.db)
    if not latest:
        print("No snapshot. Run import-csv first.", file=sys.stderr)
        return 1
    sql = """
        SELECT symbol, account_name, account_type, quantity, current_value,
               cost_basis_total, last_price
        FROM positions_snapshot
        WHERE import_date = ?
    """
    params: list = [latest]
    if args.ticker:
        sql += " AND symbol = ?"
        params.append(args.ticker.upper())
    sql += " ORDER BY current_value DESC"
    with connect(args.db) as conn:
        rows = conn.execute(sql, params).fetchall()

    print(f"Positions as of {latest}{' for ' + args.ticker if args.ticker else ''}:\n")
    print(f"{'Symbol':<8}{'Account':<24}{'Type':<13}{'Qty':>10}{'Value':>14}{'P/L%':>8}")
    print("-" * 77)
    total = 0.0
    for r in rows:
        cost = r["cost_basis_total"] or 0
        pl_pct = ((r["current_value"] - cost) / cost * 100) if cost else 0
        total += r["current_value"]
        print(f"{r['symbol']:<8}{r['account_name'][:24]:<24}{r['account_type']:<13}"
              f"{r['quantity']:>10.3f}{r['current_value']:>14,.2f}{pl_pct:>+7.1f}%")
    print("-" * 77)
    print(f"Total: ${total:,.2f}")
    return 0


def cmd_record_trade(args) -> int:
    eid = record_execution(
        trade_date=args.trade_date or date.today().isoformat(),
        account_id=args.account_id,
        account_name=args.account_name,
        symbol=args.symbol.upper(),
        action=args.action,
        shares=args.shares,
        price=args.price,
        note=args.note,
        decision_id=args.decision_id,
        db_path=args.db,
    )
    print(f"Recorded execution #{eid}: {args.action} {args.shares} {args.symbol} @ ${args.price}")
    return 0


def cmd_list_trades(args) -> int:
    rows = list_executions(symbol=args.ticker.upper() if args.ticker else None,
                           since=args.since, db_path=args.db)
    if not rows:
        print("No executions recorded.")
        return 0
    print(f"{'Date':<12}{'Symbol':<8}{'Action':<7}{'Shares':>10}{'Price':>10}{'Account':<24}{'Linked':>8}")
    print("-" * 79)
    for r in rows:
        linked = f"#{r['decision_id']}" if r["decision_id"] else "—"
        print(f"{r['trade_date']:<12}{r['symbol']:<8}{r['action']:<7}"
              f"{r['shares']:>10.3f}{r['price']:>10.2f}{r['account_name'][:24]:<24}{linked:>8}")
    return 0


def cmd_reconcile(args) -> int:
    items = reconcile_decisions(since=args.since, db_path=args.db)
    if not items:
        print("No decisions to reconcile in window.")
        return 0
    counts: dict[str, int] = {}
    for it in items:
        counts[it.status] = counts.get(it.status, 0) + 1
    print(f"\n=== Reconciliation: {len(items)} decisions ===")
    for k in ("full", "partial", "not_executed", "no_recommendation", "diverged"):
        if k in counts:
            print(f"  {k:<20}{counts[k]:>4}")
    print()

    print(f"{'Date':<12}{'Symbol':<8}{'Rating':<13}{'Status':<18}Summary")
    print("-" * 100)
    for it in items:
        print(f"{it.trade_date:<12}{it.symbol:<8}{it.rating:<13}{it.status:<18}{it.summary}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, help=f"DB path (default: {DEFAULT_DB_PATH})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    p = sub.add_parser("import-csv")
    p.add_argument("csv", nargs="+")
    p.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    p.set_defaults(func=cmd_import_csv)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("list-positions")
    p.add_argument("--ticker")
    p.set_defaults(func=cmd_list_positions)

    p = sub.add_parser("record-trade")
    p.add_argument("--trade-date")
    p.add_argument("--symbol", required=True)
    p.add_argument("--action", required=True, choices=["BUY", "SELL", "buy", "sell"])
    p.add_argument("--shares", type=float, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--account-id", required=True)
    p.add_argument("--account-name", required=True)
    p.add_argument("--decision-id", type=int)
    p.add_argument("--note")
    p.set_defaults(func=cmd_record_trade)

    p = sub.add_parser("list-trades")
    p.add_argument("--ticker")
    p.add_argument("--since")
    p.set_defaults(func=cmd_list_trades)

    p = sub.add_parser("reconcile")
    p.add_argument("--since", help="YYYY-MM-DD; default = 90 days ago")
    p.set_defaults(func=cmd_reconcile)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
