"""Regression: adding a single position on a new day must not shadow the
prior day's full snapshot.

Before the fix, ``add_position`` wrote one row with today's ``import_date``,
and ``latest_snapshot_date()`` returned today — so the by-account view only
saw that single ticker until the next CSV import.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tradingagents.portfolio_db.db import connect, init_db
from tradingagents.portfolio_db.snapshots import (
    add_position,
    carry_forward_snapshot,
    latest_snapshot_date,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db = tmp_path / "portfolio.db"
    init_db(db)
    return db


def _seed(db: Path, import_date: str, rows: list[tuple[str, str, str, str, float]]) -> None:
    with connect(db) as conn:
        for (account_id, account_name, account_type, symbol, qty) in rows:
            conn.execute(
                """
                INSERT INTO positions_snapshot (
                    import_date, statement_date, account_id, account_name, account_type,
                    symbol, quantity, last_price, current_value, cost_basis_total, avg_cost,
                    broker
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 'Fidelity')
                """,
                (import_date, import_date, account_id, account_name, account_type, symbol, qty),
            )


def _count(db: Path, import_date: str) -> int:
    with connect(db) as conn:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM positions_snapshot WHERE import_date = ?",
            (import_date,),
        ).fetchone()["n"]


@pytest.mark.unit
def test_add_position_carries_forward_prior_snapshot(temp_db: Path) -> None:
    _seed(temp_db, "2026-05-10", [
        ("A1", "Brokerage", "Taxable", "AAPL", 10),
        ("A1", "Brokerage", "Taxable", "MSFT", 5),
        ("A2", "Roth", "Roth", "VOO", 20),
    ])

    add_position(
        account_id="A2", account_name="Roth", account_type="Roth",
        symbol="RKLB", quantity=10, last_price=100, cost_basis_total=1000,
        import_date="2026-05-11", db_path=temp_db,
    )

    # All three prior holdings + new RKLB = 4 rows on the new day.
    assert _count(temp_db, "2026-05-11") == 4
    assert latest_snapshot_date(temp_db) == "2026-05-11"


@pytest.mark.unit
def test_carry_forward_is_noop_when_target_date_already_populated(temp_db: Path) -> None:
    _seed(temp_db, "2026-05-10", [("A1", "Brokerage", "Taxable", "AAPL", 10)])
    _seed(temp_db, "2026-05-11", [("A1", "Brokerage", "Taxable", "MSFT", 5)])

    copied = carry_forward_snapshot("2026-05-11", db_path=temp_db)

    assert copied == 0
    assert _count(temp_db, "2026-05-11") == 1


@pytest.mark.unit
def test_carry_forward_noop_on_empty_db(temp_db: Path) -> None:
    assert carry_forward_snapshot("2026-05-11", db_path=temp_db) == 0


@pytest.mark.unit
def test_add_position_on_same_day_does_not_duplicate(temp_db: Path) -> None:
    _seed(temp_db, "2026-05-11", [("A1", "Brokerage", "Taxable", "AAPL", 10)])

    add_position(
        account_id="A1", account_name="Brokerage", account_type="Taxable",
        symbol="MSFT", quantity=5, import_date="2026-05-11", db_path=temp_db,
    )

    assert _count(temp_db, "2026-05-11") == 2
