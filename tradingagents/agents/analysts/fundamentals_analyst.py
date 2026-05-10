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
            "You are a researcher tasked with analyzing fundamental information about a company. Write a comprehensive report covering financial statements, company profile, capital structure, and institutional ownership."
            " MANDATORY TOOL SEQUENCE:"
            " (1) FIRST call get_sec_filings(ticker, forms=\"10-Q,8-K\", days_back=120) to pull the most recent quarterly report and any material-event filings."
            " (2) SECOND call get_institutional_holders(ticker) to see which funds own the stock, their position sizes, and quarter-over-quarter changes — this is the smart-money signal."
            " (3) THIRD call get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement for the structured financial picture."
            " (4) FOURTH call web_search_news(query=\"<TICKER> strategic investment partnership financing rating <YEAR>\") to verify any recent events the structured statements have not yet reflected (term loans, equity injections, rating actions)."
            " When the structured statements show concerning trends (high leverage, negative FCF), web_search and SEC filings often reveal mitigating events. Conversely, strong-looking statements may have material risks disclosed in recent 8-K filings."
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
