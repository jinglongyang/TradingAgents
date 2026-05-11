"""Sector analysis: pick representative tickers + run multi-agent on each.

Useful when the user asks "what's worth buying in AI infrastructure?"
or "show me the semiconductor cycle exposure" — they don't know the
specific ticker, just the theme.

Step 1: Ask the quick-thinking LLM to enumerate 5-10 publicly-traded
        tickers that best represent the sector query. The LLM is told
        to return only validated US-listed tickers (yfinance fetchable).
Step 2: Validate each ticker against yfinance (skip dead tickers).
Step 3: Run analyze_new_ticker for each survivor.
Step 4: Write a per-sector summary that lists "推荐 (Buy/Overweight)"
        vs "不推荐 (Hold/Underweight/Sell)" with the PT and entry zone.

Usage:
  uv run python scripts/analyze_sector.py --sector "AI infrastructure"
  uv run python scripts/analyze_sector.py --sector "GLP-1 减肥药" --max-tickers 6
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess as sp
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import yfinance as yf  # noqa: E402

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.llm_clients import create_llm_client  # noqa: E402
from tradingagents.portfolio_db import connect, latest_snapshot_date  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("analyze_sector")


TICKER_PROMPT = """List the 5 to 10 most representative publicly-traded stock tickers for this sector / theme:

"{sector}"

Requirements:
- Only US-listed tickers (NYSE / NASDAQ).
- Cover both established leaders and high-quality growth names where applicable.
- Include the actual ticker symbol (e.g. NVDA, GOOGL), not the company name.
- Avoid ETFs unless the query specifically asks for one.
{user_block}
Return ONLY a JSON array of ticker strings, nothing else. Example:
["NVDA", "AMD", "AVGO", "TSM", "INTC"]"""


def load_user_holdings() -> list[tuple[str, str]]:
    """Return (symbol, sector) for every ticker in the latest snapshot."""
    latest = latest_snapshot_date()
    if not latest:
        return []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ps.symbol AS symbol, COALESCE(t.sector, '') AS sector
              FROM positions_snapshot ps
              LEFT JOIN tickers t ON t.symbol = ps.symbol
             WHERE ps.import_date = ?
            """,
            (latest,),
        ).fetchall()
    return [(r["symbol"], r["sector"]) for r in rows]


def llm_filter_relevant_holdings(sector: str, holdings: list[tuple[str, str]]) -> list[str]:
    """Ask the LLM which user holdings are relevant to the theme."""
    if not holdings:
        return []
    config = DEFAULT_CONFIG.copy()
    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["quick_think_llm"],
        base_url=config.get("backend_url"),
    )
    llm = client.get_llm()
    holdings_str = ", ".join(f"{s}({sec})" if sec else s for s, sec in holdings)
    prompt = (
        f'The user holds these tickers (ticker(sector)): {holdings_str}\n\n'
        f'Which of these are CORE plays on the theme "{sector}"?\n\n'
        f'Be STRICT — only include tickers whose primary business is the theme. '
        f'Reject anything that\'s only tangentially related (e.g. don\'t include AAPL '
        f'for an "AI chip" theme just because Apple uses chips). For "AI infrastructure" '
        f'/ "memory" / "storage" themes specifically, include semiconductor IP / chip / '
        f'memory / NAND / HDD / data-center direct plays — not general software, not '
        f'adtech, not crypto.\n\n'
        f'Return a JSON array of AT MOST 5 tickers, e.g. ["MU", "SNDK", "DRAM"]. '
        f'Return [] if nothing is a core fit.'
    )
    try:
        resp = llm.invoke(prompt)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"\[([^\]]*)\]", content)
        if not m:
            return []
        try:
            raw = json.loads("[" + m.group(1) + "]")
        except json.JSONDecodeError:
            raw = [t.strip().strip('"').strip("'") for t in m.group(1).split(",")]
        held_set = {s for s, _ in holdings}
        return [t.upper() for t in raw if isinstance(t, str) and t.strip().upper() in held_set]
    except Exception as e:
        log.warning("Holding-relevance filter failed: %s", e)
        return []


