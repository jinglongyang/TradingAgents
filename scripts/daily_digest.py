"""Daily portfolio digest — scan recent events, big movers, rating changes.

Designed to be cron'd at end of trading day. Produces a markdown report
with sections:
1. 大幅波动持仓（±5% in 1 day or ±10% in 5 days）
2. 最近 SEC 8-K 重要事件（持仓 ticker 过去 24h 的新 filings）
3. 待 reflection 的 PM 决策（5 day window）
4. 板块轮动（XLK/XLV 等）

Optionally emails via `mail` (mac) or any sendmail-compatible CLI.

Usage:
  uv run python scripts/daily_digest.py
  uv run python scripts/daily_digest.py --email you@example.com
  uv run python scripts/daily_digest.py --since-days 1

Cron entry:
  0 17 * * 1-5  cd /path/to/TradingAgents && uv run python scripts/daily_digest.py \\
                  --email you@example.com >> /tmp/digest.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import subprocess as sp
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import yfinance as yf  # noqa: E402

from tradingagents.dataflows.sec_edgar import describe_8k_items, get_recent_filings  # noqa: E402
from tradingagents.portfolio_db import connect, init_db, latest_snapshot_date  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily_digest")


def get_held_tickers() -> list[str]:
    latest = latest_snapshot_date()
    if not latest:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM positions_snapshot WHERE import_date = ?",
            (latest,),
        ).fetchall()
    return [r["symbol"] for r in rows]


def scan_movers(tickers: list[str], threshold_1d: float = 5.0, threshold_5d: float = 10.0) -> list[dict]:
    movers = []
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="10d")
            if len(h) < 2:
                continue
            d1 = (h["Close"].iloc[-1] - h["Close"].iloc[-2]) / h["Close"].iloc[-2] * 100
            d5 = (h["Close"].iloc[-1] - h["Close"].iloc[-6]) / h["Close"].iloc[-6] * 100 if len(h) >= 6 else 0
            if abs(d1) >= threshold_1d or abs(d5) >= threshold_5d:
                movers.append({"ticker": t, "d1": float(d1), "d5": float(d5),
                                "price": float(h["Close"].iloc[-1])})
        except Exception as e:
            log.debug("Failed %s: %s", t, e)
    movers.sort(key=lambda m: -abs(m["d1"]))
    return movers


def scan_sec_filings(tickers: list[str], days_back: int = 1) -> list[dict]:
    new_filings = []
    for t in tickers:
        try:
            filings = get_recent_filings(t, forms=("8-K", "13D", "13G", "4", "NT 10-Q", "NT 10-K"), days_back=days_back)
            for f in filings:
                f["ticker"] = t
                if f["form"] == "8-K":
                    f["description"] = describe_8k_items(f.get("items", ""))
                new_filings.append(f)
        except Exception as e:
            log.debug("SEC failed %s: %s", t, e)
    new_filings.sort(key=lambda f: f["date"], reverse=True)
    return new_filings


def scan_sectors() -> list[dict]:
    SECTORS = [("XLK", "Tech"), ("XLV", "Health"), ("XLF", "Fin"),
               ("XLY", "ConsDisc"), ("XLP", "ConsStapl"), ("XLE", "Energy"),
               ("XLU", "Util"), ("XLRE", "RealEst"), ("XLI", "Indust"),
               ("XLB", "Materials"), ("XLC", "Comm"), ("SPY", "SPY")]
    out = []
    for symbol, name in SECTORS:
        try:
            h = yf.Ticker(symbol).history(period="35d")
            if len(h) < 6:
                continue
            d1 = (h["Close"].iloc[-1] - h["Close"].iloc[-2]) / h["Close"].iloc[-2] * 100
            d5 = (h["Close"].iloc[-1] - h["Close"].iloc[-6]) / h["Close"].iloc[-6] * 100
            d30 = (h["Close"].iloc[-1] - h["Close"].iloc[0]) / h["Close"].iloc[0] * 100
            out.append({"symbol": symbol, "name": name, "d1": float(d1), "d5": float(d5), "d30": float(d30)})
        except Exception:
            continue
    return out


def build_digest(since_days: int = 1) -> str:
    init_db()
    today = date.today().isoformat()
    tickers = get_held_tickers()
    if not tickers:
        return f"# Daily Digest — {today}\n\n持仓数据为空，没什么可汇总的。"

    movers = scan_movers(tickers)
    filings = scan_sec_filings(tickers, days_back=since_days)
    sectors = scan_sectors()

    lines = [f"# 📰 Daily Portfolio Digest — {today}", f"\n持仓 {len(tickers)} 个 ticker 扫描完成", "\n---"]

    # 1. Big movers
    lines.append("\n## 1. 大幅波动持仓 (|1d|≥5% 或 |5d|≥10%)\n")
    if not movers:
        lines.append("✅ 没有显著波动")
    else:
        lines.append("| Ticker | 1d | 5d | 价格 |")
        lines.append("|---|---:|---:|---:|")
        for m in movers:
            d1_e = "📈" if m["d1"] > 0 else "📉"
            lines.append(f"| **{m['ticker']}** | {d1_e} {m['d1']:+.2f}% | {m['d5']:+.2f}% | ${m['price']:.2f} |")

    # 2. SEC filings
    lines.append(f"\n## 2. 最近 {since_days} 天 SEC 重大事件\n")
    if not filings:
        lines.append("✅ 没有新的 8-K / 13D / Form 4 / 延迟报告")
    else:
        lines.append("| Date | Ticker | Form | Description |")
        lines.append("|---|---|---|---|")
        for f in filings[:20]:
            desc = f.get("description") or f.get("items") or "—"
            lines.append(f"| {f['date']} | **{f['ticker']}** | {f['form']} | {desc} |")

    # 3. Sector rotation
    lines.append("\n## 3. 板块表现\n")
    if sectors:
        spy_d5 = next((s["d5"] for s in sectors if s["symbol"] == "SPY"), 0)
        spy_d30 = next((s["d30"] for s in sectors if s["symbol"] == "SPY"), 0)
        sectors_no_spy = [s for s in sectors if s["symbol"] != "SPY"]
        sectors_no_spy.sort(key=lambda s: -s["d5"])
        lines.append(f"基准 SPY: 5d {spy_d5:+.2f}% · 30d {spy_d30:+.2f}%\n")
        lines.append("| Sector | 1d | 5d | 30d | 5d vs SPY |")
        lines.append("|---|---:|---:|---:|---:|")
        for s in sectors_no_spy[:5]:
            rel = s["d5"] - spy_d5
            arrow = "🟢" if rel > 0 else "🔴"
            lines.append(f"| {s['symbol']} ({s['name']}) | {s['d1']:+.2f}% | {s['d5']:+.2f}% | {s['d30']:+.2f}% | {arrow} {rel:+.2f}% |")

    # 4. Pending PM decisions (5 day window)
    with connect() as conn:
        pending = conn.execute(
            "SELECT symbol, trade_date, rating FROM decisions WHERE raw_return IS NULL AND trade_date < ? ORDER BY trade_date",
            ((date.today() - timedelta(days=4)).isoformat(),),
        ).fetchall()
    if pending:
        lines.append(f"\n## 4. 待评估 PM 决策 ({len(pending)} 个，trade_date > 5 天前)\n")
        lines.append("Run `uv run python scripts/performance.py` 或 `evaluate_decisions.py` 拉 alpha 数据")
        for p in pending[:10]:
            lines.append(f"- {p['symbol']} ({p['rating']}) — analyzed {p['trade_date']}")

    lines.append(f"\n---\n*生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    return "\n".join(lines)


def send_email(body: str, to: str, subject: str | None = None) -> bool:
    """Send via the `mail` command (BSD mail on macOS). Falls back to print."""
    subject = subject or f"Daily Portfolio Digest — {date.today().isoformat()}"
    try:
        p = sp.Popen(
            ["mail", "-s", subject, to],
            stdin=sp.PIPE,
            text=True,
        )
        p.communicate(body)
        return p.returncode == 0
    except FileNotFoundError:
        log.warning("`mail` command not found. Markdown printed to stdout instead.")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since-days", type=int, default=1, help="Look back N days for SEC filings (default 1)")
    parser.add_argument("--email", help="Email address to send to (uses `mail` CLI)")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"), help="Save digest to this dir")
    args = parser.parse_args()

    digest = build_digest(since_days=args.since_days)
    print(digest)

    # Save to file
    args.out_dir.mkdir(parents=True, exist_ok=True)
    digest_path = args.out_dir / f"digest_{date.today().isoformat()}.md"
    digest_path.write_text(digest, encoding="utf-8")
    print(f"\nSaved: {digest_path}", file=sys.stderr)

    if args.email:
        sent = send_email(digest, args.email)
        print(f"Email {'sent' if sent else 'failed'} to {args.email}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
