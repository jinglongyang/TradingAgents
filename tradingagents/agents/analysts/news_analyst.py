from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_global_news,
    get_language_instruction,
    get_news,
    get_sec_filings,
    web_search_news,
)
from tradingagents.dataflows.config import get_config


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        tools = [
            get_news,
            get_global_news,
            web_search_news,
            get_sec_filings,
        ]

        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends. Write a comprehensive report on the state of the world relevant to this ticker and to macroeconomics."
            " MANDATORY TOOL SEQUENCE — execute in this order, do not skip:"
            " (1) FIRST call get_sec_filings(ticker, forms=\"8-K\", days_back=90) to retrieve the authoritative material-event disclosures the company has filed directly with the SEC."
            " Item descriptions (e.g. 'Material Definitive Agreement', 'Results of Operations', 'Departure of Officers') tell you exactly what happened. The URLs link to the filings."
            " (2) SECOND call web_search_news(query=\"<TICKER> latest earnings <YEAR>\") for the market reaction and analyst commentary on those events."
            " (3) THIRD call web_search_news(query=\"<TICKER> strategic investment partnership financing rating <YEAR>\") to specifically surface large equity investments,"
            " term loan facilities, credit rating actions (S&P, Moody's, Fitch upgrades/downgrades), and partnership announcements."
            " (4) ONLY AFTER the above, call get_news / get_global_news for additional structured context."
            " Anchor every factual claim — every dollar figure, every date, every rating action — to a tool call result."
            " Do NOT fabricate numbers or events. If web_search or SEC returns nothing on a topic, say so explicitly."
            " Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
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
            "news_report": report,
        }

    return news_analyst_node