def get_sector_tickers(sector: str, max_tickers: int) -> list[str]:
    """Ask LLM to enumerate tickers, then validate. Also surfaces any of the
    user's existing holdings that are relevant to the theme so we never silently
    miss something they already own (e.g. SNDK after the WDC spin-off, or DRAM ETF)."""
    config = DEFAULT_CONFIG.copy()
    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["quick_think_llm"],
        base_url=config.get("backend_url"),
    )
    llm = client.get_llm()

    holdings = load_user_holdings()
    # Pass 1 prompt should NOT bias the LLM toward existing holdings — sector
    # analysis is mostly about discovering new ideas. We surface held tickers
    # via a separate pass below so we never silently drop them either.
    user_block = ""
    resp = llm.invoke(TICKER_PROMPT.format(sector=sector, user_block=user_block))
    content = resp.content if isinstance(resp.content, str) else str(resp.content)

    m = re.search(r"\[([^\]]+)\]", content)
    if not m:
        log.error("LLM did not return JSON array: %s", content[:200])
        return []

    try:
        raw = json.loads("[" + m.group(1) + "]")
    except json.JSONDecodeError:
        raw = [t.strip().strip('"').strip("'") for t in m.group(1).split(",")]

    candidates = [t.upper() for t in raw if isinstance(t, str) and t.strip()]
    log.info("LLM proposed %d tickers: %s", len(candidates), candidates)

    # Second pass: catch user holdings the first pass missed (LLM knowledge
    # cutoff for recent IPOs / spin-offs, ETF exclusion rule, etc.). These get
    # APPENDED — not prepended — because the whole point of sector analysis is
    # to surface fresh ideas, not just analyze what's already owned.
    relevant_held = llm_filter_relevant_holdings(sector, holdings)
    cand_set = {t.upper() for t in candidates}
    missing_held = [t for t in relevant_held if t.upper() not in cand_set]
    if missing_held:
        log.info("Adding %d held tickers LLM missed: %s", len(missing_held), missing_held)

    merged = candidates + missing_held
    effective_cap = max_tickers + len(missing_held)  # never displace LLM picks

    validated = []
    for t in merged:
        try:
            info = yf.Ticker(t).history(period="5d")
            if len(info) > 0:
                validated.append(t)
            else:
                log.warning("Ticker %s has no recent price data, skipping", t)
        except Exception as e:
            log.warning("Ticker %s validation failed: %s", t, e)
        if len(validated) >= effective_cap:
            break

    log.info("Validated tickers (cap=%d): %s", effective_cap, validated)
    return validated


def run_per_ticker(ticker: str, trade_date: str) -> bool:
    """Run analyze_new_ticker.py for one ticker. Returns success bool."""
    log.info("=== Analyzing %s ===", ticker)
    result = sp.run(
        ["uv", "run", "python", "scripts/analyze_new_ticker.py",
         "--ticker", ticker, "--trade-date", trade_date],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Failed on %s: %s", ticker, result.stderr[-500:])
        return False
    return True


def collect_summary(tickers: list[str], sector: str, out_dir: Path) -> None:
    """Read each analyze_new_ticker output and build a sector recommendation MD."""
    rating_re = re.compile(r"\*\*Rating\*\*:\s*(\w+)")
    pt_re = re.compile(r"Price Target.*?\$?([\d.]+)")
    entry_re = re.compile(r"Entry/Exit Zone\*\*:\s*([^\n]+)")

    by_rating: dict[str, list] = {}
    for t in tickers:
        # Find most-recent new_ticker_<T>_*/REPORT.md
        candidates = sorted(Path("outputs").glob(f"new_ticker_{t}_*/REPORT.md"), reverse=True)
        if not candidates:
            continue
        md = candidates[0].read_text(encoding="utf-8")
        m = rating_re.search(md)
        pt = pt_re.search(md)
        ez = entry_re.search(md)
        if not m:
            continue
        rating = m.group(1)
        by_rating.setdefault(rating, []).append({
            "ticker": t,
            "rating": rating,
            "pt": pt.group(1) if pt else "—",
            "entry": (ez.group(1) if ez else "—")[:80],
        })

    lines = [
        f"# Sector Analysis: {sector}",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"\n分析了 **{len(tickers)}** 个 ticker: {', '.join(tickers)}\n",
        "---\n",
    ]

    rec_groups = [
        ("✅ 推荐买入 (Buy)", "Buy"),
        ("🟢 推荐超配 (Overweight)", "Overweight"),
        ("⚪ 中性持有 (Hold)", "Hold"),
        ("🟡 低配减仓 (Underweight)", "Underweight"),
        ("🔴 不推荐 / 卖出 (Sell)", "Sell"),
    ]
    for title, rating in rec_groups:
        rows = by_rating.get(rating, [])
        if not rows:
            continue
        lines.append(f"\n## {title}\n")
        lines.append("| Ticker | 目标价 | 入场/出场区间 |")
        lines.append("|---|---|---|")
        for r in rows:
            lines.append(f"| **{r['ticker']}** | ${r['pt']} | {r['entry']} |")

    if not any(by_rating.values()):
        lines.append("\n*没有 ticker 拿到有效评级 — 检查 analyze_new_ticker 日志*")
    elif not by_rating.get("Buy") and not by_rating.get("Overweight"):
        lines.append("\n## 总结\n\n**本行业目前没有强烈推荐的标的**。PM 对所有候选的判断都是中性或保守，建议等待更好的进场机会。")

    summary_path = out_dir / f"sector_{sector.replace(' ', '_')[:50]}_{datetime.now().strftime('%H%M%S')}.md"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Summary saved: %s", summary_path)
    print(f"\n=== Summary ===\n{summary_path}\n")
    print("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sector", required=True, help="Sector / theme query (Chinese OK)")
    # Default 10 to match the prompt's "5 to 10" guidance. The validator already
    # has effective_cap = max_tickers + len(missing_held) so this is the LLM
    # picks ceiling specifically — held additions stack on top.
    parser.add_argument("--max-tickers", type=int, default=10)
    parser.add_argument("--trade-date", default="2026-05-08")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()

    log.info("Sector: %s", args.sector)
    tickers = get_sector_tickers(args.sector, args.max_tickers)
    if not tickers:
        print("No valid tickers found.", file=sys.stderr)
        return 1

    for t in tickers:
        try:
            run_per_ticker(t, args.trade_date)
        except Exception as e:
            log.exception("Crash on %s: %s", t, e)
        time.sleep(2)  # be polite

    collect_summary(tickers, args.sector, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
