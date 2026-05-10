"""Render markdown reports + actions CSV into self-contained HTML files.

Produces ``outputs/html/`` with:
  - index.html      — overview dashboard with key numbers and nav
  - today.html      — TODAY_2026-05-10.md rendered
  - memo.html       — DECISION_MEMO_2026-05-10.md rendered
  - dashboard.html  — actions.csv visualization (color-coded buy/sell)
  - reports.html    — all per-batch REPORT.md files concatenated

Each HTML is self-contained (CSS inlined, no external assets), so the user
can email/AirDrop them and open offline. Chinese fonts use system stacks
(PingFang SC / Microsoft YaHei) — no web font dependency.
"""

from __future__ import annotations

import csv
import html
import re
import sys
from pathlib import Path
from typing import Iterable

import markdown


GITHUB_CSS = """
:root {
    --color-fg: #1f2328;
    --color-fg-muted: #59636e;
    --color-bg: #ffffff;
    --color-bg-subtle: #f6f8fa;
    --color-border: #d1d9e0;
    --color-border-muted: #d8dee4;
    --color-accent: #0969da;
    --color-success: #1a7f37;
    --color-danger: #d1242f;
    --color-warning: #9a6700;
    --color-success-bg: #dafbe1;
    --color-danger-bg: #ffebe9;
    --color-warning-bg: #fff8c5;
}
@media (prefers-color-scheme: dark) {
    :root {
        --color-fg: #e6edf3;
        --color-fg-muted: #848d97;
        --color-bg: #0d1117;
        --color-bg-subtle: #161b22;
        --color-border: #30363d;
        --color-border-muted: #21262d;
        --color-accent: #2f81f7;
        --color-success: #3fb950;
        --color-danger: #f85149;
        --color-warning: #d29922;
        --color-success-bg: #033a16;
        --color-danger-bg: #67060c;
        --color-warning-bg: #341a00;
    }
}
* { box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", "Source Han Sans CN",
                 "Helvetica Neue", Arial, sans-serif;
    line-height: 1.6;
    color: var(--color-fg);
    background: var(--color-bg);
    margin: 0;
    padding: 0;
    font-size: 16px;
    -webkit-font-smoothing: antialiased;
}
.container {
    max-width: 980px;
    margin: 0 auto;
    padding: 32px 24px 80px;
}
@media (max-width: 600px) {
    .container { padding: 16px 12px 60px; }
    body { font-size: 15px; }
}
.nav {
    background: var(--color-bg-subtle);
    border-bottom: 1px solid var(--color-border);
    padding: 12px 24px;
    position: sticky;
    top: 0;
    z-index: 10;
    -webkit-backdrop-filter: blur(8px);
    backdrop-filter: blur(8px);
    background: color-mix(in srgb, var(--color-bg-subtle) 85%, transparent);
}
.nav-inner {
    max-width: 980px;
    margin: 0 auto;
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    align-items: center;
}
.nav a {
    color: var(--color-fg-muted);
    text-decoration: none;
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
}
.nav a:hover { background: var(--color-border-muted); color: var(--color-fg); }
.nav a.active { color: var(--color-accent); background: color-mix(in srgb, var(--color-accent) 12%, transparent); }
.nav-brand {
    font-weight: 600;
    color: var(--color-fg);
    margin-right: auto;
    font-size: 14px;
}
h1, h2, h3 { font-weight: 600; line-height: 1.25; }
h1 { font-size: 2em; padding-bottom: 0.3em; border-bottom: 1px solid var(--color-border-muted); margin-top: 0; }
h2 { font-size: 1.5em; padding-bottom: 0.3em; border-bottom: 1px solid var(--color-border-muted); margin-top: 1.5em; }
h3 { font-size: 1.25em; margin-top: 1.5em; }
p { margin: 1em 0; }
a { color: var(--color-accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    background: var(--color-bg-subtle);
    padding: 0.2em 0.4em;
    border-radius: 6px;
    font-size: 85%;
}
pre {
    background: var(--color-bg-subtle);
    padding: 16px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 85%;
    line-height: 1.45;
}
pre code { background: transparent; padding: 0; }
table {
    border-collapse: collapse;
    margin: 16px 0;
    width: 100%;
    font-size: 0.95em;
    display: block;
    overflow-x: auto;
}
table thead { background: var(--color-bg-subtle); }
table th, table td {
    border: 1px solid var(--color-border-muted);
    padding: 8px 12px;
    text-align: left;
}
table th { font-weight: 600; }
blockquote {
    border-left: 4px solid var(--color-border);
    padding-left: 16px;
    color: var(--color-fg-muted);
    margin: 16px 0;
}
hr { border: none; border-top: 1px solid var(--color-border-muted); margin: 32px 0; }
ul, ol { margin: 1em 0; padding-left: 2em; }
li { margin: 0.25em 0; }
strong { font-weight: 600; }

/* Custom: stat cards on index page */
.stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin: 24px 0;
}
.stat-card {
    background: var(--color-bg-subtle);
    border: 1px solid var(--color-border-muted);
    border-radius: 8px;
    padding: 20px;
}
.stat-card .label {
    font-size: 13px;
    color: var(--color-fg-muted);
    margin: 0 0 4px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 500;
}
.stat-card .value {
    font-size: 28px;
    font-weight: 600;
    margin: 0;
    font-variant-numeric: tabular-nums;
}
.stat-card .sub { font-size: 13px; color: var(--color-fg-muted); margin: 4px 0 0; }
.stat-card.danger .value { color: var(--color-danger); }
.stat-card.success .value { color: var(--color-success); }
.stat-card.accent .value { color: var(--color-accent); }

/* Action rows */
.action-add { background: color-mix(in srgb, var(--color-success-bg) 60%, transparent); }
.action-add td:first-child { border-left: 3px solid var(--color-success); }
.action-reduce, .action-exit, .action-sell {
    background: color-mix(in srgb, var(--color-danger-bg) 60%, transparent);
}
.action-reduce td:first-child, .action-exit td:first-child, .action-sell td:first-child {
    border-left: 3px solid var(--color-danger);
}
.action-hold { color: var(--color-fg-muted); }
.tag {
    display: inline-block;
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: 500;
    background: var(--color-bg-subtle);
    border: 1px solid var(--color-border-muted);
}
.tag-roth { background: color-mix(in srgb, #8250df 18%, transparent); border-color: #8250df; color: #8250df; }
.tag-taxdeferred { background: color-mix(in srgb, var(--color-success) 18%, transparent); border-color: var(--color-success); color: var(--color-success); }
.tag-taxable { background: color-mix(in srgb, var(--color-warning) 18%, transparent); border-color: var(--color-warning); color: var(--color-warning); }
.tag-childedu { background: color-mix(in srgb, var(--color-accent) 18%, transparent); border-color: var(--color-accent); color: var(--color-accent); }

.rating {
    display: inline-block;
    font-weight: 600;
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 13px;
}
.rating-buy, .rating-overweight { background: var(--color-success-bg); color: var(--color-success); }
.rating-hold { background: var(--color-bg-subtle); color: var(--color-fg-muted); }
.rating-underweight, .rating-sell { background: var(--color-danger-bg); color: var(--color-danger); }

.warning-box {
    background: var(--color-warning-bg);
    border-left: 4px solid var(--color-warning);
    padding: 12px 16px;
    margin: 16px 0;
    border-radius: 0 6px 6px 0;
}
.footer {
    text-align: center;
    color: var(--color-fg-muted);
    font-size: 13px;
    padding-top: 32px;
    margin-top: 48px;
    border-top: 1px solid var(--color-border-muted);
}
"""


