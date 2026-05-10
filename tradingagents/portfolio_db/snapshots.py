"""Import broker CSV snapshots into the database."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from tradingagents.portfolio.holdings import Position, parse_fidelity_csv
from tradingagents.portfolio_db.db import connect


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

    inserted = 0
    updated = 0
    with connect(db_path) as conn:
        for pos in all_positions:
            cur = conn.execute(
                """
                INSERT INTO positions_snapshot (
                    import_date, statement_date, account_id, account_name, account_type,
                    symbol, quantity, last_price, current_value, cost_basis_total, avg_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(import_date, account_id, symbol) DO UPDATE SET
                    quantity         = excluded.quantity,
                    last_price       = excluded.last_price,
                    current_value    = excluded.current_value,
                    cost_basis_total = excluded.cost_basis_total,
                    avg_cost         = excluded.avg_cost,
                    statement_date   = excluded.statement_date
                """,
                (
                    import_date, statement_date,
                    pos.account_id, pos.account_name, pos.account_type.value,
                    pos.symbol, pos.quantity, pos.last_price, pos.current_value,
                    pos.cost_basis_total, pos.avg_cost,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1

    return {"files": files_read, "inserted": inserted, "updated": updated}


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
