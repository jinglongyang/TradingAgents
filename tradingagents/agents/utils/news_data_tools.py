import os
from typing import Annotated, Optional

from langchain_core.tools import tool

from tradingagents.dataflows.interface import route_to_vendor

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[Optional[int], "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[Optional[int], "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)


@tool
def web_search_news(
    query: Annotated[str, "Search query (e.g. 'CRWV Q1 2026 earnings', 'NVDA Nvidia investment')"],
    max_results: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Live web news search via Tavily — covers events that yfinance/alpha_vantage news
    APIs miss (recent 8-K filings, credit rating actions, large strategic
    investments, partnership announcements, late-breaking earnings reactions).
    Use this whenever the structured news tools return stale or sparse results.

    Requires TAVILY_API_KEY in the environment. Returns formatted string with
    title + url + relevant content snippet per result.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return (
            "[web_search_news unavailable: TAVILY_API_KEY not set in env. "
            "Continue analysis with the structured news tools only.]"
        )
    try:
        from langchain_tavily import TavilySearch
    except ImportError:
        return "[web_search_news unavailable: langchain-tavily not installed.]"

    search = TavilySearch(
        max_results=max_results,
        topic="news",
        search_depth="advanced",
    )
    try:
        result = search.invoke({"query": query})
    except Exception as e:  # noqa: BLE001
        return f"[web_search_news error: {e}]"

    items = result.get("results", []) if isinstance(result, dict) else result
    if not items:
        return f"No web results found for query: {query!r}"

    out = [f"Web news for: {query!r}", ""]
    for i, r in enumerate(items[:max_results], 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = (r.get("content") or "")[:600].replace("\n", " ")
        date = r.get("published_date", "")
        out.append(f"{i}. **{title}**" + (f" ({date})" if date else ""))
        if url:
            out.append(f"   {url}")
        if snippet:
            out.append(f"   {snippet}")
        out.append("")
    return "\n".join(out)
