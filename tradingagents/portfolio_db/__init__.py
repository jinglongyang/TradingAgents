"""SQLite-backed portfolio tracking.

Source of truth remains the broker CSV — the database stores historical
snapshots, manually-recorded executions, and links to PM decisions so we
can answer "what changed since last snapshot?" and "did I follow the PM's
advice?" across quarters.
"""

from tradingagents.portfolio_db.db import (
    DEFAULT_DB_PATH,
    connect,
    init_db,
)
from tradingagents.portfolio_db.snapshots import (
    add_position,
    import_csv_snapshot,
    import_manual_snapshot,
    latest_snapshot_date,
    load_latest_positions,
    snapshot_age_days,
)
from tradingagents.portfolio_db.executions import (
    record_execution,
    list_executions,
)
from tradingagents.portfolio_db.reconcile import reconcile_decisions

__all__ = [
    "DEFAULT_DB_PATH",
    "connect",
    "init_db",
    "add_position",
    "import_csv_snapshot",
    "import_manual_snapshot",
    "latest_snapshot_date",
    "load_latest_positions",
    "snapshot_age_days",
    "record_execution",
    "list_executions",
    "reconcile_decisions",
]
