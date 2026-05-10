"""Portfolio aggregation and account-aware decision context.

Pure data layer used by ``scripts/analyze_holdings.py`` and the Portfolio
Manager prompt. No LLM calls, no I/O beyond reading user-supplied CSV.
"""

from tradingagents.portfolio.holdings import (
    AccountType,
    Holding,
    Position,
    aggregate_by_ticker,
    build_holdings_context,
    classify_account,
    parse_fidelity_csv,
)

__all__ = [
    "AccountType",
    "Holding",
    "Position",
    "aggregate_by_ticker",
    "build_holdings_context",
    "classify_account",
    "parse_fidelity_csv",
]