def _shell(title: str, body: str, current: str = "") -> str:
    """Wrap body in the standard nav + container shell."""
    nav_items = [
        ("index.html", "概览"),
        ("today.html", "今天就做"),
        ("dashboard.html", "可视化清单"),
        ("memo.html", "决策备忘录"),
        ("reports.html", "完整论据"),
    ]
    nav_html = "\n".join(
        f'<a href="{href}"' + (' class="active"' if current == href else "") + f">{label}</a>"
        for href, label in nav_items
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(title)}</title>
<style>{GITHUB_CSS}</style>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <span class="nav-brand">📊 Portfolio Review · 2026-05-10</span>
    {nav_html}
  </div>
</nav>
<main class="container">
{body}
<div class="footer">由 TradingAgents multi-agent 框架生成 · gpt-5.4-mini · 数据日期 2026-05-08</div>
</main>
</body>
</html>"""


def render_markdown(md_text: str) -> str:
    """MD → HTML with GitHub-style extensions."""
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return md.convert(md_text)


def parse_money(s: str) -> float:
    if not s:
        return 0.0
    s = s.replace("$", "").replace(",", "").replace("+", "").strip()
    if not s or s == "--":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def build_index(out_dir: Path, plan_csv: Path, summary_csvs: list[Path]) -> None:
    """Top-level dashboard: stat cards + nav."""
    # Aggregate stats from plan
    sells_taxdef, sells_tax, buys = 0.0, 0.0, 0.0
    n_sells, n_buys = 0, 0
    if plan_csv.exists():
        with open(plan_csv) as f:
            for row in csv.DictReader(f):
                v = parse_money(row["delta_value"])
                if v < 0:
                    if row["type"] in ("TaxDeferred", "Roth"):
                        sells_taxdef += v
                    else:
                        sells_tax += v
                    n_sells += 1
                else:
                    buys += v
                    n_buys += 1

    # Aggregate ratings from all summary CSVs
    ratings: dict[str, int] = {}
    for sc in summary_csvs:
        if not sc.exists(): continue
        with open(sc) as f:
            for row in csv.DictReader(f):
                ratings[row["rating"]] = ratings.get(row["rating"], 0) + 1
    n_total = sum(ratings.values())

    rating_order = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    rating_chips = " ".join(
        f'<span class="rating rating-{r.lower()}">{r}: {ratings.get(r, 0)}</span>'
        for r in rating_order if ratings.get(r, 0)
    )

    body = f"""
