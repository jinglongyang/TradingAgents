"""Minimal SEC EDGAR client for filings retrieval.

SEC EDGAR is the authoritative source for public-company disclosures.
This module focuses on the two filings that materially change a thesis
the moment they are filed:

- **8-K**: material events (large investments, executive changes, credit
  rating actions, partnership agreements, debt issuances). Public the
  same day the event happens — beats every news API.
- **13F-HR**: institutional manager quarterly position disclosure. 45-day
  filing lag, so it's structural rather than real-time, but the only
  authoritative source for "which funds own this stock and at what size".

SEC requires a polite User-Agent header naming a real contact. Without it
they will rate-limit aggressively.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Iterable
from urllib.parse import quote_plus

import requests


# SEC asks for a contact-name + email user-agent; project-name fallback.
_UA = os.environ.get(
    "SEC_EDGAR_USER_AGENT",
    "TradingAgents research/1.0 contact@example.com",
)
_HEADERS = {"User-Agent": _UA, "Accept": "application/json"}
_BASE = "https://data.sec.gov"
_EFTS = "https://efts.sec.gov/LATEST/search-index"


_TICKER_CIK_CACHE: dict[str, int] = {}


def _load_ticker_cik_map() -> dict[str, int]:
    """Fetch SEC's ticker→CIK map (cached after first call)."""
    if _TICKER_CIK_CACHE:
        return _TICKER_CIK_CACHE
    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers=_HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    for entry in r.json().values():
        _TICKER_CIK_CACHE[entry["ticker"].upper()] = int(entry["cik_str"])
    return _TICKER_CIK_CACHE


def ticker_to_cik(ticker: str) -> int | None:
    """Return zero-padded CIK for a ticker, or None if unknown."""
    return _load_ticker_cik_map().get(ticker.upper())


def get_recent_filings(
    ticker: str,
    forms: Iterable[str] = ("8-K",),
    days_back: int = 90,
    limit: int = 15,
) -> list[dict]:
    """Recent filings of the given form types for ``ticker``.

    Returns a list of dicts: ``{date, form, primary_doc, items, url}``.
    """
    cik = ticker_to_cik(ticker)
    if cik is None:
        return []

    url = f"{_BASE}/submissions/CIK{cik:010d}.json"
    r = requests.get(url, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    recent = data.get("filings", {}).get("recent", {})
    rows = list(zip(
        recent.get("filingDate", []),
        recent.get("form", []),
        recent.get("primaryDocument", []),
        recent.get("items", []),
        recent.get("accessionNumber", []),
    ))

    cutoff = (date.today() - timedelta(days=days_back)).isoformat()
    forms_upper = {f.upper() for f in forms}
    out: list[dict] = []
    for filing_date, form, primary_doc, items, accession in rows:
        if form.upper() not in forms_upper:
            continue
        if filing_date < cutoff:
            continue
        clean_accession = accession.replace("-", "")
        out.append({
            "date": filing_date,
            "form": form,
            "primary_doc": primary_doc,
            "items": items or "",
            "accession": accession,
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{clean_accession}/{primary_doc}",
        })
        if len(out) >= limit:
            break
    return out


# Map 8-K Item numbers to plain English so the agent doesn't have to memorize
# the SEC item registry.
_8K_ITEM_LABELS = {
    "1.01": "Material Definitive Agreement Entered",
    "1.02": "Material Definitive Agreement Terminated",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Material Off-Balance-Sheet Arrangement / Debt Obligation",
    "2.04": "Triggering Event Accelerating Debt",
    "2.05": "Costs Associated with Exit or Disposal",
    "2.06": "Material Impairment",
    "3.01": "Notice of Delisting / Listing Standards Failure",
    "3.02": "Unregistered Sale of Equity Securities",
    "3.03": "Material Modification of Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "5.01": "Changes in Control",
    "5.02": "Departure/Appointment of Directors or Officers",
    "5.03": "Amendments to Articles / Bylaws",
    "5.07": "Submission of Matters to Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}


def describe_8k_items(items: str) -> str:
    """Turn an 8-K Items string like '1.01,2.03,9.01' into plain English."""
    if not items:
        return ""
    parts = []
    for raw in re.split(r"[,;]\s*", items):
        m = re.match(r"^(\d+\.\d+)$", raw.strip())
        if not m:
            continue
        code = m.group(1)
        label = _8K_ITEM_LABELS.get(code, "Unspecified Item")
        parts.append(f"Item {code}: {label}")
    return "; ".join(parts)


def get_institutional_holders_via_yfinance(ticker: str) -> str:
    """Top institutional holders via yfinance (aggregated from 13F filings).

    Cheaper than parsing 13F-HR XML directly — yfinance already aggregates
    the top ~10 holders with share counts and QoQ change percentages.
    """
    import yfinance as yf

    t = yf.Ticker(ticker)
    try:
        inst = t.institutional_holders
        major = t.major_holders
    except Exception as e:  # noqa: BLE001
        return f"[institutional_holders error: {e}]"

    if inst is None or len(inst) == 0:
        return f"No institutional holder data available for {ticker}."

    out = [f"## Institutional Holders for {ticker}", ""]
    if major is not None and len(major) > 0:
        out.append("**Ownership Breakdown:**")
        for _, row in major.iterrows():
            try:
                out.append(f"- {row.iloc[1]}: {row.iloc[0]}")
            except Exception:  # noqa: BLE001
                pass
        out.append("")

    out.append("**Top Institutional Holders (from 13F filings, QoQ changes):**")
    out.append("")
    out.append("| Holder | Shares | Value ($M) | % Out | QoQ Change |")
    out.append("|---|---:|---:|---:|---:|")
    for _, row in inst.iterrows():
        holder = row.get("Holder", "(unknown)")
        shares = row.get("Shares", 0)
        value = row.get("Value", 0)
        pct = row.get("pctHeld", 0) or row.get("% Out", 0)
        pct_change = row.get("pctChange", 0)
        try:
            shares_s = f"{int(shares):,}"
            value_m = f"{value / 1e6:,.1f}"
            pct_s = f"{pct * 100:.2f}%" if pct and pct < 1 else f"{pct:.2f}%"
            pct_change_s = (
                f"{pct_change * 100:+.1f}%" if pct_change and abs(pct_change) < 1
                else (f"{pct_change:+.1f}%" if pct_change else "—")
            )
        except Exception:  # noqa: BLE001
            continue
        out.append(f"| {holder} | {shares_s} | {value_m} | {pct_s} | {pct_change_s} |")
    return "\n".join(out)
