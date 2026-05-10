"""Run multi-agent analysis for a ticker NOT in your holdings.

Use this for "should I buy X?" decisions where the ticker isn't yet held —
no holdings_context is injected, so the Portfolio Manager produces a
standard Buy/Overweight/Hold/Underweight/Sell rating without per-account
actions.

Usage:
  uv run python scripts/analyze_new_ticker.py --ticker IREN
  uv run python scripts/analyze_new_ticker.py --ticker NET --trade-date 2026-05-08
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("analyze_new_ticker")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--trade-date", default="2026-05-08")
    parser.add_argument("--deep-model", default=None)
    parser.add_argument("--quick-model", default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    if args.deep_model:
        config["deep_think_llm"] = args.deep_model
    if args.quick_model:
        config["quick_think_llm"] = args.quick_model
    config["max_debate_rounds"] = 1

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = args.out_dir / f"new_ticker_{args.ticker}_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("=== Analyzing %s on %s (no holdings context) ===", args.ticker, args.trade_date)
    ta = TradingAgentsGraph(debug=False, config=config)

    t0 = time.time()
    final_state, _ = ta.propagate(args.ticker, args.trade_date)
    log.info("Done in %.1fs", time.time() - t0)

    serialisable = {
        k: v
        for k, v in final_state.items()
        if isinstance(v, (str, int, float, bool, list, dict)) or v is None
    }
    (out_dir / f"{args.ticker}.json").write_text(
        json.dumps(serialisable, indent=2, default=str), encoding="utf-8"
    )

    md_lines = [
        f"# {args.ticker} — New Ticker Analysis ({args.trade_date})",
        "",
        "Not currently held. PM produced a standard rating without per-account actions.",
        "",
        "---",
        "",
        "## Final Decision",
        "",
        final_state.get("final_trade_decision", "(no decision)"),
        "",
    ]
    (out_dir / "REPORT.md").write_text("\n".join(md_lines), encoding="utf-8")
    log.info("Output: %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
