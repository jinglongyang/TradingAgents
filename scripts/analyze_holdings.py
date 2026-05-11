"""Analyze a Fidelity portfolio export with the multi-agent trading graph.

Usage examples:
  # Dry-run: analyze a single ticker (validate prompt + output format)
  uv run python scripts/analyze_holdings.py --ticker NVDA

  # Full run: analyze all positions >= 1% of portfolio
  uv run python scripts/analyze_holdings.py --all

  # Custom CSVs and threshold
  uv run python scripts/analyze_holdings.py \\
      --csv ~/Downloads/Portfolio_Positions_May-10-2026.csv \\
      --csv ~/Downloads/Portfolio_Positions_May-10-2026\\(1\\).csv \\
      --min-pct 0.5

The script writes everything under ``outputs/portfolio_analysis_<date>/``:
  - ``REPORT.md``   — human-readable per-ticker writeup
  - ``actions.csv`` — flat per-account action list
  - ``summary.csv`` — one row per ticker with rating + key fields
  - ``per_ticker/<TICKER>.json`` — full final_state for each analysis
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402
from tradingagents.portfolio.holdings import (  # noqa: E402
    Holding,
    aggregate_by_ticker,
    build_holdings_context,
    parse_fidelity_csv,
)
from tradingagents.portfolio_db import latest_snapshot_date, load_latest_positions  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("analyze_holdings")


DEFAULT_CSV_PATHS = [
    Path.home() / "Downloads" / "Portfolio_Positions_May-10-2026.csv",
    Path.home() / "Downloads" / "Portfolio_Positions_May-10-2026(1).csv",
]


def load_holdings_from_db() -> tuple[dict[str, Holding], float]:
    """Load latest snapshot from SQLite — the source of truth.

    UI edits, Robinhood adds, price updates, and ticker fixes only land
    in the DB, so CSV reading is reserved for one-off overrides via --csv.
    """
    positions = load_latest_positions()
    snap = latest_snapshot_date()
    log.info("Loaded %d positions from DB (snapshot %s)", len(positions), snap)
    holdings = aggregate_by_ticker(positions)
    total_value = sum(h.total_value for h in holdings.values())
    return holdings, total_value


def load_holdings(csv_paths: list[Path]) -> tuple[dict[str, Holding], float]:
    """Parse all CSVs, aggregate by ticker, return (holdings, total_value)."""
    all_positions = []
    for p in csv_paths:
        if not p.exists():
            log.warning("CSV not found, skipping: %s", p)
            continue
        positions = parse_fidelity_csv(p)
        log.info("Loaded %d positions from %s", len(positions), p.name)
        all_positions.extend(positions)
    holdings = aggregate_by_ticker(all_positions)
    total_value = sum(h.total_value for h in holdings.values())
    return holdings, total_value


def select_tickers(
    holdings: dict[str, Holding],
    total_value: float,
    min_pct: float,
    explicit_ticker: str | None,
    explicit_list: list[str] | None,
) -> list[str]:
    """Pick which tickers to analyze.

    Priority: ``explicit_ticker`` > ``explicit_list`` > min_pct cutoff.
    Tickers absent from ``holdings`` produce a hard error.
    """
    if explicit_ticker:
        if explicit_ticker not in holdings:
            raise SystemExit(
                f"Ticker {explicit_ticker} not found in holdings. "
                f"Available: {sorted(holdings)}"
            )
        return [explicit_ticker]

    if explicit_list:
        missing = [t for t in explicit_list if t not in holdings]
        if missing:
            raise SystemExit(
                f"These tickers were not found in holdings: {missing}. "
                f"Available: {sorted(holdings)}"
            )
        return sorted(explicit_list, key=lambda s: -holdings[s].total_value)

    selected = [
        sym
        for sym, h in holdings.items()
        if total_value and (h.total_value / total_value * 100) >= min_pct
    ]
    selected.sort(key=lambda s: -holdings[s].total_value)
    return selected


def build_config(deep_model: str | None, quick_model: str | None) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    if deep_model:
        config["deep_think_llm"] = deep_model
    if quick_model:
        config["quick_think_llm"] = quick_model
    config["max_debate_rounds"] = 1
    config["data_vendors"] = {
        "core_stock_apis": "yfinance",
        "technical_indicators": "yfinance",
        "fundamental_data": "yfinance",
        "news_data": "yfinance",
    }
    return config


def run_one(
    ta: TradingAgentsGraph,
    ticker: str,
    trade_date: str,
    holdings: dict[str, Holding],
    portfolio_total: float,
) -> dict[str, Any]:
    """Run the multi-agent graph for one ticker, return the final_state dict."""
    holding = holdings[ticker]
    ctx = build_holdings_context(ticker, holding, portfolio_total)
    log.info("=== Analyzing %s ($%.0f, %d accts) ===", ticker, holding.total_value, len(holding.positions))

    t0 = time.time()
    final_state, _signal = ta.propagate(ticker, trade_date, holdings_context=ctx)
    dt = time.time() - t0
    log.info("[%s] done in %.1fs", ticker, dt)
    return final_state


def write_per_ticker_json(out_dir: Path, ticker: str, final_state: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    serialisable = {
        k: v
        for k, v in final_state.items()
        if isinstance(v, (str, int, float, bool, list, dict)) or v is None
    }
    (out_dir / f"{ticker}.json").write_text(
        json.dumps(serialisable, indent=2, default=str), encoding="utf-8"
    )


def write_report(
    out_dir: Path,
    trade_date: str,
    portfolio_total: float,
    results: list[tuple[str, Holding, dict[str, Any]]],
    failed: list[str] | None = None,
) -> None:
    """Render the human-readable REPORT.md."""
    failed = failed or []
    lines = [
        f"# Portfolio Analysis — {trade_date}",
        "",
        f"Total analyzed portfolio value: **${portfolio_total:,.0f}**",
        f"Tickers analyzed: **{len(results)}**",
    ]
    if failed:
        lines.append(
            f"⚠️ Failed (no decision, will retry on `--resume`): **{len(failed)}** — "
            + ", ".join(sorted(failed))
        )
    lines.extend(["", "---", ""])
    for ticker, holding, state in results:
        rating_md = state.get("final_trade_decision", "(no decision)")
        lines.extend(
            [
                f"## {ticker}",
                "",
                f"- Total value: ${holding.total_value:,.2f} "
                f"({holding.total_value / portfolio_total * 100:.2f}% of portfolio)",
                f"- Cost basis: ${holding.total_cost:,.2f}",
                f"- Unrealized P/L: {holding.unrealized_pl_pct:+.1f}%",
                f"- Accounts: {len(holding.positions)}",
                "",
                "### Final Decision",
                "",
                rating_md,
                "",
                "---",
                "",
            ]
        )
    (out_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def write_csvs(
    out_dir: Path,
    results: list[tuple[str, Holding, dict[str, Any]]],
) -> None:
    """Render summary.csv (one row per ticker) and actions.csv (one row per account-action)."""
    import csv
    import re

    rating_re = re.compile(r"\*\*Rating\*\*:\s*(\w+)", re.IGNORECASE)
    action_re = re.compile(
        r"\*\*(?P<account>[^\*]+?)\*\*\s*\[(?P<type>\w+)\]\s*→\s*"
        r"\*\*(?P<action>\w+)\*\*(?:\s*\((?P<size>[\d.]+)% of current\))?"
        r"\s*:\s*(?P<rationale>.*?)(?=$)",
        re.MULTILINE,
    )

    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "value", "cost", "pl_pct", "rating"])
        for ticker, h, state in results:
            md = state.get("final_trade_decision", "")
            m = rating_re.search(md)
            rating = m.group(1) if m else ""
            w.writerow(
                [
                    ticker,
                    f"{h.total_value:.2f}",
                    f"{h.total_cost:.2f}",
                    f"{h.unrealized_pl_pct:.2f}",
                    rating,
                ]
            )

    with open(out_dir / "actions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "account", "account_type", "action", "size_pct", "rationale"])
        for ticker, _h, state in results:
            md = state.get("final_trade_decision", "")
            for m in action_re.finditer(md):
                w.writerow(
                    [
                        ticker,
                        m.group("account").strip(),
                        m.group("type"),
                        m.group("action"),
                        m.group("size") or "",
                        m.group("rationale").strip(),
                    ]
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--csv",
        action="append",
        type=Path,
        help="Path to a Fidelity portfolio CSV. Can be passed multiple times. Defaults to two known files in ~/Downloads.",
    )
    parser.add_argument("--ticker", help="Run for a single ticker only (dry-run mode).")
    parser.add_argument("--tickers", help="Comma-separated whitelist (e.g. 'GOOGL,AMZN,AAPL'). Bypasses --min-pct.")
    parser.add_argument("--all", action="store_true", help="Run all tickers above --min-pct.")
    parser.add_argument("--min-pct", type=float, default=1.0, help="Minimum portfolio share (in %%) to analyze when --all. Default 1.0.")
    parser.add_argument("--trade-date", default="2026-05-08", help="Trading date (YYYY-MM-DD). Default 2026-05-08 (last trading day before today).")
    parser.add_argument("--deep-model", default=None,
                        help="Override DEFAULT_CONFIG['deep_think_llm'] (which reads DEEP_THINK_LLM env).")
    parser.add_argument("--quick-model", default=None,
                        help="Override DEFAULT_CONFIG['quick_think_llm'] (which reads QUICK_THINK_LLM env).")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--parallel", type=int, default=3,
                        help="How many tickers to analyze concurrently. Default 3 — "
                             "balances Azure rate limits against wall time. Use 1 for serial.")
    parser.add_argument("--resume", type=Path, default=None,
                        help="Resume into an existing portfolio_analysis_<ts>/ directory. "
                             "Tickers with per_ticker/<TICKER>.json already present are skipped "
                             "and the final REPORT/CSVs are written into the same dir.")
    args = parser.parse_args()

    if not args.ticker and not args.all and not args.tickers:
        parser.error("Must pass --ticker <SYM>, --tickers <CSV>, or --all")
    explicit_list = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else None
    )

    if args.csv:
        holdings, portfolio_total = load_holdings(args.csv)
        source = "CSV override"
    else:
        holdings, portfolio_total = load_holdings_from_db()
        source = "DB"
    if not holdings:
        log.error("No holdings loaded from %s.", source)
        return 1
    log.info("Aggregated portfolio (%s): $%.0f across %d tickers", source, portfolio_total, len(holdings))

    tickers = select_tickers(holdings, portfolio_total, args.min_pct, args.ticker, explicit_list)

    if args.resume:
        out_dir = args.resume
        if not out_dir.exists():
            log.error("--resume path does not exist: %s", out_dir)
            return 1
        per_ticker_dir = out_dir / "per_ticker"
        already_done = {p.stem for p in per_ticker_dir.glob("*.json")} if per_ticker_dir.exists() else set()
        skipped = [t for t in tickers if t in already_done]
        tickers = [t for t in tickers if t not in already_done]
        if skipped:
            log.info("Resume: skipping %d already-done ticker(s): %s", len(skipped), ", ".join(skipped))
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_dir = args.out_dir / f"portfolio_analysis_{timestamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        per_ticker_dir = out_dir / "per_ticker"

    log.info("Will analyze %d ticker(s): %s", len(tickers), ", ".join(tickers))
    if not tickers:
        log.info("Nothing to analyze — building REPORT/CSVs from existing per_ticker JSON.")

    config = build_config(args.deep_model, args.quick_model)

    results: list[tuple[str, Holding, dict[str, Any]]] = []
    # Pull in any prior per-ticker JSON from a previous run so the final
    # REPORT.md / CSVs include them alongside whatever we analyse this pass.
    if args.resume and per_ticker_dir.exists():
        import json as _json
        for jp in sorted(per_ticker_dir.glob("*.json")):
            sym = jp.stem
            if sym not in holdings:
                continue
            try:
                state = _json.loads(jp.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping unreadable %s: %s", jp, exc)
                continue
            results.append((sym, holdings[sym], state))
    parallel = max(1, int(args.parallel))
    log.info("Concurrency: %d (use --parallel N to change)", parallel)

    def _analyze_one(idx_ticker):
        i, ticker = idx_ticker
        log.info("[%d/%d] Starting %s", i, len(tickers), ticker)
        # Each worker constructs its own TradingAgentsGraph because the graph
        # carries mutable per-run state (self.curr_state, self.ticker).
        # Construction is fast (~1s) and LLM clients underneath are thread-safe.
        ta = TradingAgentsGraph(debug=False, config=config)
        try:
            return ticker, run_one(ta, ticker, args.trade_date, holdings, portfolio_total)
        except Exception as exc:
            log.exception("Failed on %s: %s", ticker, exc)
            return ticker, None

    failed: list[str] = []
    indexed = list(enumerate(tickers, 1))
    if parallel == 1:
        for it in indexed:
            ticker, state = _analyze_one(it)
            if state is None:
                failed.append(ticker)
                continue
            results.append((ticker, holdings[ticker], state))
            write_per_ticker_json(per_ticker_dir, ticker, state)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = [ex.submit(_analyze_one, it) for it in indexed]
            for fut in as_completed(futures):
                ticker, state = fut.result()
                if state is None:
                    failed.append(ticker)
                    continue
                results.append((ticker, holdings[ticker], state))
                # Persist per-ticker JSON immediately so partial progress survives
                # a crash or kill.
                write_per_ticker_json(per_ticker_dir, ticker, state)

    if results:
        write_report(out_dir, args.trade_date, portfolio_total, results, failed=failed)
        write_csvs(out_dir, results)
        # Persist the failure list separately so resume / /runs can read it
        # without having to grep REPORT.md.
        if failed:
            (out_dir / "failed.txt").write_text(
                "\n".join(sorted(failed)) + "\n", encoding="utf-8"
            )
            log.warning("Failed tickers (%d): %s", len(failed), ", ".join(sorted(failed)))
        log.info("All outputs written to %s", out_dir)
    else:
        log.error("No tickers produced results.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
