"""Holdings parsing, aggregation, and Portfolio Manager context builder."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable


class AccountType(str, Enum):
    """Tax-treatment buckets that drive Portfolio Manager decisions.

    The classification controls whether the PM should suggest rebalancing in
    that account: tax-deferred accounts have zero rebalancing tax cost,
    Roth accounts trade tax-free but their seats are precious, taxable
    accounts must weigh capital-gains tax, and child/education accounts have
    legal restrictions and long horizons.
    """

    ROTH = "Roth"
    TAX_DEFERRED = "TaxDeferred"
    TAXABLE = "Taxable"
    CHILD_EDU = "ChildEdu"
    UNKNOWN = "Unknown"


CASH_TICKERS = frozenset(
    {"SPAXX", "SPAXX**", "FDRXX", "FDRXX**", "FRGXX", "FRGXX**", ""}
)
MUTUAL_FUND_PREFIXES = ("FBCGX", "NHFSMKX")


@dataclass(frozen=True)
class Position:
    """A single broker-statement row, post-parsing."""

    account_id: str
    account_name: str
    account_type: AccountType
    symbol: str
    quantity: float
    last_price: float
    current_value: float
    cost_basis_total: float
    avg_cost: float


@dataclass(frozen=True)
class Holding:
    """All positions of one ticker rolled up across accounts."""

    symbol: str
    total_value: float
    total_cost: float
    total_quantity: float
    positions: tuple[Position, ...] = field(default_factory=tuple)

    @property
    def unrealized_pl(self) -> float:
        return self.total_value - self.total_cost if self.total_cost else 0.0

    @property
    def unrealized_pl_pct(self) -> float:
        return (self.unrealized_pl / self.total_cost * 100) if self.total_cost else 0.0


def classify_account(account_name: str) -> AccountType:
    """Map a Fidelity / broker account label to a tax bucket.

    Rules are pattern-based and conservative: anything we cannot identify
    falls into ``UNKNOWN`` so the Portfolio Manager treats it as taxable
    (the safest default — assume rebalancing has tax cost).
    """
    if not account_name:
        return AccountType.UNKNOWN
    name = account_name.lower()

    if "529" in name or "olivia" in name or "amelia" in name:
        return AccountType.CHILD_EDU
    if "roth" in name:
        return AccountType.ROTH
    if (
        "401(k)" in name
        or "401k" in name
        or "ira" in name
        or "brokeragelink" in name
    ):
        return AccountType.TAX_DEFERRED
    if (
        "stocks" in name
        or "money market" in name
        or "brokerage" == name.strip()
        or name.strip().startswith("brokerage ")
        or "cma" in name
        or "joint" in name
        or "tod" in name
    ):
        return AccountType.TAXABLE
    return AccountType.UNKNOWN


_NUMBER_RE = re.compile(r"[+\-]?\$?[\d,]+\.?\d*")


def _parse_money(s: str | None) -> float:
    """Parse Fidelity money/quantity strings.

    Returns 0.0 for the vendor's empty markers (``--``, blank, ``None``).
    Handles ``$1,234.56``, ``+$1,234.56``, ``-$1,234.56``, ``1.234%`` (the
    percent sign is stripped without rescaling — caller knows the unit).
    """
    if not s:
        return 0.0
    s = s.strip()
    if s in {"--", ""}:
        return 0.0
    s = s.replace("$", "").replace(",", "").replace("+", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_fidelity_csv(path: str | Path) -> list[Position]:
    """Read a Fidelity ``Portfolio_Positions_*.csv`` export.

    Filters out cash holdings, mutual funds we cannot price via yfinance,
    and ``Pending activity`` rows. Each surviving broker row becomes one
    ``Position``.
    """
    positions: list[Position] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = (row.get("Symbol") or "").strip()
            if not symbol or symbol in CASH_TICKERS or symbol == "Pending activity":
                continue
            if symbol.startswith("**"):
                continue
            if any(symbol.startswith(p) for p in MUTUAL_FUND_PREFIXES):
                continue

            account_name = (row.get("Account Name") or "").strip()
            positions.append(
                Position(
                    account_id=(row.get("Account Number") or "").strip(),
                    account_name=account_name,
                    account_type=classify_account(account_name),
                    symbol=symbol,
                    quantity=_parse_money(row.get("Quantity")),
                    last_price=_parse_money(row.get("Last Price")),
                    current_value=_parse_money(row.get("Current Value")),
                    cost_basis_total=_parse_money(row.get("Cost Basis Total")),
                    avg_cost=_parse_money(row.get("Average Cost Basis")),
                )
            )
    return positions


def aggregate_by_ticker(positions: Iterable[Position]) -> dict[str, Holding]:
    """Roll positions up by ticker, preserving per-account detail."""
    buckets: dict[str, list[Position]] = {}
    for p in positions:
        buckets.setdefault(p.symbol, []).append(p)

    holdings: dict[str, Holding] = {}
    for symbol, items in buckets.items():
        holdings[symbol] = Holding(
            symbol=symbol,
            total_value=sum(p.current_value for p in items),
            total_cost=sum(p.cost_basis_total for p in items),
            total_quantity=sum(p.quantity for p in items),
            positions=tuple(items),
        )
    return holdings


_ACCOUNT_TYPE_HINTS = {
    AccountType.ROTH: "tax-free growth, withdrawals untaxed; rebalancing has zero tax cost but seats are precious — keep highest-conviction long-term compounders here",
    AccountType.TAX_DEFERRED: "rebalancing has zero current tax cost; ordinary income tax on withdrawal — main rebalancing battleground",
    AccountType.TAXABLE: "selling triggers capital-gains tax (short-term = ordinary rate if held <1y, long-term lower); losses can be harvested to offset gains",
    AccountType.CHILD_EDU: "education-restricted (529) or child custodial; long horizon but glide-path down before college; limited rebalancing frequency",
    AccountType.UNKNOWN: "treat as taxable by default (conservative)",
}


def build_holdings_context(
    ticker: str,
    holding: Holding,
    portfolio_total_value: float,
) -> str:
    """Render the per-account context block injected into the PM prompt.

    Returns a markdown fragment the Portfolio Manager reads alongside the
    risk debate. Empty string when the ticker is not held — caller should
    skip injection in that case so the PM behaves as before.
    """
    if not holding.positions:
        return ""

    portfolio_pct = (
        holding.total_value / portfolio_total_value * 100
        if portfolio_total_value
        else 0.0
    )

    pl_sign = "+" if holding.unrealized_pl >= 0 else "-"
    pl_money = f"{pl_sign}${abs(holding.unrealized_pl):,.2f}"

    lines = [
        "**Current Holdings of this Ticker (across all accounts):**",
        f"- Total quantity: {holding.total_quantity:,.3f} shares",
        f"- Total market value: ${holding.total_value:,.2f}",
        f"- Total cost basis: ${holding.total_cost:,.2f}",
        f"- Unrealized P/L: {pl_money} ({holding.unrealized_pl_pct:+.1f}%)",
        f"- Share of total portfolio: {portfolio_pct:.2f}%",
        "",
        "**Per-Account Breakdown:**",
    ]
    for p in sorted(holding.positions, key=lambda x: -x.current_value):
        pl = p.current_value - p.cost_basis_total if p.cost_basis_total else 0.0
        pl_pct = (pl / p.cost_basis_total * 100) if p.cost_basis_total else 0.0
        lines.append(
            f"- **{p.account_name}** "
            f"[{p.account_type.value}]: "
            f"{p.quantity:,.3f} shares · "
            f"value ${p.current_value:,.2f} · "
            f"cost ${p.cost_basis_total:,.2f} · "
            f"P/L {pl_pct:+.1f}%"
        )

    lines.extend(
        [
            "",
            "**Account-Type Decision Hints:**",
        ]
    )
    types_present = {p.account_type for p in holding.positions}
    for t in types_present:
        lines.append(f"- *{t.value}*: {_ACCOUNT_TYPE_HINTS[t]}")

    return "\n".join(lines)
