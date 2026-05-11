"""Import broker CSV snapshots into the database."""

from __future__ import annotations

import csv as _csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from tradingagents.portfolio.holdings import (
    AccountType,
    Position,
    classify_account,
    parse_fidelity_csv,
)
from tradingagents.portfolio_db.db import connect


def parse_manual_csv(path: Path) -> list[Position]:
    """Parse a user-edited manual positions CSV (e.g. for Robinhood / Interactive
    Brokers / Schwab — any broker that does not export Fidelity-format CSV).

    Expected columns (header row required):
      account_id, account_name, account_type, symbol, quantity,
      last_price, cost_basis_total

    ``account_type`` is optional — if blank, classify_account() guesses from
    account_name. ``last_price`` is optional but recommended; if blank, the
    current_value is computed solely from cost_basis_total. avg_cost is
    derived from cost_basis_total / quantity.
    """
    out: list[Position] = []
    with open(path, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol or symbol.startswith("#"):
                continue
            try:
                quantity = float(row.get("quantity") or 0)
            except ValueError:
                continue
            if quantity <= 0:
                continue

            account_name = (row.get("account_name") or "").strip()
            account_type_raw = (row.get("account_type") or "").strip()
            try:
                account_type = AccountType(account_type_raw) if account_type_raw else classify_account(account_name)
            except ValueError:
                account_type = classify_account(account_name)

            def _f(key: str) -> float:
                raw = (row.get(key) or "").strip().replace("$", "").replace(",", "")
                try:
                    return float(raw) if raw else 0.0
                except ValueError:
                    return 0.0

            last_price = _f("last_price")
            cost_basis_total = _f("cost_basis_total")
            current_value = last_price * quantity if last_price else cost_basis_total
            avg_cost = cost_basis_total / quantity if quantity else 0.0

            out.append(Position(
                account_id=(row.get("account_id") or account_name).strip(),
                account_name=account_name,
                account_type=account_type,
                symbol=symbol,
                quantity=quantity,
                last_price=last_price,
                current_value=current_value,
                cost_basis_total=cost_basis_total,
                avg_cost=avg_cost,
                broker=(row.get("broker") or "Manual").strip(),
            ))
    return out


def import_manual_snapshot(
    csv_paths: Iterable[Path],
    import_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Same as import_csv_snapshot but parses the manual-input format."""
    import_date = import_date or date.today().isoformat()
    all_positions: list[Position] = []
    files_read = 0
    for p in csv_paths:
        path = Path(p)
        if not path.exists():
            continue
        all_positions.extend(parse_manual_csv(path))
        files_read += 1

    return _write_positions(all_positions, import_date, import_date, files_read, db_path)


def _write_positions(
    positions: list[Position],
    import_date: str,
    statement_date: str,
    files_read: int,
    db_path: Path | None,
) -> dict[str, int]:
    inserted = 0
    updated = 0
    with connect(db_path) as conn:
        for pos in positions:
            cur = conn.execute(
                """
                INSERT INTO positions_snapshot (
                    import_date, statement_date, account_id, account_name, account_type,
                    symbol, quantity, last_price, current_value, cost_basis_total, avg_cost,
                    broker
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(import_date, account_id, symbol) DO UPDATE SET
                    quantity         = excluded.quantity,
                    last_price       = excluded.last_price,
                    current_value    = excluded.current_value,
                    cost_basis_total = excluded.cost_basis_total,
                    avg_cost         = excluded.avg_cost,
                    statement_date   = excluded.statement_date,
                    broker           = excluded.broker
                """,
                (
                    import_date, statement_date,
                    pos.account_id, pos.account_name, pos.account_type.value,
                    pos.symbol, pos.quantity, pos.last_price, pos.current_value,
                    pos.cost_basis_total, pos.avg_cost, pos.broker,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
    return {"files": files_read, "inserted": inserted, "updated": updated}


def carry_forward_snapshot(
    target_date: str,
    db_path: Path | None = None,
) -> int:
    """Copy the most recent snapshot prior to ``target_date`` into ``target_date``.

    A snapshot is a *full* picture of holdings on that import_date. When the
    user adds a single position via the UI on a fresh day, that day would
    otherwise contain just one row and shadow the prior day's complete picture
    in ``MAX(import_date)`` lookups. Carrying forward makes the new day a true
    superset before the new position is inserted.

    No-op if ``target_date`` already has rows or no prior snapshot exists.
    Returns the number of rows copied.
    """
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM positions_snapshot WHERE import_date = ? LIMIT 1",
            (target_date,),
        ).fetchone()
        if existing:
            return 0

        prior = conn.execute(
            "SELECT MAX(import_date) AS d FROM positions_snapshot WHERE import_date < ?",
            (target_date,),
        ).fetchone()
        prior_date = prior["d"] if prior else None
        if not prior_date:
            return 0

        cur = conn.execute(
            """
            INSERT INTO positions_snapshot (
                import_date, statement_date, account_id, account_name, account_type,
                symbol, quantity, last_price, current_value, cost_basis_total, avg_cost,
                broker
            )
            SELECT ?, statement_date, account_id, account_name, account_type,
                   symbol, quantity, last_price, current_value, cost_basis_total, avg_cost,
                   broker
            FROM positions_snapshot
            WHERE import_date = ?
            """,
            (target_date, prior_date),
        )
        return cur.rowcount or 0


def add_position(
    account_id: str,
    account_name: str,
    account_type: str | None,
    symbol: str,
    quantity: float,
    last_price: float = 0.0,
    cost_basis_total: float = 0.0,
    import_date: str | None = None,
    db_path: Path | None = None,
    broker: str = "Manual",
) -> int:
    """Insert (or replace) a single manually-entered position. Returns rowid."""
    import_date = import_date or date.today().isoformat()
    carry_forward_snapshot(import_date, db_path)
    if account_type:
        try:
            atype = AccountType(account_type)
        except ValueError:
            atype = classify_account(account_name)
    else:
        atype = classify_account(account_name)

    current_value = last_price * quantity if last_price else cost_basis_total
    avg_cost = cost_basis_total / quantity if quantity else 0.0

    pos = Position(
        account_id=account_id, account_name=account_name, account_type=atype,
        symbol=symbol.upper(), quantity=quantity, last_price=last_price,
        current_value=current_value, cost_basis_total=cost_basis_total, avg_cost=avg_cost,
        broker=broker,
    )
    result = _write_positions([pos], import_date, import_date, 0, db_path)
    return result["inserted"] + result["updated"]


def import_csv_snapshot(
    csv_paths: Iterable[Path],
    import_date: str | None = None,
    statement_date: str | None = None,
    db_path: Path | None = None,
) -> dict[str, int]:
    """Parse CSVs and write a snapshot row per (account, ticker).

    ``import_date`` defaults to today; ``statement_date`` is the date the
    broker generated the CSV (parsed from filename if possible). Returns
    counts: {"inserted": N, "updated": M, "files": F}.
    """
    import_date = import_date or date.today().isoformat()
    statement_date = statement_date or import_date

    all_positions: list[Position] = []
    files_read = 0
    for p in csv_paths:
        path = Path(p)
        if not path.exists():
            continue
        all_positions.extend(parse_fidelity_csv(path))
        files_read += 1

    return _write_positions(all_positions, import_date, statement_date, files_read, db_path)


def latest_snapshot_date(db_path: Path | None = None) -> str | None:
    """Return the most-recent ``import_date`` in YYYY-MM-DD form, or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(import_date) AS d FROM positions_snapshot"
        ).fetchone()
        return row["d"] if row and row["d"] else None


def snapshot_age_days(db_path: Path | None = None) -> int | None:
    """Days since the latest snapshot. None if DB is empty."""
    latest = latest_snapshot_date(db_path)
    if not latest:
        return None
    return (date.today() - datetime.strptime(latest, "%Y-%m-%d").date()).days


def load_latest_positions(db_path: Path | None = None) -> list[Position]:
    """Return Positions from the latest snapshot, joining tickers for canonical price.

    Replaces parse_fidelity_csv for the analyze-holdings path: DB is now the
    source of truth (UI edits, Robinhood adds, price updates, ticker fixes
    only land here). Falls back to positions_snapshot.last_price when the
    tickers table has no entry for a symbol.
    """
    latest = latest_snapshot_date(db_path)
    if not latest:
        return []
    out: list[Position] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT ps.account_id, ps.account_name, ps.account_type, ps.symbol,
                   ps.quantity, ps.cost_basis_total, ps.avg_cost, ps.broker,
                   COALESCE(t.last_price, ps.last_price) AS last_price
              FROM positions_snapshot ps
              LEFT JOIN tickers t ON t.symbol = ps.symbol
             WHERE ps.import_date = ?
            """,
            (latest,),
        ).fetchall()
    for r in rows:
        qty = float(r["quantity"] or 0)
        price = float(r["last_price"] or 0)
        try:
            acct_type = AccountType(r["account_type"]) if r["account_type"] else classify_account(r["account_name"] or "")
        except ValueError:
            acct_type = classify_account(r["account_name"] or "")
        out.append(Position(
            account_id=r["account_id"] or "",
            account_name=r["account_name"] or "",
            account_type=acct_type,
            symbol=r["symbol"],
            quantity=qty,
            last_price=price,
            current_value=qty * price,
            cost_basis_total=float(r["cost_basis_total"] or 0),
            avg_cost=float(r["avg_cost"] or 0),
            broker=r["broker"] or "Unknown",
        ))
    return out
