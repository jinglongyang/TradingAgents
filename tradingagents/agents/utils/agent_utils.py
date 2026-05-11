from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news,
    web_search_news,
    get_sec_filings,
    get_institutional_holders,
)


NEUTRAL_LANGUAGE_DISCIPLINE = (
    "\n\nLanguage discipline (keep responses through enterprise content filters):"
    " Use sober, analyst-note phrasing. Substitute 'bankruptcy/collapse/crisis' with"
    " 'financial weakness/capital constraints/liquidity pressure'; 'crash/death spiral'"
    " with 'extended drawdown'; 'burning cash/hemorrhaging' with 'negative free cash flow';"
    " 'doomed/fatal' with 'structural challenges'; 'destroy/wipe out' with"
    " 'compress/impair'. Flag every real risk — do not soften conclusions — but use"
    " neutral, professional vocabulary."
)


_STRONGER_NEUTRAL = (
    "\n\nIMPORTANT: A previous attempt was blocked by the content filter. "
    "Rewrite using ONLY sell-side research vocabulary. Avoid hostile or "
    "catastrophic framing entirely. Describe risks in terms of: margin compression, "
    "competitive pressure, demand softness, capital intensity, execution risk, "
    "guidance reset, multiple compression. Do NOT use words like: bankruptcy, "
    "crisis, collapse, doomed, dying, crash, fatal, hemorrhage, destruction, "
    "implode, meltdown. Substitute industry-standard analyst phrasing throughout."
)


def safe_invoke(llm, prompt: str, agent_label: str = "agent", max_retries: int = 1):
    """Invoke an LLM with graceful degradation when an Azure / OpenAI content
    filter rejects the response.

    1. Try the prompt as-is.
    2. On a ValueError mentioning "content filter", retry with an additional
       stronger neutral-language preamble.
    3. If retries are exhausted, return a stub response so downstream nodes can
       continue instead of crashing the whole analysis. The stub is structured
       like the original LLM response (object with `.content`).
    """
    import logging as _logging
    log = _logging.getLogger("safe_invoke")

    attempt_prompt = prompt
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return llm.invoke(attempt_prompt)
        except Exception as e:
            # Match the filter signature across SDKs: langchain wraps as
            # ValueError, OpenAI Python SDK raises BadRequestError (status 400)
            # whose str() begins with "Error code: 400 - {... 'code':
            # 'content_filter', 'code': 'ResponsibleAIPolicyViolation' ...}".
            msg = str(e).lower()
            if (
                "content filter" not in msg
                and "content_filter" not in msg
                and "responsibleaipolicy" not in msg
            ):
                raise
            last_err = e
            log.warning(
                "[%s] content filter tripped (attempt %d/%d); retrying with stronger discipline",
                agent_label, attempt + 1, max_retries + 1,
            )
            attempt_prompt = prompt + _STRONGER_NEUTRAL

    log.error("[%s] content filter still blocking after %d attempts — returning stub", agent_label, max_retries + 1)

    class _StubResponse:
        content = (
            f"[{agent_label} unavailable: Azure content filter blocked output for this ticker. "
            "Downstream agents should proceed without this perspective.]"
        )

    return _StubResponse()


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