<h1>Portfolio Review</h1>
<p style="color: var(--color-fg-muted); margin-top: -16px; font-size: 15px;">
2026-05-10 · 18 个账户 · 55 个标的 · 总盘 $2.6M
</p>

<div class="stat-grid">
  <div class="stat-card danger">
    <p class="label">净现金释放</p>
    <p class="value">$-{abs(int(sells_taxdef + sells_tax + buys)):,}</p>
    <p class="sub">{n_sells} 笔卖出 · {n_buys} 笔加仓</p>
  </div>
  <div class="stat-card">
    <p class="label">税延 / Roth 卖出</p>
    <p class="value">$-{abs(int(sells_taxdef)):,}</p>
    <p class="sub">零税成本，先做这些</p>
  </div>
  <div class="stat-card">
    <p class="label">应税卖出</p>
    <p class="value">$-{abs(int(sells_tax)):,}</p>
    <p class="sub">需算资本利得税</p>
  </div>
  <div class="stat-card success">
    <p class="label">税延加仓</p>
    <p class="value">$+{int(buys):,}</p>
    <p class="sub">LLY · APP · NVDA · SNOW · FBTC</p>
  </div>
</div>

<h2>34 只标的评级分布</h2>
<p>{rating_chips}</p>

<h2>开始的地方</h2>
<ul>
  <li><strong><a href="today.html">今天就做</a></strong> — 6 个立即执行步骤（CRWV 全清、QQQ 大减仓、加仓 LLY/APP/NVDA/SNOW、清小仓）</li>
  <li><a href="dashboard.html">可视化清单</a> — 44 笔动作按账户/主题分组，红绿色区分</li>
  <li><a href="memo.html">决策备忘录</a> — 完整 4 阶段执行计划 + 税务计算</li>
  <li><a href="reports.html">完整论据</a> — 34 只标的的 PM 详细推理（中文）</li>
</ul>

<h2>关键判断回顾</h2>
<ul>
  <li><strong>QQQ Underweight</strong>：占组合 23.9% 集中度过高，11 个账户全部减 20-35%。最大单一动作。</li>
  <li><strong>CRWV 是唯一 Sell</strong>：基本面恶化（负 FCF + 高杠杆 + 依赖融资），6 个账户全清。</li>
  <li><strong>LLY 加仓最多</strong>：4 个税延账户各 +10%。GLP-1 主题 + 趋势健康 + 估值合理。</li>
  <li><strong>SNOW 是唯一新加仓</strong>（小仓位 → Overweight）：38% 浮盈下仍加 25%，因为占比仅 0.13% 有空间。</li>
  <li><strong>NBIS Hold vs CRWV Sell</strong>：同主题不同评级，差别在资产负债表。</li>
</ul>

<div class="warning-box">
<strong>⚠️ 执行前必做的 2 件事</strong><br>
1. 打电话给 Fidelity 询问 CMA-Edge 账户中 QQQ 的实际 cost basis（CSV 显示 $0 不可信）<br>
2. 核实 AMD 在 Joint money market 两笔大幅浮盈的持有期（&gt;1 年享长期资本利得税率）
</div>
"""
    (out_dir / "index.html").write_text(_shell("Portfolio Review", body, "index.html"), encoding="utf-8")


def build_dashboard(out_dir: Path, plan_csv: Path) -> None:
    """Visual dashboard: actions table grouped by ticker, color-coded."""
    if not plan_csv.exists():
        return

    rows = []
    with open(plan_csv) as f:
        for row in csv.DictReader(f):
            rows.append(row)

    # Group by ticker
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    # Sort tickers by total |delta_value|
    sorted_tickers = sorted(
        by_ticker,
        key=lambda t: -sum(abs(parse_money(r["delta_value"])) for r in by_ticker[t]),
    )

    sections = []
    for ticker in sorted_tickers:
        actions = by_ticker[ticker]
        total = sum(parse_money(r["delta_value"]) for r in actions)
        direction = "卖出" if total < 0 else "加仓"
        color_class = "danger" if total < 0 else "success"

        action_rows_html = []
        for r in actions:
            action = r["action"]
            cls = f"action-{action.lower()}"
            type_tag = f'<span class="tag tag-{r["type"].lower()}">{r["type"]}</span>'
            dv = parse_money(r["delta_value"])
            ds = r["delta_shares"]
            sign = "-" if dv < 0 else "+"
            action_rows_html.append(
                f'<tr class="{cls}">'
                f'<td><strong>{action}</strong></td>'
                f'<td>{html.escape(r["account"])}</td>'
                f'<td>{type_tag}</td>'
                f'<td style="text-align: right;">{r["pct"]}%</td>'
                f'<td style="text-align: right; font-variant-numeric: tabular-nums;">{html.escape(ds)}</td>'
                f'<td style="text-align: right; font-variant-numeric: tabular-nums; font-weight: 600;">'
                f'{sign}${abs(int(dv)):,}</td>'
                f'</tr>'
            )

        sections.append(f"""
