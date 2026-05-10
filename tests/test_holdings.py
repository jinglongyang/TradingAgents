"""Tests for the holdings parser, account classification, and PM context builder."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tradingagents.portfolio.holdings import (
    AccountType,
    Holding,
    Position,
    aggregate_by_ticker,
    build_holdings_context,
    classify_account,
    parse_fidelity_csv,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Roth IRA", AccountType.ROTH),
        ("BrokerageLink Roth", AccountType.ROTH),
        ("Traditional IRA", AccountType.TAX_DEFERRED),
        ("UIPATH 401(K)", AccountType.TAX_DEFERRED),
        ("AMAZON 401(K) PLAN", AccountType.TAX_DEFERRED),
        ("BrokerageLink-UP", AccountType.TAX_DEFERRED),
        ("BrokerageLink-SF", AccountType.TAX_DEFERRED),
        ("Jinglong Stocks", AccountType.TAXABLE),
        ("Joint money market", AccountType.TAXABLE),
        ("Brokerage", AccountType.TAXABLE),
        ("CMA-Edge", AccountType.TAXABLE),
        ("Joint WROS - TOD", AccountType.TAXABLE),
        ("Olivia- 529", AccountType.CHILD_EDU),
        ("Amelia- 529", AccountType.CHILD_EDU),
        ("Olivia", AccountType.CHILD_EDU),
        ("Amelia", AccountType.CHILD_EDU),
        ("", AccountType.UNKNOWN),
        ("Strange unknown account", AccountType.UNKNOWN),
    ],
)
def test_classify_account(name, expected):
    assert classify_account(name) == expected


@pytest.mark.unit
def test_parse_fidelity_csv_filters_cash_and_funds(tmp_path: Path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        textwrap.dedent(
            """\
            Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
            A1,Roth IRA,SPAXX**,MONEY MKT,,,,$1.00,,,,,0.00%,,,Cash,
            A1,Roth IRA,FBCGX,FID BLUE CHIP,990,$49.37,,$48890.07,,,,,100%,$28236,$28.51,,
            A1,Roth IRA,Pending activity,,,,,$100,,,,,,,,,
            A1,Roth IRA,AAPL,APPLE INC,10,$293.05,+$5.88,$2930.50,+$58.80,+2.04%,+$930.50,+46.5%,1.5%,$2000.00,$200.00,Margin,
            A2,Brokerage,AAPL,APPLE INC,5,$293.05,+$5.88,$1465.25,+$29.40,+2.04%,+$465.25,+46.5%,2%,$1000.00,$200.00,Cash,
            """
        ),
        encoding="utf-8",
    )
    positions = parse_fidelity_csv(csv)
    assert len(positions) == 2
    assert {p.symbol for p in positions} == {"AAPL"}
    assert {p.account_type for p in positions} == {AccountType.ROTH, AccountType.TAXABLE}


@pytest.mark.unit
def test_aggregate_by_ticker_sums_correctly():
    positions = [
        Position("A1", "Roth IRA", AccountType.ROTH, "AAPL", 10, 293.05, 2930.50, 2000.0, 200.0),
        Position("A2", "Brokerage", AccountType.TAXABLE, "AAPL", 5, 293.05, 1465.25, 1000.0, 200.0),
        Position("A3", "Brokerage", AccountType.TAXABLE, "GOOGL", 1, 400.80, 400.80, 100.0, 100.0),
    ]
    holdings = aggregate_by_ticker(positions)
    assert set(holdings) == {"AAPL", "GOOGL"}
    aapl = holdings["AAPL"]
    assert aapl.total_quantity == pytest.approx(15)
    assert aapl.total_value == pytest.approx(4395.75)
    assert aapl.total_cost == pytest.approx(3000.0)
    assert aapl.unrealized_pl == pytest.approx(1395.75)
    assert aapl.unrealized_pl_pct == pytest.approx(46.525)
    assert len(aapl.positions) == 2


@pytest.mark.unit
def test_build_holdings_context_includes_account_breakdown():
    positions = [
        Position("A1", "Roth IRA", AccountType.ROTH, "AAPL", 10, 293.05, 2930.50, 2000.0, 200.0),
        Position("A2", "Brokerage", AccountType.TAXABLE, "AAPL", 5, 293.05, 1465.25, 1000.0, 200.0),
    ]
    holdings = aggregate_by_ticker(positions)
    ctx = build_holdings_context("AAPL", holdings["AAPL"], portfolio_total_value=100000.0)

    assert "Current Holdings of this Ticker" in ctx
    assert "Roth IRA" in ctx
    assert "Brokerage" in ctx
    assert "Roth" in ctx and "Taxable" in ctx
    assert "Account-Type Decision Hints" in ctx
    assert "$4,395.75" in ctx
    assert "+$1,395.75" in ctx
    # unrealized P/L %
    assert "+46.5%" in ctx or "+46.6%" in ctx


@pytest.mark.unit
def test_build_holdings_context_empty_when_no_positions():
    holding = Holding(symbol="ZZZZ", total_value=0, total_cost=0, total_quantity=0, positions=())
    assert build_holdings_context("ZZZZ", holding, portfolio_total_value=100.0) == ""
