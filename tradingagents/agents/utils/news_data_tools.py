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


def _format_news_results(query: str, items: list[dict], max_results: int) -> str:
    if not items:
        return f"No web results found for query: {query!r}"
    out = [f"Web news for: {query!r}", ""]
    for i, r in enumerate(items[:max_results], 1):
        title = r.get("title", "(no title)")
        url = r.get("url", "")
        snippet = (r.get("snippet") or "")[:600].replace("\n", " ")
        date = r.get("date", "")
        out.append(f"{i}. **{title}**" + (f" ({date})" if date else ""))
        if url:
            out.append(f"   {url}")
        if snippet:
            out.append(f"   {snippet}")
        out.append("")
    return "\n".join(out)


def _search_brave(api_key: str, query: str, max_results: int) -> dict:
    """Returns {ok: bool, items: list, error: str}. Items normalized to
    {title, url, snippet, date}."""
    import requests
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/news/search",
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            params={
                "q": query,
                "count": max(1, min(int(max_results), 20)),
                "freshness": "pw",
                "country": "us",
                "safesearch": "off",
            },
            timeout=15,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "items": [], "error": f"network: {e}"}
    if resp.status_code != 200:
        return {"ok": False, "items": [], "error": f"HTTP {resp.status_code} {resp.text[:160]}"}
    try:
        raw = resp.json().get("results", [])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "items": [], "error": f"parse: {e}"}
    items = [
        {
            "title": r.get("title", "(no title)"),
            "url": r.get("url", ""),
            "snippet": r.get("description") or "",
            "date": r.get("age") or r.get("page_age") or "",
        }
        for r in raw
    ]
    return {"ok": True, "items": items, "error": ""}


def _search_tavily(api_key: str, query: str, max_results: int) -> dict:
    try:
        from langchain_tavily import TavilySearch
    except ImportError:
        return {"ok": False, "items": [], "error": "langchain-tavily not installed"}
    try:
        search = TavilySearch(
            max_results=max_results,
            topic="news",
            search_depth="advanced",
            tavily_api_key=api_key,
        )
        result = search.invoke({"query": query})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "items": [], "error": str(e)[:200]}
    raw = result.get("results", []) if isinstance(result, dict) else result
    items = [
        {
            "title": r.get("title", "(no title)"),
            "url": r.get("url", ""),
            "snippet": r.get("content") or "",
            "date": r.get("published_date") or "",
        }
        for r in (raw or [])
    ]
    return {"ok": True, "items": items, "error": ""}


@tool
def web_search_news(
    query: Annotated[str, "Search query (e.g. 'CRWV Q1 2026 earnings', 'NVDA Nvidia investment')"],
    max_results: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """
    Live web news search — covers events that yfinance/alpha_vantage news APIs
    miss (recent 8-K filings, credit rating actions, large strategic
    investments, partnership announcements, late-breaking earnings reactions).
    Use this whenever the structured news tools return stale or sparse results.

    Tries Brave Search API first, falls back to Tavily on rate-limit / error.
    Set either BRAVE_API_KEY or TAVILY_API_KEY (or both — recommended) in env.
    """
    providers = []
    brave_key = os.environ.get("BRAVE_API_KEY")
    if brave_key:
        providers.append(("Brave", brave_key, _search_brave))
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        providers.append(("Tavily", tavily_key, _search_tavily))

    if not providers:
        return (
            "[web_search_news unavailable: neither BRAVE_API_KEY nor TAVILY_API_KEY "
            "set in env. Continue analysis with the structured news tools only.]"
        )

    errors: list[str] = []
    for name, key, fn in providers:
        result = fn(key, query, max_results)
        if result["ok"] and result["items"]:
            return _format_news_results(query, result["items"], max_results)
        if result["ok"]:
            errors.append(f"{name}: no results")
        else:
            errors.append(f"{name}: {result['error']}")

    return f"[web_search_news exhausted: {'; '.join(errors)}]"


@tool
def get_sec_filings(
    ticker: Annotated[str, "Ticker symbol"],
    forms: Annotated[str, "Comma-separated form types, e.g. '8-K' or '8-K,10-Q,13D'"] = "8-K",
    days_back: Annotated[int, "How many days back to look"] = 90,
) -> str:
    """
    Fetch recent SEC filings directly from EDGAR. This is the AUTHORITATIVE
    source for material events — same-day disclosure, beats every news API.

    Use for:
    - 8-K: large investments, executive changes, credit rating reactions,
      partnership agreements, debt issuances, material impairments
    - 10-Q / 10-K: quarterly / annual financials
    - 13D / 13G: large beneficial owner filings (>5% holders)

    Returns a markdown table of filings with date, form, item descriptions
    (in plain English for 8-K), and direct SEC URLs.
    """
    from tradingagents.dataflows.sec_edgar import (
        describe_8k_items,
        get_recent_filings,
    )

    form_list = [f.strip().upper() for f in forms.split(",") if f.strip()]
    try:
        filings = get_recent_filings(ticker, forms=form_list, days_back=days_back)
    except Exception as e:  # noqa: BLE001
        return f"[get_sec_filings error: {e}]"
    if not filings:
        return f"No {','.join(form_list)} filings for {ticker} in the past {days_back} days."

    lines = [
        f"## SEC Filings for {ticker} (last {days_back} days)",
        "",
        "| Date | Form | Items / Description | Link |",
        "|---|---|---|---|",
    ]
    for f in filings:
        desc = describe_8k_items(f["items"]) if f["form"] == "8-K" else (f["items"] or "—")
        lines.append(f"| {f['date']} | {f['form']} | {desc} | [view]({f['url']}) |")
    return "\n".join(lines)


@tool
def get_institutional_holders(
    ticker: Annotated[str, "Ticker symbol"],
) -> str:
    """
    Top institutional holders aggregated from 13F-HR filings (via yfinance).
    Shows shares, value, % of float, and quarter-over-quarter % change.

    Use this to gauge "smart money" conviction: large QoQ increases by
    well-known funds (Renaissance, Tiger Global, Berkshire, Bridgewater)
    are bullish signals; broad reductions are bearish. Note 13F data is
    45 days delayed, so it is structural context, not real-time signal.
    """
    from tradingagents.dataflows.sec_edgar import (
        get_institutional_holders_via_yfinance,
    )
    return get_institutional_holders_via_yfinance(ticker)
