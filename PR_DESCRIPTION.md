# PR: Account-aware portfolio rebalancing for the Portfolio Manager

## Motivation

The current Portfolio Manager produces a single 5-tier rating
(Buy/Overweight/Hold/Underweight/Sell) per ticker. This works well for "should
I take this position?" but doesn't address the more common real-world
question: **"I already hold this across multiple accounts — what should I do
in each one?"**

The same rating maps to very different actions depending on the account:
- A `Reduce` in a tax-deferred 401(k) is free; in a taxable brokerage it
  triggers capital gains
- A `Hold` of a deep loser in a taxable account misses tax-loss harvesting
- A `Buy` in a Roth IRA preserves a precious tax-free compounding seat;
  the same Buy in taxable accumulates eventual tax drag

This PR adds an **optional** holdings-context channel so the PM produces
per-account rebalancing actions when the caller supplies positions.

## What this PR does

1. **`tradingagents/portfolio/holdings.py`** — pure-Python data layer.
   Parses Fidelity `Portfolio_Positions_*.csv` exports, classifies accounts
   into Roth / TaxDeferred / Taxable / ChildEdu / Unknown buckets, aggregates
   positions across multiple statements, and renders a markdown context block
   the PM can read.

2. **Schemas** — adds `AccountAction` enum and `AccountActionItem` BaseModel,
   appended as an optional `account_actions` field on `PortfolioDecision`.
   `render_pm_decision` emits a `**Per-Account Actions**` markdown block when
   populated. Existing call sites that don't supply holdings get the same
   output as before.

3. **Portfolio Manager prompt** — when `state["holdings_context"]` is
   non-empty, the prompt appends tax-aware rules:
   - Rebalance in TaxDeferred first (zero current tax cost)
   - Harvest losses in Taxable accounts to offset gains
   - Preserve Roth seats for high-conviction longs (don't sell winners)
   - Respect 529 / education account rebalancing limits
   - Use `SwapToTaxAdvantaged` when low-cost-basis taxable positions can be
     relocated to Roth/TaxDeferred

4. **Plumbing** — optional `holdings_context` parameter on
   `Propagator.create_initial_state(...)`, `TradingAgentsGraph.propagate(...)`,
   and a new `holdings_context` field in `AgentState`.

5. **Driver** — `scripts/analyze_holdings.py` reads CSVs, picks tickers
   above a portfolio-percentage threshold (or a whitelist), runs
   `propagate(ticker, date, holdings_context=...)` serially, and writes
   `REPORT.md` + `summary.csv` + `actions.csv` + `per_ticker/<TICKER>.json`.

6. **Tests** — `tests/test_holdings.py` adds 22 unit tests covering parser,
   account classification, aggregation math, and the context renderer.

## Backward compatibility

Every change is additive:
- New optional field `account_actions` defaults to `None`
- New optional parameter `holdings_context` defaults to `""`
- Existing prompt is unchanged when `holdings_context` is empty
- `render_pm_decision` only emits the per-account block when
  `account_actions` is populated

Existing tests pass unchanged (105 tests, 0 failures).

## Caveats / scope decisions

- **Fidelity CSV format only.** `parse_fidelity_csv` is hard-coded to
  Fidelity's column schema. Other brokers (Schwab, Vanguard, Robinhood)
  would need their own parsers. I considered a generic adapter but
  preferred to ship something that actually works for one broker rather
  than a leaky abstraction.
- **Account classification is rule-based.** Pattern-matching on account
  name strings — works well for typical Fidelity accounts but may need
  extending for unusual labels.
- **`AccountAction.SwapToTaxAdvantaged`** is suggested by the PM but
  execution requires user-side coordination (sell here + buy there in
  near-simultaneous transactions to avoid market exposure gap). The PR
  produces the recommendation; orchestration is out of scope.

## Demo

In a real test run with a 55-ticker / 18-account portfolio, the PM
correctly:
- Reduced AMD across 5 accounts at differentiated percentages (35% in
  TaxDeferred, 25-30% in Taxable depending on cost basis)
- Issued the only Sell (CRWV) and tagged the deepest-loss taxable
  account first for tax-loss harvesting
- Preserved Roth IRA seats for Overweight names (LLY, NVDA) and refused
  to sell Roth winners
- Avoided rebalancing 529/child accounts unless rating was decisive

## Related

Closes — n/a (greenfield feature, no issue).
