"""Portfolio Manager: synthesises the risk-analyst debate into the final decision.

Uses LangChain's ``with_structured_output`` so the LLM produces a typed
``PortfolioDecision`` directly, in a single call.  The result is rendered
back to markdown for storage in ``final_trade_decision`` so memory log,
CLI display, and saved reports continue to consume the same shape they do
today.  When a provider does not expose structured output, the agent falls
back gracefully to free-text generation.
"""

from __future__ import annotations

import os

from tradingagents.agents.schemas import PortfolioDecision, render_pm_decision
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
)
from tradingagents.agents.utils.structured import (
    bind_structured,
    invoke_structured_or_freetext,
)


def create_portfolio_manager(llm):
    structured_llm = bind_structured(llm, PortfolioDecision, "Portfolio Manager")

    def portfolio_manager_node(state) -> dict:
        instrument_context = build_instrument_context(state["company_of_interest"])

        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]
        research_plan = state["investment_plan"]
        trader_plan = state["trader_investment_plan"]

        past_context = state.get("past_context", "")
        lessons_line = (
            f"- Lessons from prior decisions and outcomes:\n{past_context}\n"
            if past_context
            else ""
        )

        holdings_context = state.get("holdings_context", "")
        user_tax_context = os.environ.get("USER_TAX_CONTEXT", "").strip()
        if holdings_context:
            tax_user_block = (
                f"\n**User-Specific Tax Context (must factor into account_actions):**\n{user_tax_context}\n"
                if user_tax_context
                else ""
            )
            holdings_block = (
                f"\n---\n\n{holdings_context}\n{tax_user_block}\n"
                "Because the user already holds this position across multiple accounts, "
                "you MUST also populate `account_actions` with one entry per account "
                "listed above. Use these tax-aware rules:\n"
                "1. Prefer rebalancing in **TaxDeferred** accounts (zero tax cost).\n"
                "2. In **Taxable** accounts: trim heavy winners only when the rating "
                "is Underweight/Sell, and prefer harvesting losses for tax savings.\n"
                "3. In **Roth** accounts: avoid selling winners (lose precious tax-free "
                "compounding seat); use these for Buy/Overweight conviction holds.\n"
                "4. Use **SwapToTaxAdvantaged** when a low-cost-basis position sits in "
                "Taxable but Roth/TaxDeferred has cash to buy the same exposure.\n"
                "5. **ChildEdu** accounts: only adjust when the rating is decisive and "
                "the long horizon supports it; respect 529 rebalancing limits.\n"
                "6. When the User-Specific Tax Context flags imminent material life events "
                "(home purchase, refinancing, large planned expense), DEFER large taxable "
                "gain realizations to a later year — only proceed with TLH (loss harvests) "
                "and tax-deferred account rebalancing this year. Note this in each affected "
                "rationale so the user can confirm timing.\n"
                "Per-account actions must collectively be consistent with the top-level "
                "rating you choose.\n"
            )
        else:
            holdings_block = ""

        prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate and deliver the final trading decision.

{instrument_context}

---

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Required Price Levels** (any non-Hold rating MUST include all three):
- **price_target**: 12-month target price (upside for Buy/Overweight; the fair-value level that would trigger a re-rating for Underweight/Sell)
- **entry_zone**: specific price range to execute (e.g. "$420-435 on dips" for Buy, "$510-525 on bounces" for Sell). Anchor to support/resistance, moving averages, or recent ranges from the technical analyst's report.
- **stop_loss**: only for Buy/Overweight — the level below which the bullish thesis is invalidated. Use the 200-day SMA, recent swing low, or a defined percentage drawdown.

For Hold, all three may be null but you should still mention the level at which your stance would change in the executive_summary.

**Language Discipline** (mandatory — keeps responses through enterprise content filters):
Use sober, analytical financial language. Treat your output as a sell-side
research note, not commentary. Specifically:
- Replace "bankruptcy / collapse / crisis / catastrophic" with "financial
  weakness / capital constraints / liquidity pressure / material downside risk"
- Replace "death spiral / falling knife / crash" with "extended drawdown /
  multi-quarter underperformance / sharp price decline"
- Replace "burning cash / hemorrhaging" with "negative free cash flow /
  high cash burn rate"
- Replace "doomed / fatal / dying" with "structural challenges / impaired
  business model / deteriorating fundamentals"
- Replace "destroy / wipe out / annihilate" with "compress / reduce / impair"
Keep all the substance — flag every real risk — but use neutral, professional
phrasing. Do not soften the conclusion (Sell is still Sell); only refine the
words used to describe risks.

**Context:**
- Research Manager's investment plan: **{research_plan}**
- Trader's transaction proposal: **{trader_plan}**
{lessons_line}
**Risk Analysts Debate History:**
{history}
{holdings_block}
---

Be decisive and ground every conclusion in specific evidence from the analysts.{get_language_instruction()}"""

        final_trade_decision = invoke_structured_or_freetext(
            structured_llm,
            llm,
            prompt,
            render_pm_decision,
            "Portfolio Manager",
        )

        new_risk_debate_state = {
            "judge_decision": final_trade_decision,
            "history": risk_debate_state["history"],
            "aggressive_history": risk_debate_state["aggressive_history"],
            "conservative_history": risk_debate_state["conservative_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_aggressive_response": risk_debate_state["current_aggressive_response"],
            "current_conservative_response": risk_debate_state["current_conservative_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_risk_debate_state,
            "final_trade_decision": final_trade_decision,
        }

    return portfolio_manager_node
