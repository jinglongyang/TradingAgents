"""Compare PM decisions against actual executions.

The reconcile step answers: for each PM decision in the last N days, did
the user execute it? If yes, did they follow the recommended size? If no,
the divergence is logged so the next analysis cycle can learn from it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from tradingagents.portfolio_db.db import connect


@dataclass(frozen=True)
class ReconcileItem:
    decision_id: int
    symbol: str
    trade_date: str
    rating: str
    recommended_actions: list[dict]   # parsed account_actions JSON, possibly empty
    executions: list[dict]
    status: str   # "no_recommendation" / "not_executed" / "partial" / "full" / "diverged"
    summary: str


def _classify(rating: str, recommended: list[dict], executions: list[dict]) -> tuple[str, str]:
    """Bucket a decision into one of the status categories with a one-line summary."""
    has_action = any(a.get("action") not in (None, "Hold") for a in recommended)
    if not has_action and not executions:
        return "no_recommendation", "PM 建议 Hold，未交易（一致）"
    if not has_action and executions:
        return "diverged", f"PM 建议 Hold，但实际交易 {len(executions)} 笔"
    if has_action and not executions:
        return "not_executed", f"PM 建议有动作但未执行（{len(recommended)} 个账户级建议）"
    # both present — compare by direction
    rec_buys = sum(1 for a in recommended if a.get("action") in ("Add",))
    rec_sells = sum(1 for a in recommended if a.get("action") in ("Reduce", "Exit"))
    exe_buys = sum(1 for e in executions if e["action"] == "BUY")
    exe_sells = sum(1 for e in executions if e["action"] == "SELL")
    if rec_buys and exe_sells and not exe_buys:
        return "diverged", f"PM 建议加仓 {rec_buys} 笔，实际却卖出 {exe_sells} 笔"
    if rec_sells and exe_buys and not exe_sells:
        return "diverged", f"PM 建议减仓 {rec_sells} 笔，实际却买入 {exe_buys} 笔"
    if (rec_buys or rec_sells) and (exe_buys or exe_sells):
        executed = exe_buys + exe_sells
        recommended_count = rec_buys + rec_sells
        if executed >= recommended_count:
            return "full", f"PM 建议 {recommended_count} 笔，实际 {executed} 笔（已执行）"
        return "partial", f"PM 建议 {recommended_count} 笔，实际 {executed} 笔（部分执行）"
    return "diverged", "执行方向与建议不一致"


def reconcile_decisions(
    since: str | None = None,
    db_path: Path | None = None,
) -> list[ReconcileItem]:
    """Walk decisions since ``since`` (default 90 days ago) and classify each."""
    if not since:
        since = (date.today() - timedelta(days=90)).isoformat()

    items: list[ReconcileItem] = []
    with connect(db_path) as conn:
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE trade_date >= ? ORDER BY trade_date, symbol",
            (since,),
        ).fetchall()
        for d in decisions:
            recommended = json.loads(d["account_actions"]) if d["account_actions"] else []
            executions = [
                dict(row) for row in conn.execute(
                    """
                    SELECT * FROM executions
                    WHERE symbol = ? AND trade_date >= ?
                    ORDER BY trade_date
                    """,
                    (d["symbol"], d["trade_date"]),
                ).fetchall()
            ]
            status, summary = _classify(d["rating"], recommended, executions)
            items.append(ReconcileItem(
                decision_id=d["decision_id"],
                symbol=d["symbol"],
                trade_date=d["trade_date"],
                rating=d["rating"],
                recommended_actions=recommended,
                executions=executions,
                status=status,
                summary=summary,
            ))
    return items