<h3>
  <span class="rating rating-{('sell' if total < 0 else 'buy')}">{ticker}</span>
  <span style="font-size: 0.7em; color: var(--color-fg-muted); font-weight: 400;">
    {direction} ${abs(int(total)):,} · {len(actions)} 笔
  </span>
</h3>
<table>
<thead>
<tr><th>动作</th><th>账户</th><th>类型</th><th style="text-align:right">%</th><th style="text-align:right">Δ 股数</th><th style="text-align:right">Δ 金额</th></tr>
</thead>
<tbody>
{''.join(action_rows_html)}
</tbody>
</table>
""")

    body = f"""
<h1>可视化再平衡清单</h1>
<p style="color: var(--color-fg-muted);">{len(rows)} 笔动作 · 按标的分组 · <span style="color: var(--color-success); font-weight: 600;">绿色 = 加仓</span> · <span style="color: var(--color-danger); font-weight: 600;">红色 = 减仓 / 清仓</span></p>

<h2>账户类型图例</h2>
<p>
  <span class="tag tag-roth">Roth</span> — 税后免税增长，最珍贵席位
  <span class="tag tag-taxdeferred">TaxDeferred</span> — 税延，调仓零税成本
  <span class="tag tag-taxable">Taxable</span> — 应税，要算资本利得
  <span class="tag tag-childedu">ChildEdu</span> — 教育金，长期 + 法规限制
</p>

{''.join(sections)}
"""
    (out_dir / "dashboard.html").write_text(_shell("可视化清单", body, "dashboard.html"), encoding="utf-8")


def build_md_page(out_dir: Path, md_path: Path, html_name: str, title: str) -> None:
    """Render a single MD file to HTML."""
    if not md_path.exists():
        return
    md_text = md_path.read_text(encoding="utf-8")
    body = render_markdown(md_text)
    (out_dir / html_name).write_text(_shell(title, body, html_name), encoding="utf-8")


def build_reports_page(out_dir: Path, report_paths: Iterable[Path]) -> None:
    """Concatenate all REPORT.md into one navigable HTML."""
    paths = [p for p in report_paths if p.exists()]
    if not paths:
        return
    sections = []
    for p in paths:
        md_text = p.read_text(encoding="utf-8")
        # Add batch heading from parent dir
        batch = p.parent.name.replace("portfolio_analysis_2026-05-10_", "Batch ").replace("new_ticker_", "新增分析: ")
        sections.append(f'<h2 class="batch-header">{html.escape(batch)}</h2>')
        sections.append(render_markdown(md_text))
        sections.append('<hr>')
    body = '<h1>完整论据</h1><p style="color: var(--color-fg-muted);">所有标的的 multi-agent 详细推理（中文）</p>' + "".join(sections)
    (out_dir / "reports.html").write_text(_shell("完整论据", body, "reports.html"), encoding="utf-8")


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    outputs = project_root / "outputs"
    html_dir = outputs / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    plan_csv = outputs / "rebalance_plan_FINAL_2026-05-10.csv"
    summary_csvs = list(outputs.glob("portfolio_analysis_*/summary.csv"))
    report_paths = sorted(outputs.glob("portfolio_analysis_*/REPORT.md")) + \
                   list(outputs.glob("new_ticker_*/REPORT.md"))

    build_index(html_dir, plan_csv, summary_csvs)
    build_dashboard(html_dir, plan_csv)
    build_md_page(html_dir, outputs / "TODAY_2026-05-10.md", "today.html", "今天就做")
    build_md_page(html_dir, outputs / "DECISION_MEMO_2026-05-10.md", "memo.html", "决策备忘录")
    build_reports_page(html_dir, report_paths)

    print(f"Generated:")
    for f in sorted(html_dir.glob("*.html")):
        print(f"  {f}")
    print(f"\n打开方式: open {html_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
