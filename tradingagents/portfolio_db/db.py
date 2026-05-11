"""SQLite connection + schema initialization.

Schema is intentionally small — three tables that together answer:
1. "What did I hold on date X?" (positions_snapshot)
2. "What did the PM recommend?" (decisions)
3. "What did I actually trade?" (executions)
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(
    os.environ.get(
        "TRADINGAGENTS_PORTFOLIO_DB",
        Path.home() / ".tradingagents" / "portfolio.db",
    )
)


SCHEMA = """
-- Ticker registry: single source of truth for symbol → price / metadata.
-- positions_snapshot.last_price is kept as a denormalized cache (for backwards
-- compatibility with existing SELECTs) but the authoritative price lives here.
CREATE TABLE IF NOT EXISTS tickers (
    symbol         TEXT PRIMARY KEY,
    last_price     REAL,
    name           TEXT,         -- company name (fetched on demand)
    sector         TEXT,         -- e.g. Technology
    last_updated   TEXT          -- ISO timestamp
);

-- Snapshot of holdings imported from a broker CSV. Multiple snapshots per
-- ticker (one per import_date). Source of truth = broker; DB = audit trail.
CREATE TABLE IF NOT EXISTS positions_snapshot (
    snapshot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    import_date      TEXT NOT NULL,            -- YYYY-MM-DD when CSV was imported
    statement_date   TEXT,                     -- YYYY-MM-DD on the broker statement
    account_id       TEXT NOT NULL,
    account_name     TEXT NOT NULL,
    account_type     TEXT NOT NULL,            -- Roth/TaxDeferred/Taxable/ChildEdu/Unknown
    symbol           TEXT NOT NULL,
    quantity         REAL NOT NULL,
    last_price       REAL,
    current_value    REAL NOT NULL,
    cost_basis_total REAL,
    avg_cost         REAL,
    UNIQUE (import_date, account_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_symbol ON positions_snapshot (symbol);
CREATE INDEX IF NOT EXISTS idx_snapshot_date   ON positions_snapshot (import_date);

-- PM decisions, one row per (ticker, trade_date). The full final markdown
-- and the per-account JSON live here so we can reconcile against
-- executions later.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    rating           TEXT NOT NULL,            -- Buy/Overweight/Hold/Underweight/Sell
    final_decision   TEXT NOT NULL,            -- full markdown
    account_actions  TEXT,                     -- JSON list, may be NULL
    raw_return       REAL,                     -- filled by Reflector later
    alpha_return     REAL,
    holding_days     INTEGER,
    reflection       TEXT,                     -- 2-4 sentences from Reflector
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (trade_date, symbol, decision_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions (symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_trade_date ON decisions (trade_date);

-- Manually recorded executions — what the user actually traded after
-- seeing PM's advice. decision_id is optional (some trades aren't
-- driven by the system).
CREATE TABLE IF NOT EXISTS executions (
    execution_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    account_id       TEXT NOT NULL,
    account_name     TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    action           TEXT NOT NULL,            -- BUY / SELL
    shares           REAL NOT NULL,
    price            REAL NOT NULL,
    decision_id      INTEGER REFERENCES decisions(decision_id),
    note             TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_exec_symbol ON executions (symbol);
CREATE INDEX IF NOT EXISTS idx_exec_date   ON executions (trade_date);
"""


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection with row factory + foreign keys enabled."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> Path:
    """Create tables if missing. Idempotent — also runs lightweight migrations."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # Migration: add broker column to positions_snapshot if missing
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(positions_snapshot)").fetchall()}
        if "broker" not in cols:
            conn.execute("ALTER TABLE positions_snapshot ADD COLUMN broker TEXT")
            conn.execute("UPDATE positions_snapshot SET broker = 'Fidelity' WHERE broker IS NULL")
        # New table: cost_basis_lots for per-purchase lot tracking
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cost_basis_lots (
                lot_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_date TEXT NOT NULL,
                account_id    TEXT NOT NULL,
                account_name  TEXT NOT NULL,
                symbol        TEXT NOT NULL,
                shares        REAL NOT NULL,
                cost_per_share REAL NOT NULL,
                broker        TEXT,
                note          TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_symbol ON cost_basis_lots (symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lots_account ON cost_basis_lots (account_id, symbol)")

        # Migration: add sector/name/market_cap columns to tickers if missing
        ticker_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickers)").fetchall()}
        for col_name, col_type in [
            ("name", "TEXT"),
            ("sector", "TEXT"),
            ("industry", "TEXT"),
            ("market_cap", "REAL"),
            ("beta", "REAL"),
        ]:
            if col_name not in ticker_cols:
                conn.execute(f"ALTER TABLE tickers ADD COLUMN {col_name} {col_type}")

        if "owner" not in cols:
            conn.execute("ALTER TABLE positions_snapshot ADD COLUMN owner TEXT")
            # Best-effort backfill based on account_name patterns
            for pattern, owner in [
                ("%Olivia%", "Olivia"),
                ("%Amelia%", "Amelia"),
                ("%Meimei%", "Meimei"),
                ("%Joint%", "Joint"),
                ("%-SF%", "Spouse"),
            ]:
                conn.execute(
                    "UPDATE positions_snapshot SET owner = ? WHERE owner IS NULL AND account_name LIKE ?",
                    (owner, pattern),
                )
            conn.execute("UPDATE positions_snapshot SET owner = 'Self' WHERE owner IS NULL")

        # Migration: instruction column on decisions for proper cache-by-instruction
        decision_cols = {row["name"] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
        if "instruction" not in decision_cols:
            conn.execute("ALTER TABLE decisions ADD COLUMN instruction TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_instruction ON decisions (symbol, instruction)")

        # Per-account cash balance for budget-constrained allocation on /today
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_cash (
                account_id   TEXT PRIMARY KEY,
                cash         REAL NOT NULL DEFAULT 0,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # Per-ticker target portfolio weight (as percent, e.g. 5.0 = 5%).
        # Lets /today rank under-weight tickers ahead of those already at
        # target so an add deploys capital where the gap is biggest.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS target_weights (
                symbol       TEXT PRIMARY KEY,
                target_pct   REAL NOT NULL,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # Per-sector target weight — catches "every ticker at target but the
        # sector is 50% overweight" concentration risk that ticker-level
        # targets miss.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_targets (
                sector       TEXT PRIMARY KEY,
                target_pct   REAL NOT NULL,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # Cached pairwise daily-return correlations over a fixed look-back
        # window. Used by /today to discount add priorities for buys that
        # heavily overlap with another buy already higher in the queue (e.g.
        # buying NVDA, AMD, AVGO together isn't 3x diversified). symbol_a is
        # stored alphabetically before symbol_b so each pair has one row.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ticker_correlations (
                symbol_a     TEXT NOT NULL,
                symbol_b     TEXT NOT NULL,
                correlation  REAL NOT NULL,
                period_days  INTEGER NOT NULL DEFAULT 90,
                updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (symbol_a, symbol_b)
            )
            """
        )
    return path
