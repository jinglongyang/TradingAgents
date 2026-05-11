"""Tax estimation for proposed sells.

Federal capital gains rates are approximate — for actual tax filing,
consult a CPA. These estimates are decision-support tools, not legal
advice.

Assumptions:
- User in highest marginal bracket: 37% ordinary, 20% LTCG, +3.8% NIIT
  for AGI > $250K (married filing jointly).
- Holding period > 365 days → long-term; ≤ 365 → short-term.
- Washington state = no state income tax. Adjust for other states.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


# Conservative defaults — caller can override per analysis
DEFAULT_BRACKET = "high"  # high | mid | low

TAX_RATES = {
    "high": {  # AGI > $250K MFJ, $200K single
        "short_term": 0.37,
        "long_term": 0.20,
        "niit": 0.038,
    },
    "mid": {  # $100K-$250K
        "short_term": 0.24,
        "long_term": 0.15,
        "niit": 0.038,
    },
    "low": {
        "short_term": 0.12,
        "long_term": 0.0,
        "niit": 0.0,
    },
}


@dataclass(frozen=True)
class TaxEstimate:
    """Per-sell tax impact estimate."""

    symbol: str
    account_name: str
    account_type: str
    shares: float
    sell_price: float
    cost_basis_per_share: float
    proceeds: float
    cost_basis_total: float
    realized_gain: float       # positive = gain, negative = loss
    is_long_term: bool
    federal_tax: float          # estimated federal tax (or refund if loss)
    niit_tax: float             # NIIT 3.8% surtax
    total_tax: float
    net_proceeds: float         # cash after tax


def estimate_sell_tax(
    symbol: str,
    account_name: str,
    account_type: str,
    shares: float,
    sell_price: float,
    cost_basis_per_share: float,
    holding_days: int | None = None,
    purchase_date: str | None = None,
    bracket: str = DEFAULT_BRACKET,
) -> TaxEstimate:
    """Estimate tax impact of selling ``shares`` of ``symbol``.

    Tax-advantaged accounts (Roth, TaxDeferred, ChildEdu) return zero
    federal/NIIT tax — the realized gain still shows but doesn't cost
    anything this year.
    """
    proceeds = shares * sell_price
    cost = shares * cost_basis_per_share
    gain = proceeds - cost

    # Determine holding period
    if holding_days is None and purchase_date:
        try:
            d = datetime.strptime(purchase_date, "%Y-%m-%d").date()
            holding_days = (date.today() - d).days
        except Exception:
            holding_days = 0
    is_long_term = (holding_days or 0) > 365

    # Tax-advantaged accounts pay nothing now
    if account_type in ("Roth", "TaxDeferred", "ChildEdu"):
        return TaxEstimate(
            symbol=symbol, account_name=account_name, account_type=account_type,
            shares=shares, sell_price=sell_price,
            cost_basis_per_share=cost_basis_per_share, proceeds=proceeds,
            cost_basis_total=cost, realized_gain=gain, is_long_term=is_long_term,
            federal_tax=0.0, niit_tax=0.0, total_tax=0.0, net_proceeds=proceeds,
        )

    rates = TAX_RATES.get(bracket, TAX_RATES["high"])
    if gain >= 0:
        fed_rate = rates["long_term"] if is_long_term else rates["short_term"]
        federal_tax = gain * fed_rate
        niit_tax = gain * rates["niit"] if is_long_term else 0.0  # NIIT applies to LTCG, not ordinary
        # NIIT also applies to short-term cap gains technically — both are "investment income"
        if not is_long_term:
            niit_tax = gain * rates["niit"]
    else:
        # Loss — federal value is the TLH offset (negative tax = refund)
        loss_rate = rates["long_term"] if is_long_term else rates["short_term"]
        federal_tax = gain * loss_rate  # negative
        niit_tax = 0.0  # losses don't reduce NIIT directly

    total_tax = federal_tax + niit_tax
    net_proceeds = proceeds - total_tax

    return TaxEstimate(
        symbol=symbol, account_name=account_name, account_type=account_type,
        shares=shares, sell_price=sell_price,
        cost_basis_per_share=cost_basis_per_share, proceeds=proceeds,
        cost_basis_total=cost, realized_gain=gain, is_long_term=is_long_term,
        federal_tax=federal_tax, niit_tax=niit_tax, total_tax=total_tax,
        net_proceeds=net_proceeds,
    )


def render_tax_estimate(est: TaxEstimate) -> str:
    """One-line summary for display."""
    period = "LT" if est.is_long_term else "ST"
    if est.realized_gain >= 0:
        return (
            f"{est.symbol} {est.shares:.2f}@${est.sell_price:.2f} ({est.account_type}) "
            f"→ gain ${est.realized_gain:+,.0f} [{period}] · "
            f"tax ${est.total_tax:,.0f} · net ${est.net_proceeds:,.0f}"
        )
    else:
        return (
            f"{est.symbol} {est.shares:.2f}@${est.sell_price:.2f} ({est.account_type}) "
            f"→ TLH loss ${est.realized_gain:,.0f} [{period}] · "
            f"tax saving ${-est.federal_tax:,.0f}"
        )
