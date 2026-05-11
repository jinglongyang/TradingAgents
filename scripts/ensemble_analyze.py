"""Multi-LLM ensemble: run the same ticker through 2-3 different models
and surface where they agree (high conviction) vs disagree (low conviction).

The same prompt with different LLMs is the cheapest cross-validation
technique against single-model bias and silent failures.

Usage:
  uv run python scripts/ensemble_analyze.py --ticker NVDA
  uv run python scripts/ensemble_analyze.py --ticker NVDA \\
      --models gpt-5.4,gpt-5.4-mini,gpt-4.1-mini

Writes outputs/ensemble_<TICKER>_<timestamp>/{model_<i>}/REPORT.md
and a final summary.md aggregating ratings + key disagreements.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess as sp
import sys
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ensemble")


RATING_RE = re.compile(r"\*\*Rating\*\*:\s*(\w+)")
PT_RE = re.compile(r"Price Target.*?\$?([\d.]+)")


def run_one_model(ticker: str, model: str, out_dir: Path, mode: str) -> dict | None:
    """Run analyze script with a specific model, return parsed result."""
    log.info("=== Running %s with model %s ===", ticker, model)
    env = dict(os.environ)
    env["DEEP_THINK_LLM"] = model
    env["QUICK_THINK_LLM"] = model

    is_held = mode == "held"
    script = "scripts/analyze_holdings.py" if is_held else "scripts/analyze_new_ticker.py"
    arg = "--tickers" if is_held else "--ticker"

    result = sp.run(
        ["uv", "run", "python", script, arg, ticker],
        env=env, capture_output=True, text=True, timeout=600,
    )

    if result.returncode != 0:
        log.error("Failed with %s: %s", model, result.stderr[-500:])
        return None

    # Find the most recent output dir for this ticker
    pattern = f"new_ticker_{ticker}_*" if not is_held else f"portfolio_analysis_*"
    candidates = sorted(Path("outputs").glob(f"{pattern}/REPORT.md"), reverse=True)
    if not candidates:
        return None
    md = candidates[0].read_text(encoding="utf-8")

    rating_m = RATING_RE.search(md)
    pt_m = PT_RE.search(md)

    # Move to ensemble dir to avoid future model runs overwriting
    saved = out_dir / f"model_{model.replace('/', '_').replace(':', '_').replace('.', '_')}.md"
    saved.write_text(md, encoding="utf-8")

    return {
        "model": model,
        "rating": rating_m.group(1) if rating_m else "?",
        "price_target": pt_m.group(1) if pt_m else None,
        "report_path": str(saved),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--models", default="gpt-5.4,gpt-5.4-mini,gpt-4.1-mini",
                        help="Comma-separated model names")
    parser.add_argument("--out-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    ticker = args.ticker.upper()
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_dir = args.out_dir / f"ensemble_{ticker}_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Decide held vs new mode
    from tradingagents.portfolio_db import connect, latest_snapshot_date
    latest = latest_snapshot_date()
    mode = "new"
    if latest:
        with connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM positions_snapshot WHERE import_date = ? AND symbol = ? LIMIT 1",
                (latest, ticker),
            ).fetchone()
            if row:
                mode = "held"
    log.info("Mode: %s (%s)", mode, "with holdings_context" if mode == "held" else "no holdings_context")

    results = []
    for m in models:
        r = run_one_model(ticker, m, out_dir, mode)
        if r:
            results.append(r)

    # Build summary
    summary = [
        f"# Ensemble Analysis: {ticker}",
        f"\n生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} · 模式: {mode}",
        f"\n跑了 **{len(results)}** 个模型: {', '.join(r['model'] for r in results)}",
        "\n---\n",
        "## 评级一致性\n",
        "| Model | Rating | Price Target |",
        "|---|---|---|",
    ]
    for r in results:
        summary.append(f"| {r['model']} | **{r['rating']}** | ${r['price_target']}{' ' if r['price_target'] else '—'} |")

    # Detect agreement
    ratings = [r["rating"] for r in results if r["rating"] != "?"]
    if not ratings:
        summary.append("\n**❌ 没有模型成功完成分析**")
    elif len(set(ratings)) == 1:
        summary.append(f"\n## ✅ 高 conviction\n**所有 {len(ratings)} 个模型一致评级为 {ratings[0]}**。这是强信号。")
    elif len(set(ratings)) == len(ratings):
        summary.append(f"\n## ⚠️ 严重分歧\n**{len(ratings)} 个模型给出 {len(set(ratings))} 个不同评级**: {', '.join(set(ratings))}。低 conviction，建议人工审阅每个 thesis。")
    else:
        from collections import Counter
        c = Counter(ratings)
        most = c.most_common(1)[0]
        summary.append(f"\n## 🟡 部分一致\n**多数派评级**: {most[0]} ({most[1]}/{len(ratings)} 模型)。少数派建议人工读 thesis 看是否有有效反对意见。")

    # Price target spread
    pts = [float(r["price_target"]) for r in results if r["price_target"]]
    if len(pts) >= 2:
        spread = (max(pts) - min(pts)) / sum(pts) * len(pts) * 100
        summary.append(f"\n## 价格目标分布\n- 最低: \\${min(pts):.0f}\n- 最高: \\${max(pts):.0f}\n- 离散度: {spread:.1f}%")
        if spread > 30:
            summary.append("- ⚠️ 价格目标分歧 > 30% — 模型对估值有结构性差异")

    summary.append("\n---\n## 各模型完整 thesis\n")
    for r in results:
        summary.append(f"- [{r['model']}]({Path(r['report_path']).name})")

    summary_path = out_dir / "summary.md"
    summary_path.write_text("\n".join(summary), encoding="utf-8")
    print(f"\n=== Summary ===\n{summary_path}\n")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
