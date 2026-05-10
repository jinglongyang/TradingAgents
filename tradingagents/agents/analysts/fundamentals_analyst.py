from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_institutional_holders,
    get_language_instruction,
    get_sec_filings,
    web_search_news,
)
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            web_search_news,
            get_sec_filings,
            get_institutional_holders,
        ]

        system_message = (
            "You are a researcher tasked with analyzing fundamental information about a company. Write a comprehensive report covering financial statements, capital structure, institutional ownership, and insider activity."
            " MANDATORY TOOL SEQUENCE (execute in order, do not skip):"
            " (1) get_sec_filings(ticker, forms=\"10-Q,8-K\", days_back=120) — most recent quarterly report and material-event filings."
            " (2) get_sec_filings(ticker, forms=\"4,SC 13D,SC 13G\", days_back=180) — insider trading and large beneficial owner disclosures."
            "     Form 4 = CEO/CFO/director buy/sell; SC 13D = activist 5%+ stake; SC 13G = passive 5%+ stake."
            " (3) get_sec_filings(ticker, forms=\"NT 10-K,NT 10-Q,S-1,S-3,424B5\", days_back=365) — delayed-filing red flags and dilutive equity issuance."
            "     Any NT-* hit = company asked SEC for filing extension; major red flag. S-* / 424B = new share offering, dilution risk."
            " (4) get_institutional_holders(ticker) — top-10 holders with QoQ changes. Look for split signals (one big holder cutting, another doubling)."
            " (5) get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement for the structured financial picture."
            " (6) web_search_news(query=\"<TICKER> strategic investment partnership financing rating <YEAR>\") to verify any recent events the structured tools missed."
            " Interpret findings TOGETHER, not in isolation:"
            " - High leverage + negative FCF in statements, BUT recent 8-K showing investment-grade term loan = far less concerning."
            " - Insider buying in Form 4 + activist 13D = bullish convergence."
            " - Insider selling + S-1 dilution + NT-10-Q delay = bearish convergence."
            " - Split institutional view (top holder cutting, others adding) = uncertainty, not directional signal."
            " Anchor every factual claim to a tool call result. Do NOT fabricate dollar figures or dates."
            " Make sure to append a Markdown table at the end of the report to organize key points."
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
