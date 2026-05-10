"""Record actual broker executions and link them to PM decisions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from tradingagents.portfolio_db.db import connect


def record_execution(
    trade_date: str,
    account_id: str,
    account_name: str,
    symbol: str,
    action: str,
    shares: float,
    price: float,
    note: str | None = None,
    decision_id: int | None = None,
    db_path: Path | None = None,
) -> int:
    """Insert one execution row. Returns the new ``execution_id``.

    If ``decision_id`` is None, tries to auto-link to the most recent decision
    for the same symbol (within 90 days) so the user doesn't have to look it up.
    """
    action = action.upper()
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got {action!r}")

    with connect(db_path) as conn:
        if decision_id is None:
            row = conn.execute(
                """
                SELECT decision_id FROM decisions
                WHERE symbol = ? AND trade_date >= date(?, '-90 days')
                ORDER BY trade_date DESC LIMIT 1
                """,
                (symbol, trade_date),
            ).fetchone()
            decision_id = row["decision_id"] if row else None

        cur = conn.execute(
            """
            INSERT INTO executions (
                trade_date, account_id, account_name, symbol,
                action, shares, price, decision_id, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trade_date, account_id, account_name, symbol,
             action, shares, price, decision_id, note),
        )
        return cur.lastrowid


def list_executions(
    symbol: str | None = None,
    since: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """List executions, optionally filtered by symbol and/or date."""
    sql = "SELECT * FROM executions WHERE 1=1"
    params: list = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol)
    if since:
        sql += " AND trade_date >= ?"
        params.append(since)
    sql += " ORDER BY trade_date DESC, execution_id DESC"
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
