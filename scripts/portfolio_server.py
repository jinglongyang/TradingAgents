"""Local web UI for full portfolio management. Writes directly to SQLite.

Three actions per holding:
- **Add** a new position (any broker including Robinhood)
- **Edit** an existing position's quantity / price / cost basis
- **Sell** (partial or full) — writes an execution row and decrements quantity

Usage:
  uv run python scripts/portfolio_server.py
  # open http://localhost:8765

All data stays on localhost. The DB is ~/.tradingagents/portfolio.db.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from fastapi import FastAPI, Form  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402

from tradingagents.portfolio.holdings import classify_account, AccountType  # noqa: E402
from tradingagents.portfolio_db import (  # noqa: E402
    add_position,
    connect,
    init_db,
    latest_snapshot_date,
    record_execution,
)


app = FastAPI(title="Portfolio Manager")


CSS = """
:root {
    --fg:#1f2328; --fg-muted:#59636e; --bg:#fff; --bg-subtle:#f6f8fa;
    --border:#d1d9e0; --accent:#0969da; --success:#1a7f37; --danger:#d1242f; --warning:#9a6700;
}
@media (prefers-color-scheme: dark) {
    :root { --fg:#e6edf3; --fg-muted:#848d97; --bg:#0d1117; --bg-subtle:#161b22;
            --border:#30363d; --accent:#2f81f7; --success:#3fb950; --danger:#f85149; --warning:#d29922; }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 20px; line-height: 1.5; }
.container { max-width: 1100px; margin: 0 auto; }
h1 { margin-top: 0; font-size: 24px; }
h2 { font-size: 16px; color: var(--fg-muted); margin: 32px 0 12px; font-weight: 500; }
.subtitle { color: var(--fg-muted); font-size: 14px; margin-bottom: 24px; }
.status { background: var(--bg-subtle); padding: 12px 16px; border-radius: 6px; margin-bottom: 20px; font-size: 13px; color: var(--fg-muted); border-left: 3px solid var(--accent); }
.toolbar { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; align-items: center; }
button, .btn { background: var(--accent); color: white; border: none; padding: 8px 16px; border-radius: 6px;
         font-size: 13px; font-weight: 500; cursor: pointer; text-decoration: none; display: inline-block; }
button.secondary, .btn.secondary { background: var(--bg-subtle); color: var(--fg); border: 1px solid var(--border); }
button.danger, .btn.danger { background: var(--danger); }
button.success, .btn.success { background: var(--success); }
button.small { padding: 4px 10px; font-size: 11px; }
button:hover, .btn:hover { opacity: 0.9; }
.msg { padding: 12px 16px; border-radius: 6px; margin: 12px 0; font-size: 14px; }
.msg.success { background: color-mix(in srgb, var(--success) 15%, transparent); color: var(--success); border-left: 3px solid var(--success); }
.msg.error { background: color-mix(in srgb, var(--danger) 15%, transparent); color: var(--danger); border-left: 3px solid var(--danger); }
.account { background: var(--bg-subtle); border-radius: 8px; padding: 16px; margin-bottom: 16px; border: 1px solid var(--border); }
.account-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.account-name { font-weight: 600; font-size: 15px; }
.account-meta { color: var(--fg-muted); font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }
th { color: var(--fg-muted); font-weight: 500; font-size: 11px; text-transform: uppercase; }
th.num, td.num { text-align: right; font-variant-numeric: tabular-nums; }
td.actions { text-align: right; white-space: nowrap; }
td.actions button { margin-left: 6px; }
.tag { display: inline-block; font-size: 10px; padding: 1px 7px; border-radius: 10px; background: var(--border); }
.tag-roth { background: color-mix(in srgb, #8250df 30%, transparent); color: #8250df; }
.tag-taxdeferred { background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }
.tag-taxable { background: color-mix(in srgb, var(--warning) 30%, transparent); color: var(--warning); }
.tag-childedu { background: color-mix(in srgb, var(--accent) 30%, transparent); color: var(--accent); }
.gain { color: var(--success); }
.loss { color: var(--danger); }
.modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 100; align-items: center; justify-content: center; padding: 20px; }
.modal-backdrop.open { display: flex; }
.modal { background: var(--bg); padding: 24px; border-radius: 8px; max-width: 480px; width: 100%; max-height: 90vh; overflow-y: auto; }
.modal h3 { margin-top: 0; }
.field { margin-bottom: 12px; }
label { display: block; font-weight: 500; margin-bottom: 4px; font-size: 13px; }
label .hint { font-weight: 400; color: var(--fg-muted); font-size: 11px; margin-left: 6px; }
input, select { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px;
                font-size: 14px; background: var(--bg); color: var(--fg); }
input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent); }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
@media (max-width: 600px) { .row { grid-template-columns: 1fr; } }
"""


JS = """
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
function openSell(sym, acctId, acctName, qty) {
    document.getElementById('sell-symbol').value = sym;
    document.getElementById('sell-account-id').value = acctId;
    document.getElementById('sell-account-name').value = acctName;
    document.getElementById('sell-current').textContent = qty + ' shares held';
    document.getElementById('sell-qty').max = qty;
    document.getElementById('sell-qty').value = qty;
    document.getElementById('sell-title').textContent = `Sell ${sym} from ${acctName}`;
    const anchor = 'acct-' + acctId.replace(/[^a-zA-Z0-9_-]/g, '_');
    const inp = document.querySelector('#sell-modal input[name="redirect_anchor"]');
    if (inp) inp.value = anchor;
    openModal('sell-modal');
}
function openAccountEdit(acctId, acctName, currentBroker, currentType, currentOwner) {
    document.getElementById('acct-edit-id').value = acctId;
    document.getElementById('acct-edit-name-input').value = acctName;
    document.getElementById('acct-edit-broker-select').value = currentBroker || 'Fidelity';
    document.getElementById('acct-edit-type-select').value = currentType || 'Taxable';
    document.getElementById('acct-edit-owner-input').value = currentOwner || 'Self';
    document.getElementById('acct-edit-title').textContent = `编辑账户: ${acctName}`;
    document.getElementById('acct-delete-id').value = acctId;
    document.getElementById('acct-edit-anchor').value = 'acct-' + acctId.replace(/[^a-zA-Z0-9_-]/g, '_');
    openModal('acct-edit-modal');
}
function deleteRow(snapshotId, symbol) {
    if (!confirm(`确认删除 ${symbol} 这一行？\\n\\n这是数据修正操作，不会记入 executions。\\n如果是卖出实仓，请用 Sell 按钮。`)) return;
    let form = document.createElement('form');
    form.method = 'POST';
    form.action = '/delete-row';
    let inp = document.createElement('input');
    inp.name = 'snapshot_id';
    inp.value = snapshotId;
    form.appendChild(inp);
    document.body.appendChild(form);
    form.submit();
}
function openAddToAccount(acctId, acctName, broker, accType, owner) {
    // Open the add-modal pre-filled with this account's metadata
    document.querySelector('#add-modal input[name="account_id"]').value = acctId;
    document.querySelector('#add-modal input[name="account_name"]').value = acctName;
    document.querySelector('#add-modal input[name="owner"]').value = owner || 'Self';
    document.querySelector('#add-modal select[name="account_type"]').value = accType || 'Taxable';
    document.querySelector('#add-modal select[name="broker"]').value = broker || 'Fidelity';
    // Tell the backend to redirect back to this account's anchor after add
    let anchor = 'acct-' + acctId.replace(/[^a-zA-Z0-9_-]/g, '_');
    let inp = document.querySelector('#add-modal input[name="redirect_anchor"]');
    if (inp) inp.value = anchor;
    document.querySelector('#add-modal input[name="symbol"]').focus();
    openModal('add-modal');
}
function currentAnchor(snapshotId) {
    // Walk up to the surrounding .account div and return its anchor id
    const row = document.querySelector(`tr button[onclick*="${snapshotId}"]`);
    if (!row) return '';
    const acctDiv = row.closest('.account')?.previousElementSibling
                  || row.closest('.account')?.querySelector('[id^="acct-"]');
    // fallback: find any [id^="acct-"] preceding the row
    let el = row.closest('tr');
    while (el) {
        let prev = el.previousElementSibling;
        while (prev) {
            if (prev.id && prev.id.startsWith('acct-')) return prev.id;
            const inner = prev.querySelector?.('[id^="acct-"]');
            if (inner) return inner.id;
            prev = prev.previousElementSibling;
        }
        el = el.parentElement;
    }
    return '';
}
function openEdit(snapshotId, sym, qty, price, cost, broker) {
    document.getElementById('edit-snapshot-id').value = snapshotId;
    document.getElementById('edit-symbol').value = sym;
    document.getElementById('edit-qty').value = qty;
    document.getElementById('edit-price').value = price;
    document.getElementById('edit-cost').value = cost;
    // Pre-fill avg cost (cost / qty)
    document.getElementById('edit-avg-cost').value = qty > 0 ? (cost / qty).toFixed(4) : '';
    document.getElementById('edit-broker').value = broker || 'Fidelity';
    document.getElementById('edit-title').textContent = `Edit ${sym}`;
    const anchorInp = document.querySelector('#edit-modal input[name="redirect_anchor"]');
    if (anchorInp) anchorInp.value = currentAnchor(snapshotId);
    openModal('edit-modal');
}
"""


def _build_holdings_view():
    today = date.today().isoformat()
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM positions_snapshot
            WHERE import_date = ?
            ORDER BY account_name, symbol
            """,
            (latest,) if latest else (today,),
        ).fetchall()

    if not rows:
        return '<p style="color:var(--fg-muted);">DB 暂时为空 — 点 "添加持仓" 开始</p>', 0, 0.0

    # Group by account
    by_account: dict[tuple[str, str], list] = {}
    for r in rows:
        key = (r["account_id"], r["account_name"])
        by_account.setdefault(key, []).append(r)

    out = []
    total = 0.0
    for (aid, aname), items in sorted(by_account.items(), key=lambda kv: -sum(r["current_value"] for r in kv[1])):
        sub = sum(r["current_value"] for r in items)
        total += sub
        atype = items[0]["account_type"]
        broker = items[0]["broker"] or "—"
        owner = items[0]["owner"] or "—"
        out.append(f'''
<div class="account">
  <div class="account-header">
    <div>
      <button class="small secondary" onclick="openAccountEdit('{aid}', `{aname}`, '{broker}', '{atype}', '{owner}')"
              style="margin-right:8px;padding:2px 8px;font-size:11px;">✎ 编辑账户</button>
      <span class="account-name">{aname}</span>
      <span class="tag tag-{atype.lower()}">{atype}</span>
      <span class="tag" style="margin-left:4px;font-size:10px;background:var(--bg);border:1px solid var(--border);">🏦 {broker}</span>
      <span class="tag" style="margin-left:4px;font-size:10px;background:var(--bg);border:1px solid var(--border);">👤 {owner}</span>
    </div>
    <div class="account-meta">
      {len(items)} 仓位 · ${sub:,.0f}
      <button class="small success" style="margin-left:8px;padding:3px 10px;font-size:11px;"
              onclick="openAddToAccount('{aid}', `{aname}`, '{broker}', '{atype}', '{owner}')">➕ 加 ticker</button>
    </div>
  </div>
  <div id="acct-{re.sub(r"[^a-zA-Z0-9_-]", "_", aid)}" style="scroll-margin-top:80px;"></div>
  <table>
    <thead><tr><th>Ticker</th><th class="num">持股</th><th class="num">价格</th><th class="num">价值</th><th class="num">成本</th><th class="num">P/L%</th><th></th></tr></thead>
    <tbody>''')
        for r in items:
            cost = r["cost_basis_total"] or 0
            pl_pct = ((r["current_value"] - cost) / cost * 100) if cost else 0.0
            pl_class = "gain" if pl_pct >= 0 else "loss"
            out.append(f'''
      <tr>
        <td><strong>{r['symbol']}</strong></td>
        <td class="num">{r['quantity']:.3f}</td>
        <td class="num">${r['last_price']:.2f}</td>
        <td class="num">${r['current_value']:,.0f}</td>
        <td class="num">${cost:,.0f}</td>
        <td class="num {pl_class}">{pl_pct:+.1f}%</td>
        <td class="actions">
          <button class="small secondary" onclick="openEdit({r['snapshot_id']}, '{r['symbol']}', {r['quantity']}, {r['last_price']}, {cost}, '{r['broker'] or 'Fidelity'}')">Edit</button>
          <button class="small danger" onclick="openSell('{r['symbol']}', '{aid}', '{aname}', {r['quantity']})">Sell</button>
          <button class="small" style="background:transparent;color:var(--fg-muted);border:1px solid var(--border);"
                  onclick="deleteRow({r['snapshot_id']}, '{r['symbol']}')" title="删除该行（数据错误时用，不记入交易）">🗑️</button>
        </td>
      </tr>''')
        out.append('</tbody></table></div>')
    return "\n".join(out), len(rows), total


def _render(message: str = ""):
    html_view, n_rows, total_val = _build_holdings_view()
    latest = latest_snapshot_date() or "(none)"
    today = date.today().isoformat()

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>持仓管理 — Portfolio Manager</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
  <h1>📊 持仓管理</h1>
  <p class="subtitle">本地浏览器界面 · 数据存在 ~/.tradingagents/portfolio.db · 任何时候关掉浏览器都不会丢数据</p>

  <form method="get" action="/lookup" style="margin-bottom:20px;display:flex;gap:8px;">
    <input name="q" placeholder="🔍 输入 ticker (如 NVDA, CRWV, IREN) 查看持仓 + PM 评级 + 一键触发分析"
           style="flex:1;padding:10px 14px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);font-size:14px;"
           autofocus required style="text-transform:uppercase">
    <button type="submit">查询</button>
  </form>

  <div class="status">
    最新 snapshot: <strong>{latest}</strong> · {n_rows} 仓位 · 总价值 <strong>${total_val:,.0f}</strong> · 今天: {today}
  </div>

  {message}

  <div class="toolbar">
    <button onclick="openModal('add-modal')">➕ 添加新持仓</button>
    <button class="success" onclick="openModal('run-modal')">🚀 运行 PM 分析</button>
    <form method="post" action="/update-prices" style="display:inline;"
          onsubmit="return confirm('从 yfinance 拉所有持仓 ticker 最新价格？1-2 分钟。')">
      <button class="secondary" type="submit">📡 更新价格</button>
    </form>
    <a class="btn secondary" href="/owners">👥 按 Owner 看</a>
    <a class="btn secondary" href="/drift">⚠️ Drift Alert</a>
    <a class="btn secondary" href="/tlh">💰 TLH Finder</a>
    <a class="btn secondary" href="/correlation">🔗 相关性热力</a>
    <a class="btn secondary" href="/wash-sale">🚫 Wash Sale</a>
    <a class="btn secondary" href="/lots">📦 Cost Basis Lots</a>
    <a class="btn secondary" href="/decisions">📋 PM 分析</a>
    <a class="btn secondary" href="/thesis-evolution">📈 Thesis 演变</a>
    <a class="btn secondary" href="/charts">📊 Charts</a>
    <a class="btn secondary" href="/sectors">🌐 板块轮动</a>
    <a class="btn secondary" href="/tickers">🗂️ Tickers</a>
    <a class="btn secondary" href="/performance">🎯 PM 准确度</a>
    <a class="btn secondary" href="/runs">🏃 运行历史</a>
    <a class="btn secondary" href="/executions">📒 交易记录</a>
    <a class="btn secondary" href="/api/positions" target="_blank">View JSON</a>
  </div>

  {html_view}
</div>

<!-- Add Modal -->
<div class="modal-backdrop" id="add-modal">
  <div class="modal">
    <h3>添加新持仓</h3>
    <form method="post" action="/add">
      <input type="hidden" name="redirect_anchor" value="">
      <div class="row">
        <div class="field"><label>账户 ID <span class="hint">如 RH-IND</span></label><input name="account_id" required></div>
        <div class="field"><label>账户名</label><input name="account_name" required></div>
      </div>
      <div class="field">
        <label>Owner <span class="hint">Self / Spouse / Joint / Olivia / Amelia / ...</span></label>
        <input name="owner" value="Self" required>
      </div>
      <div class="row">
        <div class="field">
          <label>账户类型</label>
          <select name="account_type">
            <option value="Taxable" selected>Taxable — 应税</option>
            <option value="Roth">Roth</option>
            <option value="TaxDeferred">TaxDeferred — 401k/IRA</option>
            <option value="ChildEdu">ChildEdu — 529</option>
          </select>
        </div>
        <div class="field">
          <label>Broker / 平台</label>
          <select name="broker">
            <option value="Fidelity">Fidelity</option>
            <option value="Robinhood" selected>Robinhood</option>
            <option value="Schwab">Schwab</option>
            <option value="E-Trade">E-Trade</option>
            <option value="Vanguard">Vanguard</option>
            <option value="Interactive Brokers">Interactive Brokers</option>
            <option value="Merrill Lynch">Merrill Lynch</option>
            <option value="TD Ameritrade">TD Ameritrade</option>
            <option value="Other">Other</option>
          </select>
        </div>
      </div>
      <div class="row">
        <div class="field"><label>Ticker</label><input name="symbol" required style="text-transform: uppercase"></div>
        <div class="field"><label>持股数</label><input name="quantity" type="number" step="0.001" required min="0.001"></div>
      </div>
      <div class="row">
        <div class="field"><label>当前价 <span class="hint">每股</span></label><input name="last_price" type="number" step="0.01"></div>
        <div class="field"><label>每股成本 <span class="hint">Robinhood 显示这个</span></label>
          <input name="avg_cost" type="number" step="0.01" id="add-avg-cost"
                 oninput="document.getElementById('add-cost-total').value = (this.value * document.querySelector('#add-modal input[name=\\'quantity\\']').value || 0).toFixed(2)"></div>
      </div>
      <div class="field"><label>总成本 <span class="hint">= 每股成本 × 股数，自动算；也可手填</span></label>
        <input id="add-cost-total" name="cost_basis_total" type="number" step="0.01"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('add-modal')">取消</button>
        <button type="submit" class="success">添加</button>
      </div>
    </form>
  </div>
</div>

<!-- Sell Modal -->
<div class="modal-backdrop" id="sell-modal">
  <div class="modal">
    <h3 id="sell-title">Sell</h3>
    <form method="post" action="/sell">
      <input type="hidden" name="redirect_anchor" value="">
      <input type="hidden" id="sell-symbol" name="symbol">
      <input type="hidden" id="sell-account-id" name="account_id">
      <input type="hidden" id="sell-account-name" name="account_name">
      <div class="field" style="color: var(--fg-muted); font-size: 13px;" id="sell-current"></div>
      <div class="row">
        <div class="field"><label>卖出股数</label><input id="sell-qty" name="shares" type="number" step="0.001" required min="0.001"></div>
        <div class="field"><label>成交价 <span class="hint">每股</span></label><input name="price" type="number" step="0.01" required min="0"></div>
      </div>
      <div class="field"><label>备注 <span class="hint">可选</span></label><input name="note" placeholder="如 PM Phase 1 / TLH harvest"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('sell-modal')">取消</button>
        <button type="submit" class="danger">确认卖出</button>
      </div>
    </form>
  </div>
</div>

<!-- Run Analysis Modal -->
<div class="modal-backdrop" id="run-modal">
  <div class="modal" style="max-width:560px;">
    <h3>🚀 运行 PM 分析</h3>
    <form method="post" action="/run">
      <div class="field">
        <label>分析类型</label>
        <select name="mode" onchange="toggleRunMode(this.value)">
          <option value="tickers" selected>📊 Ticker 列表分析（持仓 / 新股票 / 单只）</option>
          <option value="sector">🏭 行业分析（LLM 推荐 ticker + 逐个分析）</option>
          <option value="all">📈 全部持仓重跑（所有持仓 ticker）</option>
        </select>
      </div>

      <div class="field" id="run-tickers-field">
        <label>Ticker(s) <span class="hint">逗号分隔，如 NVDA,CRWV 或 IREN（持仓 ticker 带账户上下文，新 ticker 用探索性分析）</span></label>
        <input name="tickers" placeholder="NVDA,CRWV 或 IREN">
      </div>

      <div class="field" id="run-sector-field" style="display:none;">
        <label>行业 / 主题描述 <span class="hint">中文 OK，如 "AI 算力" / "GLP-1 减肥药" / "半导体周期"</span></label>
        <input name="sector" placeholder="AI infrastructure 或 GLP-1 减肥药">
        <p style="margin-top:6px;font-size:12px;color:var(--fg-muted);">系统会让 LLM 列 5-10 个该行业的代表性 ticker，逐个跑分析，最后给推荐 / 不推荐汇总。</p>
      </div>

      <div class="field">
        <label>额外指令 <span class="hint">可选，注入到 PM prompt（如税务约束 / 时间窗口）</span></label>
        <textarea name="instruction" rows="3" style="width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);font-family:inherit;font-size:14px;resize:vertical;" placeholder="例如：我下个月房产购买，避免 $5K+ 资本利得；优先 TLH"></textarea>
      </div>

      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('run-modal')">取消</button>
        <button type="submit" class="success">🚀 启动分析</button>
      </div>
    </form>
  </div>
</div>

<script>
function toggleRunMode(mode) {{
    document.getElementById('run-tickers-field').style.display = mode === 'tickers' ? '' : 'none';
    document.getElementById('run-sector-field').style.display = mode === 'sector' ? '' : 'none';
}}
</script>

<!-- Account-level Edit Modal -->
<div class="modal-backdrop" id="acct-edit-modal">
  <div class="modal">
    <h3 id="acct-edit-title">编辑账户</h3>
    <p style="color:var(--fg-muted);font-size:13px;">修改会应用到本账户的所有持仓行（按 account_id 匹配）</p>
    <form method="post" action="/account-edit">
      <input type="hidden" name="redirect_anchor" id="acct-edit-anchor" value="">
      <input type="hidden" id="acct-edit-id" name="account_id">
      <div class="field">
        <label>账户名 <span class="hint">如 "Merrill CMA" / "Robinhood 个人户"</span></label>
        <input id="acct-edit-name-input" name="account_name" required>
      </div>
      <div class="field">
        <label>Owner / 所有者 <span class="hint">如 Self / Spouse / Joint / Olivia / Amelia</span></label>
        <input id="acct-edit-owner-input" name="owner" placeholder="Self / Spouse / Joint / Child name" required>
      </div>
      <div class="row">
        <div class="field">
          <label>账户类型</label>
          <select id="acct-edit-type-select" name="account_type">
            <option value="Taxable">Taxable — 应税</option>
            <option value="Roth">Roth</option>
            <option value="TaxDeferred">TaxDeferred — 401k/IRA</option>
            <option value="ChildEdu">ChildEdu — 529</option>
            <option value="Unknown">Unknown</option>
          </select>
        </div>
        <div class="field">
          <label>Broker / 平台</label>
          <select id="acct-edit-broker-select" name="broker">
            <option value="Fidelity">Fidelity</option>
            <option value="Robinhood">Robinhood</option>
            <option value="Schwab">Schwab</option>
            <option value="E-Trade">E-Trade</option>
            <option value="Vanguard">Vanguard</option>
            <option value="Interactive Brokers">Interactive Brokers</option>
            <option value="Merrill Lynch">Merrill Lynch</option>
            <option value="TD Ameritrade">TD Ameritrade</option>
            <option value="Other">Other</option>
          </select>
        </div>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('acct-edit-modal')">取消</button>
        <button type="submit">应用到所有行</button>
      </div>
    </form>
    <hr style="margin:20px 0;border:none;border-top:1px solid var(--border);">
    <details>
      <summary style="cursor:pointer;color:var(--danger);font-size:13px;">⚠️ 危险区域 — 删除整个账户</summary>
      <p style="font-size:12px;color:var(--fg-muted);margin-top:10px;">删除该账户的所有持仓行。<strong>不影响已记录的交易（executions）</strong>。如果是 CSV import 进来的，下次 import-csv 会重新添加。</p>
      <form method="post" action="/account-delete" onsubmit="return confirm('确认删除此账户的所有持仓行吗？此操作只删持仓，不影响交易记录。')" style="margin-top:8px;">
        <input type="hidden" id="acct-delete-id" name="account_id">
        <button type="submit" class="danger" style="width:100%;">🗑️ 删除整个账户</button>
      </form>
    </details>
  </div>
</div>

<!-- Edit Modal -->
<div class="modal-backdrop" id="edit-modal">
  <div class="modal">
    <h3 id="edit-title">Edit</h3>
    <form method="post" action="/edit">
      <input type="hidden" name="redirect_anchor" value="">
      <input type="hidden" id="edit-snapshot-id" name="snapshot_id">
      <div class="field"><label>Ticker <span class="hint">readonly</span></label><input id="edit-symbol" readonly style="background:var(--bg-subtle);"></div>
      <div class="row">
        <div class="field"><label>持股数</label>
          <input id="edit-qty" name="quantity" type="number" step="0.001" required
                 oninput="const ac=document.getElementById('edit-avg-cost'); if(ac.value) document.getElementById('edit-cost').value = (ac.value * this.value || 0).toFixed(2)"></div>
        <div class="field"><label>当前价</label><input id="edit-price" name="last_price" type="number" step="0.01"></div>
      </div>
      <div class="row">
        <div class="field"><label>每股成本 <span class="hint">Robinhood 显示这个</span></label>
          <input id="edit-avg-cost" type="number" step="0.01"
                 oninput="document.getElementById('edit-cost').value = (this.value * document.getElementById('edit-qty').value || 0).toFixed(2)"></div>
        <div class="field"><label>总成本 <span class="hint">自动算或手填</span></label>
          <input id="edit-cost" name="cost_basis_total" type="number" step="0.01"></div>
      </div>
      <div class="field">
        <label>Broker / 平台</label>
        <select id="edit-broker" name="broker">
          <option value="Fidelity">Fidelity</option>
          <option value="Robinhood">Robinhood</option>
          <option value="Schwab">Schwab</option>
          <option value="E-Trade">E-Trade</option>
          <option value="Vanguard">Vanguard</option>
          <option value="Interactive Brokers">Interactive Brokers</option>
          <option value="Merrill Lynch">Merrill Lynch</option>
          <option value="TD Ameritrade">TD Ameritrade</option>
          <option value="Other">Other</option>
        </select>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('edit-modal')">取消</button>
        <button type="submit">保存</button>
      </div>
    </form>
  </div>
</div>

<script>{JS}</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index(msg: str = ""):
    init_db()
    flash = f'<div class="msg success">{msg}</div>' if msg else ""
    return _render(message=flash)


@app.post("/add")
def add(
    account_id: str = Form(...),
    account_name: str = Form(...),
    account_type: str = Form("Taxable"),
    broker: str = Form("Robinhood"),
    owner: str = Form("Self"),
    symbol: str = Form(...),
    quantity: float = Form(...),
    last_price: Optional[float] = Form(None),
    cost_basis_total: Optional[float] = Form(None),
    redirect_anchor: str = Form(""),
):
    try:
        snap_id = add_position(
            account_id=account_id.strip(), account_name=account_name.strip(),
            account_type=account_type, symbol=symbol.strip().upper(),
            quantity=quantity, last_price=last_price or 0.0,
            cost_basis_total=cost_basis_total or 0.0,
            broker=broker,
        )
        # add_position returns count, but we need to set owner too. Update by account_id.
        with connect() as conn:
            conn.execute(
                "UPDATE positions_snapshot SET owner = ? WHERE account_id = ? AND owner IS NULL",
                (owner.strip(), account_id.strip()),
            )
        # Always derive anchor from account_id (ignore any stale redirect_anchor
        # from a recycled modal — JS reuses one form element across accounts).
        anchor = "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", account_id.strip())
        return RedirectResponse(url=f"/#{anchor}", status_code=303)
        msg = f'<div class="msg success">✓ 已添加 <strong>{symbol.upper()}</strong> 到 {account_name}</div>'
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


@app.post("/delete-row")
def delete_row(snapshot_id: int = Form(...)):
    """Hard-delete a single positions_snapshot row. Use for data correction
    (e.g. duplicate import, wrong ticker). Does NOT record an execution."""
    with connect() as conn:
        row = conn.execute(
            "SELECT symbol, account_id FROM positions_snapshot WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if not row:
            return _render(message='<div class="msg error">行不存在</div>')
        conn.execute("DELETE FROM positions_snapshot WHERE snapshot_id = ?", (snapshot_id,))
        anchor = "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", row["account_id"])
    return RedirectResponse(url=f"/#{anchor}", status_code=303)


@app.post("/update-prices")
def update_prices():
    """Refresh prices in the canonical tickers table + sync to positions cache."""
    import yfinance as _yf
    from urllib.parse import quote as _quote
    from datetime import datetime as _dt

    latest = latest_snapshot_date()
    if not latest:
        return RedirectResponse(url="/?msg=" + _quote("DB 为空，没数据可更新"), status_code=303)

    with connect() as conn:
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM positions_snapshot WHERE import_date = ?",
            (latest,),
        ).fetchall()]

    if not symbols:
        return RedirectResponse(url="/?msg=" + _quote("没有 ticker 可更新"), status_code=303)

    # Batch fetch via yfinance
    try:
        data = _yf.download(symbols, period="5d", progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return RedirectResponse(url="/?msg=" + _quote(f"yfinance 错误: {e}"), status_code=303)

    import pandas as _pd
    if isinstance(data, _pd.Series):
        data = data.to_frame()

    updated_rows = 0
    tickers_written = 0
    missing = []
    now_iso = _dt.now().isoformat(timespec="seconds")
    with connect() as conn:
        for symbol in symbols:
            if symbol not in data.columns:
                try:
                    hist = _yf.Ticker(symbol).history(period="5d")
                    if len(hist) == 0:
                        missing.append(symbol)
                        continue
                    price = float(hist["Close"].iloc[-1])
                except Exception:
                    missing.append(symbol)
                    continue
            else:
                ser = data[symbol].dropna()
                if len(ser) == 0:
                    missing.append(symbol)
                    continue
                price = float(ser.iloc[-1])

            # 1. Upsert canonical price in tickers (single source of truth)
            conn.execute(
                """
                INSERT INTO tickers (symbol, last_price, last_updated)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    last_price = excluded.last_price,
                    last_updated = excluded.last_updated
                """,
                (symbol, price, now_iso),
            )
            tickers_written += 1

            # 2. Sync to positions_snapshot cache (backwards compat for existing SELECTs)
            cur = conn.execute(
                """
                UPDATE positions_snapshot
                SET last_price = ?, current_value = ? * quantity
                WHERE import_date = ? AND symbol = ?
                """,
                (price, price, latest, symbol),
            )
            updated_rows += cur.rowcount

    updated = updated_rows

    msg = f"✓ {tickers_written} ticker 写入 canonical tickers 表 · {updated} 行 positions sync"
    if missing:
        msg += f" · 拉取失败: {', '.join(missing[:10])}"
    return RedirectResponse(url="/?msg=" + _quote(msg), status_code=303)


@app.post("/account-delete", response_class=HTMLResponse)
def account_delete(account_id: str = Form(...)):
    """Delete all positions_snapshot rows for an account (preserves executions)."""
    try:
        with connect() as conn:
            cur = conn.execute(
                "DELETE FROM positions_snapshot WHERE account_id = ?",
                (account_id,),
            )
            n = cur.rowcount
        msg = f'<div class="msg success">✓ 已删除账户 <code>{account_id[:24]}</code> 的 <strong>{n}</strong> 行持仓（executions 保留）</div>'
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


@app.post("/account-edit")
def account_edit(
    account_id: str = Form(...),
    account_name: str = Form(...),
    account_type: str = Form(...),
    broker: str = Form(...),
    owner: str = Form(...),
    redirect_anchor: str = Form(""),
):
    """Update account_name, account_type, broker, owner on every row of this account."""
    try:
        with connect() as conn:
            cur = conn.execute(
                """
                UPDATE positions_snapshot
                SET account_name = ?, account_type = ?, broker = ?, owner = ?
                WHERE account_id = ?
                """,
                (account_name.strip(), account_type, broker, owner.strip(), account_id),
            )
            n = cur.rowcount
        anchor = "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", account_id)
        return RedirectResponse(url=f"/#{anchor}", status_code=303)
    except Exception as e:  # noqa: BLE001
        return _render(message=f'<div class="msg error">✗ 错误: {e}</div>')


@app.post("/edit")
def edit(
    snapshot_id: int = Form(...),
    quantity: float = Form(...),
    last_price: Optional[float] = Form(None),
    cost_basis_total: Optional[float] = Form(None),
    broker: Optional[str] = Form(None),
    redirect_anchor: str = Form(""),
):
    try:
        price = last_price or 0.0
        cost = cost_basis_total or 0.0
        with connect() as conn:
            conn.execute(
                """
                UPDATE positions_snapshot
                SET quantity = ?, last_price = ?,
                    current_value = ?, cost_basis_total = ?,
                    avg_cost = ?, broker = COALESCE(?, broker)
                WHERE snapshot_id = ?
                """,
                (quantity, price, price * quantity if price else cost,
                 cost, cost / quantity if quantity else 0.0, broker, snapshot_id),
            )
            row = conn.execute("SELECT account_id FROM positions_snapshot WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        # Always derive from account_id, ignore stale redirect_anchor
        anchor = ("acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", row["account_id"])) if row else ""
        return RedirectResponse(url=f"/#{anchor}" if anchor else "/", status_code=303)
    except Exception as e:  # noqa: BLE001
        return _render(message=f'<div class="msg error">✗ 错误: {e}</div>')


@app.post("/sell")
def sell(
    symbol: str = Form(...),
    account_id: str = Form(...),
    account_name: str = Form(...),
    shares: float = Form(...),
    price: float = Form(...),
    note: Optional[str] = Form(None),
    redirect_anchor: str = Form(""),
):
    try:
        # Record execution
        record_execution(
            trade_date=date.today().isoformat(),
            account_id=account_id, account_name=account_name, symbol=symbol,
            action="SELL", shares=shares, price=price, note=note,
        )
        # Decrement quantity in latest snapshot
        latest = latest_snapshot_date()
        with connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, quantity FROM positions_snapshot
                WHERE import_date = ? AND account_id = ? AND symbol = ?
                """,
                (latest, account_id, symbol),
            ).fetchone()
            if row:
                new_qty = row["quantity"] - shares
                if new_qty <= 0.001:
                    conn.execute(
                        "DELETE FROM positions_snapshot WHERE snapshot_id = ?",
                        (row["snapshot_id"],),
                    )
                else:
                    new_value = new_qty * price
                    conn.execute(
                        """
                        UPDATE positions_snapshot
                        SET quantity = ?, current_value = ?, last_price = ?
                        WHERE snapshot_id = ?
                        """,
                        (new_qty, new_value, price, row["snapshot_id"]),
                    )

        anchor = "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", account_id)
        return RedirectResponse(url=f"/#{anchor}", status_code=303)
    except Exception as e:  # noqa: BLE001
        return _render(message=f'<div class="msg error">✗ 错误: {e}</div>')


@app.get("/lookup", response_class=HTMLResponse)
def lookup_view(q: str):
    """Unified ticker view: positions across all accounts + latest PM decision."""
    import json as _json
    ticker = q.strip().upper()
    if not ticker:
        return RedirectResponse(url="/", status_code=303)

    latest = latest_snapshot_date()
    with connect() as conn:
        pos_rows = conn.execute(
            """
            SELECT * FROM positions_snapshot
            WHERE symbol = ? AND import_date = ?
            ORDER BY current_value DESC
            """,
            (ticker, latest) if latest else (ticker, date.today().isoformat()),
        ).fetchall()
        decision = conn.execute(
            "SELECT * FROM decisions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        executions = conn.execute(
            "SELECT * FROM executions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 10",
            (ticker,),
        ).fetchall()
        lots = conn.execute(
            "SELECT * FROM cost_basis_lots WHERE symbol = ? ORDER BY purchase_date DESC",
            (ticker,),
        ).fetchall()

    total_value = sum(r["current_value"] for r in pos_rows) if pos_rows else 0
    total_cost = sum(r["cost_basis_total"] or 0 for r in pos_rows) if pos_rows else 0
    total_shares = sum(r["quantity"] for r in pos_rows) if pos_rows else 0
    pl = total_value - total_cost if total_cost else 0
    pl_pct = pl / total_cost * 100 if total_cost else 0

    body = [f'<h1>🔍 {ticker}</h1>']
    body.append('<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>')

    # Header summary
    if pos_rows:
        pl_class = "gain" if pl >= 0 else "loss"
        body.append(f'''
<div class="status" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;">
  <div><div style="color:var(--fg-muted);font-size:12px;">总价值</div><strong style="font-size:18px;">${total_value:,.0f}</strong></div>
  <div><div style="color:var(--fg-muted);font-size:12px;">总成本</div><strong style="font-size:18px;">${total_cost:,.0f}</strong></div>
  <div><div style="color:var(--fg-muted);font-size:12px;">P/L</div><strong class="{pl_class}" style="font-size:18px;">{pl_pct:+.1f}% (${pl:+,.0f})</strong></div>
  <div><div style="color:var(--fg-muted);font-size:12px;">合计股数</div><strong style="font-size:18px;">{total_shares:,.3f}</strong></div>
  <div><div style="color:var(--fg-muted);font-size:12px;">账户数</div><strong style="font-size:18px;">{len(pos_rows)}</strong></div>
</div>
''')
    else:
        body.append('<div class="msg">⚠️ <strong>未持仓</strong> — 这是探索性查询。可以触发新股票分析。</div>')

    # PM decision
    if decision:
        rating = decision["rating"]
        actions_json = decision["account_actions"] or "[]"
        n_actions = len(_json.loads(actions_json)) if actions_json else 0
        body.append(f'''
<h2 style="margin-top:24px;">📋 PM 评级</h2>
<div class="status" style="border-left:3px solid var(--accent);">
  <span class="tag tag-{rating.lower()}" style="padding:4px 12px;font-size:14px;">{rating}</span>
  · 分析日期 {decision["trade_date"]} · {n_actions} 账户级动作 ·
  <a href="/decisions/{ticker}">查看完整 thesis →</a>
</div>
''')
    else:
        body.append(f'''
<h2 style="margin-top:24px;">📋 PM 评级</h2>
<p style="color:var(--fg-muted);">⏳ 还没分析过 — 用下方按钮触发。</p>
''')

    # Quick-trigger
    body.append(f'''
<h2 style="margin-top:24px;">🚀 触发 PM 分析</h2>
<form method="post" action="/run" style="display:flex;gap:8px;align-items:flex-start;">
  <input type="hidden" name="mode" value="tickers">
  <input type="hidden" name="tickers" value="{ticker}">
  <input name="instruction" placeholder="可选指令（如：避免 $5K+ 资本利得）"
         style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;background:var(--bg);color:var(--fg);">
  <button type="submit" class="success">🚀 启动</button>
</form>
''')

    # Positions table
    if pos_rows:
        body.append('<h2 style="margin-top:32px;">📊 跨账户持仓</h2>')
        body.append('<table><thead><tr><th>账户</th><th>类型</th><th>Broker</th><th>Owner</th><th class="num">股数</th><th class="num">价值</th><th class="num">成本</th><th class="num">P/L%</th></tr></thead><tbody>')
        for r in pos_rows:
            c = r["cost_basis_total"] or 0
            ppct = (r["current_value"] - c) / c * 100 if c else 0
            pc = "gain" if ppct >= 0 else "loss"
            body.append(
                f'<tr><td>{r["account_name"]}</td>'
                f'<td><span class="tag tag-{r["account_type"].lower()}">{r["account_type"]}</span></td>'
                f'<td>🏦 {r["broker"] or "—"}</td>'
                f'<td>👤 {r["owner"] or "—"}</td>'
                f'<td class="num">{r["quantity"]:.3f}</td>'
                f'<td class="num">${r["current_value"]:,.0f}</td>'
                f'<td class="num">${c:,.0f}</td>'
                f'<td class="num {pc}">{ppct:+.1f}%</td>'
                f'</tr>'
            )
        body.append('</tbody></table>')

    # Executions
    if executions:
        body.append('<h2 style="margin-top:32px;">📒 最近交易（最多 10 笔）</h2>')
        body.append('<table><thead><tr><th>日期</th><th>动作</th><th class="num">股数</th><th class="num">价格</th><th>账户</th><th>备注</th></tr></thead><tbody>')
        for e in executions:
            body.append(
                f'<tr><td>{e["trade_date"]}</td>'
                f'<td><span class="tag" style="background:var(--{"danger" if e["action"]=="SELL" else "success"});color:white;padding:2px 8px;">{e["action"]}</span></td>'
                f'<td class="num">{e["shares"]:.3f}</td>'
                f'<td class="num">${e["price"]:.2f}</td>'
                f'<td>{e["account_name"]}</td>'
                f'<td style="font-size:12px;color:var(--fg-muted);">{e["note"] or ""}</td></tr>'
            )
        body.append('</tbody></table>')

    # Lots
    if lots:
        body.append(f'<h2 style="margin-top:32px;">📦 Cost Basis Lots ({len(lots)})</h2>')
        body.append('<p style="color:var(--fg-muted);font-size:13px;"><a href="/lots?symbol={ticker}">查看完整 lot 列表 →</a></p>')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ticker} — 综合视图</title><style>{CSS}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/lots", response_class=HTMLResponse)
def lots_view(symbol: str = ""):
    """Cost-basis lot ledger — each purchase tracked individually."""
    with connect() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM cost_basis_lots WHERE symbol = ? ORDER BY purchase_date",
                (symbol.upper(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM cost_basis_lots ORDER BY purchase_date DESC LIMIT 200"
            ).fetchall()

    body = ['<h1>📦 Cost Basis Lots</h1>',
            '<p class="subtitle">每笔买入独立追踪 → 支持 FIFO / LIFO / specific-lot 卖出策略</p>',
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a>',
            f' <button onclick="openModal(\'add-lot-modal\')">➕ 添加 Lot</button></div>']

    if not rows:
        body.append('<p style="color:var(--fg-muted);padding:20px;background:var(--bg-subtle);border-radius:8px;">还没有 lot 记录。点 "添加 Lot" 录入买入历史；以后所有买入都会出现在这里。</p>')
    else:
        # Group by symbol
        from collections import defaultdict
        by_sym = defaultdict(list)
        for r in rows:
            by_sym[r["symbol"]].append(r)

        today = date.today()
        for sym in sorted(by_sym):
            lots = by_sym[sym]
            total_shares = sum(l["shares"] for l in lots)
            total_cost = sum(l["shares"] * l["cost_per_share"] for l in lots)
            body.append(f'<h3 style="margin-top:24px;">{sym} · {total_shares:.3f} 股 · 总成本 ${total_cost:,.0f}</h3>')
            body.append('<table><thead><tr><th>买入日期</th><th>持有天数</th><th>持有期</th><th>账户</th><th class="num">股数</th><th class="num">单价</th><th class="num">小计</th><th>备注</th></tr></thead><tbody>')
            for l in lots:
                purchase = datetime.strptime(l["purchase_date"], "%Y-%m-%d").date()
                days = (today - purchase).days
                term = "长期" if days > 365 else f"短期 ({365 - days} 天后变长期)"
                cost = l["shares"] * l["cost_per_share"]
                body.append(
                    f'<tr><td>{l["purchase_date"]}</td>'
                    f'<td class="num">{days}</td>'
                    f'<td>{term}</td>'
                    f'<td>{l["account_name"]}</td>'
                    f'<td class="num">{l["shares"]:.3f}</td>'
                    f'<td class="num">${l["cost_per_share"]:.2f}</td>'
                    f'<td class="num">${cost:,.0f}</td>'
                    f'<td style="font-size:12px;color:var(--fg-muted);">{l["note"] or ""}</td></tr>'
                )
            body.append('</tbody></table>')

    add_modal = """
<div class="modal-backdrop" id="add-lot-modal">
  <div class="modal">
    <h3>添加 Cost Basis Lot</h3>
    <form method="post" action="/lots/add">
      <div class="row">
        <div class="field"><label>买入日期</label><input name="purchase_date" type="date" required></div>
        <div class="field"><label>Ticker</label><input name="symbol" required style="text-transform:uppercase"></div>
      </div>
      <div class="row">
        <div class="field"><label>账户名</label><input name="account_name" required></div>
        <div class="field"><label>Account ID</label><input name="account_id" required></div>
      </div>
      <div class="row">
        <div class="field"><label>股数</label><input name="shares" type="number" step="0.001" required></div>
        <div class="field"><label>单价（每股）</label><input name="cost_per_share" type="number" step="0.01" required></div>
      </div>
      <div class="field"><label>备注</label><input name="note" placeholder="可选"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
        <button type="button" class="secondary" onclick="closeModal('add-lot-modal')">取消</button>
        <button type="submit" class="success">添加</button>
      </div>
    </form>
  </div>
</div>
<script>
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
</script>"""

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cost Basis Lots</title><style>{CSS}</style></head><body><div class="container">
{''.join(body)}
{add_modal}
</div></body></html>"""


@app.post("/lots/add", response_class=HTMLResponse)
def lots_add(
    purchase_date: str = Form(...),
    account_id: str = Form(...),
    account_name: str = Form(...),
    symbol: str = Form(...),
    shares: float = Form(...),
    cost_per_share: float = Form(...),
    note: str = Form(""),
):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO cost_basis_lots
              (purchase_date, account_id, account_name, symbol, shares, cost_per_share, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (purchase_date, account_id.strip(), account_name.strip(),
             symbol.strip().upper(), shares, cost_per_share, note.strip()),
        )
    return RedirectResponse(url="/lots", status_code=303)


@app.get("/wash-sale", response_class=HTMLResponse)
def wash_sale_view():
    """Detect potential wash sale violations.

    IRS rule: selling a security at a loss disallows the loss deduction if
    you buy a "substantially identical" security within 30 days before or
    after the sale (61-day window total).
    """
    from datetime import timedelta as _td

    with connect() as conn:
        # Get all SELL executions where we likely had a loss
        sells = conn.execute(
            """
            SELECT e.*, p.cost_basis_total / NULLIF(p.quantity, 0) as avg_cost
            FROM executions e
            LEFT JOIN positions_snapshot p
              ON p.symbol = e.symbol AND p.account_id = e.account_id
            WHERE e.action = 'SELL'
            ORDER BY e.trade_date DESC
            """
        ).fetchall()

        # Get all BUY executions
        buys = conn.execute(
            "SELECT * FROM executions WHERE action = 'BUY'"
        ).fetchall()

    alerts = []
    for sell in sells:
        sell_date = datetime.strptime(sell["trade_date"], "%Y-%m-%d").date()
        # Check if it was at a loss
        avg_cost = sell["avg_cost"] or 0
        is_loss = avg_cost > 0 and sell["price"] < avg_cost
        if not is_loss:
            continue

        # Look for buys of same symbol within 30 days (any account, any owner)
        for buy in buys:
            if buy["symbol"] != sell["symbol"]:
                continue
            buy_date = datetime.strptime(buy["trade_date"], "%Y-%m-%d").date()
            delta_days = (buy_date - sell_date).days
            if -30 <= delta_days <= 30 and buy["execution_id"] != sell["execution_id"]:
                alerts.append({
                    "symbol": sell["symbol"],
                    "sell_date": sell["trade_date"],
                    "sell_account": sell["account_name"],
                    "sell_shares": sell["shares"],
                    "sell_price": sell["price"],
                    "buy_date": buy["trade_date"],
                    "buy_account": buy["account_name"],
                    "buy_shares": buy["shares"],
                    "buy_price": buy["price"],
                    "delta_days": delta_days,
                    "estimated_loss": (avg_cost - sell["price"]) * sell["shares"],
                })

    body = []
    body.append(f'<div class="status">扫描了 {len(sells)} 笔卖出 × {len(buys)} 笔买入。<strong>{len(alerts)}</strong> 个潜在 wash sale 警告。</div>')

    if not alerts:
        body.append('<p style="color:var(--fg-muted);padding:20px;background:var(--bg-subtle);border-radius:8px;">✅ 当前没有 wash sale 风险。</p>')
    else:
        body.append('<table><thead><tr><th>Ticker</th><th>卖出</th><th>买回</th><th class="num">间隔（天）</th><th class="num">受影响损失</th><th>风险</th></tr></thead><tbody>')
        for a in alerts:
            severity = "🔴 高" if abs(a["delta_days"]) <= 30 else ""
            body.append(
                f'<tr>'
                f'<td><strong>{a["symbol"]}</strong></td>'
                f'<td>{a["sell_date"]} · {a["sell_shares"]:.2f} @ ${a["sell_price"]:.2f}<br>'
                f'<span style="font-size:11px;color:var(--fg-muted);">{a["sell_account"]}</span></td>'
                f'<td>{a["buy_date"]} · {a["buy_shares"]:.2f} @ ${a["buy_price"]:.2f}<br>'
                f'<span style="font-size:11px;color:var(--fg-muted);">{a["buy_account"]}</span></td>'
                f'<td class="num">{a["delta_days"]:+d}</td>'
                f'<td class="num loss">−${a["estimated_loss"]:,.0f}</td>'
                f'<td>{severity}</td>'
                f'</tr>'
            )
        body.append('</tbody></table>')

    body.append('''
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 Wash Sale Rule 详解</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>定义</strong>：IRS 规则 — 卖出有损失的证券后 30 天内（前后各 30 天，<strong>61 天总窗口</strong>）买回 substantially identical 证券，损失不能抵税。</li>
<li><strong>什么算 substantially identical</strong>：同一 ticker；同公司不同 share class（罕见）；密切跟踪同一指数的 ETF 之间也可能（争议）。</li>
<li><strong>跨账户 + 跨配偶</strong>：⚠️ Wash sale rule 适用于<strong>你和配偶的全部账户</strong>（包括 401k / IRA）。所以这个工具扫描了所有账户的交易。</li>
<li><strong>常见错误</strong>：401k 自动定投 + 同时在 taxable 卖出同一只股 → 触发 wash sale。</li>
<li><strong>合法替代品</strong>：卖 VOO 想保持大盘敞口 → 买 IVV 或 SPLG（不同发行商但都跟踪 S&amp;P 500，争议小）。卖 NVDA → 买半导体 ETF（SOXX/SMH）肯定 OK。</li>
<li><strong>后果</strong>：损失加到买回的 cost basis，所以税务上是 deferred 而不是 lost。但当年抵税计划被打乱。</li>
</ul>
</details>
''')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wash Sale Alert</title><style>{CSS}</style></head><body><div class="container">
<h1>🚫 Wash Sale 警告</h1>
<p class="subtitle">检测 61 天窗口内"卖出损失 + 买回同票"的潜在违规</p>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>
{"".join(body)}
</div></body></html>"""


@app.get("/correlation", response_class=HTMLResponse)
def correlation_view():
    """Pairwise correlation matrix of top holdings over the past 90 days."""
    import yfinance as yf
    import pandas as pd
    import numpy as np

    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT symbol, SUM(current_value) v FROM positions_snapshot
            WHERE import_date = ? GROUP BY symbol
            ORDER BY v DESC LIMIT 20
            """,
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()

    tickers = [r["symbol"] for r in rows]
    if len(tickers) < 2:
        return HTMLResponse(f"<p>至少需要 2 个持仓</p><a href='/'>← Back</a>")

    try:
        data = yf.download(tickers, period="90d", progress=False, auto_adjust=True)["Close"]
        # Handle single-ticker DataFrame edge case
        if isinstance(data, pd.Series):
            data = data.to_frame()
        returns = data.pct_change().dropna()
        corr = returns.corr()
    except Exception as e:
        return HTMLResponse(f"<p>yfinance 错误: {e}</p><a href='/'>← Back</a>")

    # Reorder by total value (already sorted by SQL)
    valid = [t for t in tickers if t in corr.columns]
    corr = corr.loc[valid, valid]

    # Build SVG heatmap
    n = len(valid)
    cell = 36
    label_w = 70
    width = label_w + n * cell + 20
    height = label_w + n * cell + 60

    def color(v: float) -> str:
        """Blue (-1) → white (0) → red (+1)."""
        if v >= 0:
            r = 255
            g = int(255 - v * 200)
            b = int(255 - v * 200)
        else:
            v = -v
            r = int(255 - v * 200)
            g = int(255 - v * 200)
            b = 255
        return f"rgb({r},{g},{b})"

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="ui-monospace, monospace" font-size="11">']
    # Column labels (top, rotated)
    for j, sym in enumerate(valid):
        x = label_w + j * cell + cell / 2
        svg.append(f'<text x="{x}" y="{label_w - 6}" transform="rotate(-45 {x} {label_w - 6})" text-anchor="start" fill="#666">{sym}</text>')
    # Cells + row labels
    for i, sym_i in enumerate(valid):
        svg.append(f'<text x="{label_w - 6}" y="{label_w + i * cell + cell / 2 + 4}" text-anchor="end" fill="#666">{sym_i}</text>')
        for j, sym_j in enumerate(valid):
            v = corr.iloc[i, j]
            if pd.isna(v):
                continue
            x = label_w + j * cell
            y = label_w + i * cell
            svg.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{color(v)}" stroke="#fff" stroke-width="0.5"/>')
            # Show value in cell for readability
            text_color = "#000" if abs(v) < 0.7 else "#fff"
            svg.append(f'<text x="{x + cell/2}" y="{y + cell/2 + 3}" text-anchor="middle" fill="{text_color}" font-size="10">{v:.2f}</text>')
    svg.append('</svg>')

    # Find high-correlation pairs (excluding self)
    high_pairs = []
    for i, t1 in enumerate(valid):
        for j, t2 in enumerate(valid):
            if i >= j:
                continue
            v = corr.iloc[i, j]
            if pd.isna(v):
                continue
            if v > 0.85:
                high_pairs.append((t1, t2, float(v)))
    high_pairs.sort(key=lambda x: -x[2])

    high_pairs_html = ""
    if high_pairs:
        high_pairs_html = '<h3 style="margin-top:24px;">⚠️ 高度相关对（&gt; 0.85） — 隐性集中度</h3>'
        high_pairs_html += '<p style="color:var(--fg-muted);font-size:13px;">这些 ticker 走势几乎一致 — 持有多个 = 没有真正分散。考虑只留一个或整合 ETF。</p>'
        high_pairs_html += '<table><thead><tr><th>Ticker A</th><th>Ticker B</th><th class="num">相关系数</th></tr></thead><tbody>'
        for t1, t2, v in high_pairs[:15]:
            high_pairs_html += f'<tr><td><strong>{t1}</strong></td><td><strong>{t2}</strong></td><td class="num loss">{v:.3f}</td></tr>'
        high_pairs_html += '</tbody></table>'

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>相关性热力图</title><style>{CSS}</style></head><body><div class="container">
<h1>🔗 持仓相关性热力图</h1>
<p class="subtitle">过去 90 天日收益率的皮尔逊相关系数 · top {n} 持仓</p>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-bottom:16px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 如何读热力图</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>红色 (1.0)</strong> = 走势完全一致 → 实际上是同一种暴露</li>
<li><strong>白色 (0.0)</strong> = 完全独立 → 真分散</li>
<li><strong>蓝色 (-1.0)</strong> = 完全反向 → 对冲</li>
<li><strong>0.85+ 的对</strong>：考虑整合（持有多只 = 隐性集中度）</li>
<li><strong>大盘 ETF (VOO/QQQ)</strong> 通常和大科技高相关 — 重复持有意义有限</li>
</ul>
</details>
<div style="background:var(--bg-subtle);padding:20px;border-radius:8px;border:1px solid var(--border);overflow-x:auto;">
{"".join(svg)}
</div>
{high_pairs_html}
</div></body></html>"""


@app.get("/tlh", response_class=HTMLResponse)
def tlh_view():
    """Tax-Loss Harvest candidates: taxable accounts with unrealized losses."""
    from tradingagents.portfolio.tax import estimate_sell_tax

    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM positions_snapshot
            WHERE import_date = ? AND account_type = 'Taxable'
              AND current_value > 0 AND cost_basis_total > 0
              AND current_value < cost_basis_total
            ORDER BY (cost_basis_total - current_value) DESC
            """,
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()

    if not rows:
        body = '<p style="color:var(--fg-muted);padding:20px;background:var(--bg-subtle);border-radius:8px;">🎉 当前没有 taxable 账户的浮亏仓位 — 没 TLH 机会。</p>'
        total_loss = 0.0
        total_savings = 0.0
    else:
        body_rows = []
        total_loss = 0.0
        total_savings = 0.0
        for r in rows:
            loss = r["cost_basis_total"] - r["current_value"]
            loss_pct = loss / r["cost_basis_total"] * 100 if r["cost_basis_total"] else 0
            # Estimate tax savings (high bracket: 37% ST or 23.8% LT + NIIT)
            # Assume long-term for conservative estimate
            est_savings_lt = loss * 0.238  # 20% LTCG + 3.8% NIIT
            est_savings_st = loss * 0.37
            total_loss += loss
            total_savings += est_savings_lt  # conservative
            body_rows.append(
                f'<tr>'
                f'<td><strong>{r["symbol"]}</strong></td>'
                f'<td>{r["account_name"]} <span class="tag" style="font-size:10px;">🏦 {r["broker"] or "?"}</span></td>'
                f'<td class="num">{r["quantity"]:.3f}</td>'
                f'<td class="num">${r["current_value"]:,.0f}</td>'
                f'<td class="num">${r["cost_basis_total"]:,.0f}</td>'
                f'<td class="num loss">−${loss:,.0f} ({loss_pct:.1f}%)</td>'
                f'<td class="num gain">${est_savings_lt:,.0f}</td>'
                f'<td class="num gain">${est_savings_st:,.0f}</td>'
                f'</tr>'
            )
        body = f'''
<div class="status">
  💡 共 <strong>{len(rows)}</strong> 个 TLH 候选 · 总浮亏 <strong>${total_loss:,.0f}</strong> ·
  估算抵税 <strong>${total_savings:,.0f}</strong>（按长期资本利得 23.8% 计算）
</div>
<table><thead><tr>
<th>Ticker</th><th>账户</th><th class="num">股数</th><th class="num">市值</th><th class="num">成本</th><th class="num">浮亏</th>
<th class="num">LT 抵税估算</th><th class="num">ST 抵税估算</th>
</tr></thead><tbody>{"".join(body_rows)}</tbody></table>

<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 TLH 操作指南</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>什么是 TLH</strong>：Tax-Loss Harvesting — 卖出浮亏仓位 → 实现资本损失 → 抵消同年其他资本利得（或最多 $3,000 抵消普通收入）。</li>
<li><strong>税率假设</strong>：LT（长期持有 &gt;1 年）= 20% LTCG + 3.8% NIIT = 23.8%；ST（短期 ≤1 年）= 37% 最高边际税率 + 3.8% NIIT。</li>
<li><strong>Wash Sale Rule</strong>：卖出后 30 天内（前后各 30 天）不能买回 substantially identical 证券 — 否则 IRS 不让抵亏损。⚠️ 查 <a href="/wash-sale">Wash Sale Alert</a> 页面避免。</li>
<li><strong>替代品策略</strong>：卖 NVDA 想保持半导体敞口 → 买 SOXX/SMH ETF（不算 substantially identical）。</li>
<li><strong>结转</strong>：当年无法抵消的损失可结转到未来无限期。</li>
</ul>
</details>
'''

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TLH Finder</title><style>{CSS}</style></head><body><div class="container">
<h1>💰 Tax-Loss Harvest 候选</h1>
<p class="subtitle">应税账户中的浮亏仓位 — 卖出可实现资本损失抵税</p>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>
{body}
</div></body></html>"""


@app.get("/drift", response_class=HTMLResponse)
def drift_view():
    """Position-vs-rating drift: where current weight conflicts with PM rating."""
    latest = latest_snapshot_date()
    with connect() as conn:
        positions = conn.execute(
            "SELECT symbol, SUM(current_value) v FROM positions_snapshot WHERE import_date = ? GROUP BY symbol",
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()
        decisions = {
            r["symbol"]: r["rating"]
            for r in conn.execute("SELECT symbol, rating FROM decisions").fetchall()
        }

    total = sum(p["v"] for p in positions)

    # Score each holding's drift
    # Bullish ratings (Buy/Overweight) → fine to have high weight, ALERT if very low
    # Bearish ratings (Underweight/Sell) → ALERT if weight is high
    # Hold → no drift alert
    rows = []
    for p in positions:
        sym = p["symbol"]
        value = p["v"]
        weight = value / total * 100 if total else 0
        rating = decisions.get(sym, "—")
        drift_score = 0.0
        action = ""
        if rating in ("Underweight", "Sell"):
            # The higher the weight, the more urgent the trim
            drift_score = weight  # purely weight-driven
            if rating == "Sell" and weight > 0.5:
                action = "🔴 紧迫减仓"
            elif weight > 2:
                action = "🟡 应减仓"
            elif weight > 0.5:
                action = "🟢 已小仓位"
        elif rating in ("Buy", "Overweight"):
            # Too small (<0.5%) suggests Buy is underutilized
            if weight < 0.5:
                drift_score = 1.0 - weight / 0.5  # higher when smaller
                action = "🔵 加仓空间"
            elif weight < 1.5:
                action = "🟢 适度"
            else:
                action = "✓ 充分"
        elif rating == "Hold":
            action = "—"
        else:
            action = "❓ 未分析"

        rows.append({
            "symbol": sym, "value": value, "weight": weight,
            "rating": rating, "drift_score": drift_score, "action": action,
        })

    # Sort: most urgent first (Sells with high weight, then Underweights with high weight, then Buys with very low weight)
    def priority(r):
        rating_priority = {"Sell": 0, "Underweight": 1, "Buy": 2, "Overweight": 3, "Hold": 4}.get(r["rating"], 5)
        return (rating_priority, -r["drift_score"])
    rows.sort(key=priority)

    body = [
        '<h1>⚠️ Position Drift Alert</h1>',
        '<p class="subtitle">PM 评级 vs 当前组合权重的错配 — 越靠前越紧迫</p>',
        '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>',
        '''<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-bottom:16px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 如何读这个表</summary>
<ul style="margin-top:12px;font-size:13px;color:var(--fg-muted);">
<li>🔴 <strong>紧迫减仓</strong>：PM 评级 Sell 且组合权重 > 0.5%</li>
<li>🟡 <strong>应减仓</strong>：PM 评级 Underweight 且权重 > 2%</li>
<li>🔵 <strong>加仓空间</strong>：PM 评级 Buy/Overweight 但当前权重 < 0.5%</li>
<li>✓ <strong>充分</strong>：Buy/Overweight 且权重 > 1.5%（已合理配置）</li>
<li>— <strong>Hold</strong>：评级中性，不需 drift action</li>
</ul>
</details>''',
        '<table><thead><tr><th>Ticker</th><th>评级</th><th class="num">当前价值</th><th class="num">组合权重</th><th>Drift 状态</th></tr></thead><tbody>',
    ]
    for r in rows:
        weight_class = ""
        if r["weight"] > 15: weight_class = "loss"
        elif r["weight"] < 0.3 and r["rating"] in ("Buy", "Overweight"): weight_class = "loss"
        body.append(
            f'<tr><td><strong>{r["symbol"]}</strong></td>'
            f'<td><span class="tag tag-{r["rating"].lower()}" style="padding:2px 8px;">{r["rating"]}</span></td>'
            f'<td class="num">${r["value"]:,.0f}</td>'
            f'<td class="num {weight_class}">{r["weight"]:.2f}%</td>'
            f'<td>{r["action"]}</td></tr>'
        )
    body.append('</tbody></table>')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Drift Alert</title><style>{CSS}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/performance", response_class=HTMLResponse)
def performance_view(windows: str = "5,30,90"):
    """PM forward-test: actual return + alpha vs SPY for each decision."""
    import yfinance as yf
    from datetime import datetime as _dt, timedelta as _td

    try:
        window_days = [int(w) for w in windows.split(",") if w.strip()]
    except ValueError:
        window_days = [5, 30, 90]

    with connect() as conn:
        decisions = conn.execute(
            "SELECT trade_date, symbol, rating FROM decisions ORDER BY trade_date, symbol"
        ).fetchall()

    if not decisions:
        return HTMLResponse("<p>No decisions yet</p><a href='/'>← Back</a>")

    def alpha(t: str, d: str, days: int):
        try:
            start = _dt.strptime(d, "%Y-%m-%d")
            end_s = (start + _td(days=days + 7)).strftime("%Y-%m-%d")
            stk = yf.Ticker(t).history(start=d, end=end_s)
            spy = yf.Ticker("SPY").history(start=d, end=end_s)
            if len(stk) < 2 or len(spy) < 2:
                return None, None
            di = min(days, len(stk) - 1, len(spy) - 1)
            raw = float((stk["Close"].iloc[di] - stk["Close"].iloc[0]) / stk["Close"].iloc[0])
            spy_r = float((spy["Close"].iloc[di] - spy["Close"].iloc[0]) / spy["Close"].iloc[0])
            return raw, raw - spy_r
        except Exception:
            return None, None

    def classify(rating: str, a):
        if a is None:
            return "pending"
        bullish = rating in ("Buy", "Overweight")
        bearish = rating in ("Underweight", "Sell")
        if bullish and a > 0.005:
            return "hit"
        if bearish and a < -0.005:
            return "hit"
        if rating == "Hold" and abs(a) < 0.02:
            return "hit"
        return "miss"

    rows = []
    for d in decisions:
        row = {"symbol": d["symbol"], "trade_date": d["trade_date"], "rating": d["rating"]}
        for w in window_days:
            raw, a = alpha(d["symbol"], d["trade_date"], w)
            row[f"raw_{w}"] = raw
            row[f"alpha_{w}"] = a
            row[f"hit_{w}"] = classify(d["rating"], a)
        rows.append(row)

    # Aggregate hit rate per rating × window
    summary = {}
    for rating in ["Buy", "Overweight", "Hold", "Underweight", "Sell"]:
        summary[rating] = {}
        rs = [r for r in rows if r["rating"] == rating]
        for w in window_days:
            settled = [r for r in rs if r[f"hit_{w}"] != "pending"]
            hits = sum(1 for r in settled if r[f"hit_{w}"] == "hit")
            summary[rating][w] = (hits, len(settled))

    body = [
        '<h1>🎯 PM 准确度跟踪</h1>',
        '<p class="subtitle">每个 PM 决策 vs SPY 的实际 alpha · 多窗口窗口</p>',
        '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>',
    ]
    body.append(f'<div class="status">📊 跟踪 {len(decisions)} 个决策 · 窗口: {", ".join(f"{w}d" for w in window_days)}</div>')

    # Aggregate table
    body.append('<h2>命中率汇总</h2>')
    body.append('<table><thead><tr><th>评级</th>' + ''.join(f'<th class="num">{w}d</th>' for w in window_days) + '</tr></thead><tbody>')
    for rating in ["Buy", "Overweight", "Hold", "Underweight", "Sell"]:
        cells = [f'<td><span class="tag tag-{rating.lower()}" style="padding:2px 8px;">{rating}</span></td>']
        for w in window_days:
            hits, total = summary[rating][w]
            if total == 0:
                cells.append('<td class="num" style="color:var(--fg-muted);">pending</td>')
            else:
                pct = hits / total * 100
                cls = "gain" if pct >= 60 else "loss" if pct < 40 else ""
                cells.append(f'<td class="num {cls}">{hits}/{total} ({pct:.0f}%)</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    body.append('</tbody></table>')

    # Per-decision details
    body.append(f'<h2 style="margin-top:32px;">每只决策详情</h2>')
    body.append('<table><thead><tr><th>Ticker</th><th>评级</th><th>日期</th>'
                + ''.join(f'<th class="num">{w}d Raw / Alpha</th>' for w in window_days)
                + ''.join(f'<th>{w}d Hit?</th>' for w in window_days)
                + '</tr></thead><tbody>')
    for r in rows:
        cells = [
            f'<td><strong>{r["symbol"]}</strong></td>',
            f'<td><span class="tag tag-{r["rating"].lower()}" style="padding:2px 8px;">{r["rating"]}</span></td>',
            f'<td>{r["trade_date"]}</td>',
        ]
        for w in window_days:
            raw = r[f"raw_{w}"]
            a = r[f"alpha_{w}"]
            if raw is None:
                cells.append('<td class="num" style="color:var(--fg-muted);">⏳ pending</td>')
            else:
                cls = "gain" if a > 0 else "loss"
                cells.append(f'<td class="num {cls}">{raw*100:+.2f}% / {a*100:+.2f}%</td>')
        for w in window_days:
            hit = r[f"hit_{w}"]
            emoji = {"hit": "✅", "miss": "❌", "pending": "⏳"}[hit]
            cells.append(f'<td>{emoji}</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    body.append('</tbody></table>')

    body.append('''
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 怎么读这表</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>Raw</strong>：该 ticker 从 trade_date 起 N 天的实际涨跌。</li>
<li><strong>Alpha</strong>：减去同期 SPY 涨跌 — 衡量超额收益。</li>
<li><strong>Hit?</strong>：评级方向与 alpha 方向是否一致。Buy/Overweight 看 alpha > +0.5%；Underweight/Sell 看 alpha < -0.5%；Hold 看 |alpha| < 2%。</li>
<li><strong>命中率参考</strong>：&gt;60% 算好（绿）；&lt;40% 算差（红）。50% = 随机。</li>
<li><strong>注意</strong>：决策刚做时（5 月 8 日）所有 5d 窗口都 pending（要到 5/15 才有数据）。30d / 90d 窗口还要等。</li>
</ul>
</details>
''')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PM 准确度</title><style>{CSS}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/sectors", response_class=HTMLResponse)
def sectors_view():
    """Sector ETF performance dashboard (XLK/XLV/XLF/...) — rotation signals."""
    import yfinance as yf

    SECTORS = [
        ("XLK", "Technology", "🖥️"),
        ("XLV", "Health Care", "🏥"),
        ("XLF", "Financials", "🏦"),
        ("XLY", "Consumer Discretionary", "🛍️"),
        ("XLP", "Consumer Staples", "🛒"),
        ("XLI", "Industrials", "🏭"),
        ("XLE", "Energy", "⛽"),
        ("XLB", "Materials", "⛏️"),
        ("XLU", "Utilities", "💡"),
        ("XLRE", "Real Estate", "🏢"),
        ("XLC", "Communication Services", "📡"),
        ("SPY", "S&P 500 (benchmark)", "📊"),
    ]

    results = []
    for symbol, name, emoji in SECTORS:
        try:
            hist = yf.Ticker(symbol).history(period="6mo")
            if len(hist) < 2:
                continue
            latest = float(hist["Close"].iloc[-1])
            # Various lookbacks
            def pct(days: int) -> float | None:
                if len(hist) < days + 1:
                    return None
                past = float(hist["Close"].iloc[-1 - days])
                return (latest - past) / past * 100 if past else None
            d1 = pct(1)
            d5 = pct(5)
            d30 = pct(22)  # ~30 calendar = ~22 trading
            d90 = pct(66)
            results.append({
                "symbol": symbol, "name": name, "emoji": emoji,
                "latest": latest, "d1": d1, "d5": d5, "d30": d30, "d90": d90,
            })
        except Exception:
            continue

    if not results:
        return HTMLResponse("<p>yfinance 拉取失败</p><a href='/'>← Back</a>")

    spy_d30 = next((r["d30"] for r in results if r["symbol"] == "SPY"), 0) or 0
    spy_d90 = next((r["d90"] for r in results if r["symbol"] == "SPY"), 0) or 0

    body = ['<h1>🌐 行业 ETF 轮动</h1>',
            '<p class="subtitle">11 个 SPDR 行业 ETF + SPY 基准 · 多窗口涨跌</p>',
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>',
            '<table><thead><tr><th>ETF</th><th>板块</th><th class="num">最新价</th><th class="num">1d</th><th class="num">5d</th><th class="num">30d</th><th class="num">90d</th><th class="num">30d vs SPY</th><th class="num">90d vs SPY</th></tr></thead><tbody>']

    def cls(v):
        if v is None: return ""
        return "gain" if v > 0 else "loss"
    def fmt(v):
        if v is None: return "—"
        return f"{v:+.2f}%"

    # Sort by 30d performance descending (top performers first)
    for r in sorted(results, key=lambda x: -(x["d30"] or -999)):
        is_spy = r["symbol"] == "SPY"
        rel30 = (r["d30"] - spy_d30) if r["d30"] is not None else None
        rel90 = (r["d90"] - spy_d90) if r["d90"] is not None else None
        row_style = ' style="background:var(--bg-subtle);font-weight:600;"' if is_spy else ''
        body.append(
            f'<tr{row_style}><td>{r["emoji"]} <strong>{r["symbol"]}</strong></td>'
            f'<td>{r["name"]}</td>'
            f'<td class="num">${r["latest"]:.2f}</td>'
            f'<td class="num {cls(r["d1"])}">{fmt(r["d1"])}</td>'
            f'<td class="num {cls(r["d5"])}">{fmt(r["d5"])}</td>'
            f'<td class="num {cls(r["d30"])}">{fmt(r["d30"])}</td>'
            f'<td class="num {cls(r["d90"])}">{fmt(r["d90"])}</td>'
            f'<td class="num {cls(rel30)}">{fmt(rel30)}</td>'
            f'<td class="num {cls(rel90)}">{fmt(rel90)}</td>'
            f'</tr>'
        )
    body.append('</tbody></table>')

    body.append('''
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 怎么读这张表</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>"30d vs SPY"</strong>：板块超额收益（相对大盘）。正数 = 板块跑赢，负数 = 跑输。</li>
<li><strong>板块轮动信号</strong>：30d 排名前 3 + 90d 也前 3 → 强势板块；30d 前 3 但 90d 后 3 → 反弹但短期，慎入。</li>
<li><strong>防御 vs 进攻</strong>：XLU/XLP/XLV 跑赢 → 市场避险；XLK/XLY/XLF 跑赢 → 风险偏好。</li>
<li><strong>对你组合的影响</strong>：你 ~80% 集中在 XLK（科技）领域。如果科技 30d 跑输 SPY → 组合大概率跑输大盘。</li>
</ul>
</details>
''')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>板块轮动</title><style>{CSS}</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/tickers", response_class=HTMLResponse)
def tickers_view():
    """Canonical ticker registry — single source of truth for prices/metadata."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tickers ORDER BY symbol"
        ).fetchall()
        # Count how many positions reference each ticker
        ref_counts = {r["symbol"]: r["c"] for r in conn.execute(
            """
            SELECT symbol, COUNT(*) c FROM positions_snapshot
            WHERE import_date = (SELECT MAX(import_date) FROM positions_snapshot)
            GROUP BY symbol
            """
        ).fetchall()}

    body = ['<h1>🗂️ Ticker Registry</h1>',
            '<p class="subtitle">所有股票的权威价格表 — positions_snapshot 引用这里。点 📡 更新价格 同步。</p>',
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>']
    if not rows:
        body.append('<p style="color:var(--fg-muted);">还没有 ticker 记录 — 点主页 📡 更新价格 初始化。</p>')
    else:
        body.append(f'<div class="status">📊 <strong>{len(rows)}</strong> 个 ticker · 在 <strong>{sum(ref_counts.values())}</strong> 行 positions 中被引用</div>')
        body.append('<table><thead><tr><th>Ticker</th><th class="num">最新价</th><th class="num">引用行数</th><th>最近更新</th></tr></thead><tbody>')
        for r in rows:
            n_refs = ref_counts.get(r["symbol"], 0)
            price = f"${r['last_price']:.2f}" if r["last_price"] else "—"
            body.append(
                f'<tr><td><strong><a href="/lookup?q={r["symbol"]}">{r["symbol"]}</a></strong></td>'
                f'<td class="num">{price}</td>'
                f'<td class="num">{n_refs}</td>'
                f'<td style="font-size:12px;color:var(--fg-muted);">{r["last_updated"] or "—"}</td></tr>'
            )
        body.append('</tbody></table>')

    body.append('''
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 Normalization 设计</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>问题</strong>：同一 ticker 在 N 个账户里出现，原本每行都重复存 last_price — 更新要 UPDATE N 次，可能不一致。</li>
<li><strong>方案</strong>：tickers 表是<strong>权威价格源</strong>（每个 ticker 一行）。positions_snapshot.last_price 保留作 cache。</li>
<li><strong>更新流</strong>：点 📡 更新价格 → yfinance 批量拉 → UPSERT tickers → SYNC 到 positions_snapshot.last_price。</li>
<li><strong>未来扩展</strong>：tickers 表可加 sector / market_cap / dividend_yield / target_price 等元数据，所有 ticker 一处维护。</li>
</ul>
</details>
''')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ticker Registry</title><style>{CSS}</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/charts", response_class=HTMLResponse)
def charts_view():
    """Visual breakdowns: ticker weight / owner / account_type / broker pies."""
    import math
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM positions_snapshot WHERE import_date = ?",
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()

    if not rows:
        return HTMLResponse("<p>No data</p><a href='/'>← Back</a>")

    total = sum(r["current_value"] for r in rows)

    def aggregate(key: str) -> list[tuple[str, float]]:
        d: dict[str, float] = {}
        for r in rows:
            d[r[key] or "Unknown"] = d.get(r[key] or "Unknown", 0) + r["current_value"]
        sorted_pairs = sorted(d.items(), key=lambda kv: -kv[1])
        # Cap at 10 slices + Other
        if len(sorted_pairs) > 10:
            top = sorted_pairs[:10]
            other = sum(v for _, v in sorted_pairs[10:])
            top.append(("Other", other))
            return top
        return sorted_pairs

    def aggregate_tickers() -> list[tuple[str, float]]:
        d: dict[str, float] = {}
        for r in rows:
            d[r["symbol"]] = d.get(r["symbol"], 0) + r["current_value"]
        sorted_pairs = sorted(d.items(), key=lambda kv: -kv[1])
        if len(sorted_pairs) > 12:
            top = sorted_pairs[:12]
            other = sum(v for _, v in sorted_pairs[12:])
            top.append(("Other", other))
            return top
        return sorted_pairs

    PALETTE = ["#0969da", "#1a7f37", "#9a6700", "#8250df", "#d1242f",
               "#0a3069", "#116329", "#7d4e00", "#5a1e93", "#a40e26",
               "#1f6feb", "#3fb950", "#d29922", "#a371f7", "#f85149"]

    def render_pie(data: list[tuple[str, float]], title: str, size: int = 320) -> str:
        total_v = sum(v for _, v in data)
        if not total_v:
            return ""
        cx, cy, r = size / 2, size / 2, size / 2 - 8
        arcs = []
        start_angle = -math.pi / 2  # start at top
        for i, (label, value) in enumerate(data):
            angle = value / total_v * 2 * math.pi
            end_angle = start_angle + angle
            x1 = cx + r * math.cos(start_angle)
            y1 = cy + r * math.sin(start_angle)
            x2 = cx + r * math.cos(end_angle)
            y2 = cy + r * math.sin(end_angle)
            large = 1 if angle > math.pi else 0
            color = PALETTE[i % len(PALETTE)]
            path = f"M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} Z"
            arcs.append(f'<path d="{path}" fill="{color}" stroke="var(--bg)" stroke-width="1.5"><title>{label}: ${value:,.0f} ({value/total_v*100:.1f}%)</title></path>')
            start_angle = end_angle

        # Legend
        legend = ['<div style="display:flex;flex-direction:column;gap:4px;font-size:12px;">']
        for i, (label, value) in enumerate(data):
            pct = value / total_v * 100
            color = PALETTE[i % len(PALETTE)]
            legend.append(
                f'<div style="display:flex;align-items:center;gap:8px;">'
                f'<span style="display:inline-block;width:14px;height:14px;background:{color};border-radius:3px;"></span>'
                f'<span style="flex:1;">{label}</span>'
                f'<span style="font-variant-numeric:tabular-nums;color:var(--fg-muted);">${value/1000:.0f}K · {pct:.1f}%</span>'
                f'</div>'
            )
        legend.append('</div>')

        return f'''
<div style="background:var(--bg-subtle);border-radius:8px;padding:20px;border:1px solid var(--border);margin-bottom:20px;">
  <h3 style="margin-top:0;font-size:15px;">{title}</h3>
  <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap;">
    <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" style="flex-shrink:0;">{"".join(arcs)}</svg>
    <div style="flex:1;min-width:200px;">{"".join(legend)}</div>
  </div>
</div>'''

    pie_ticker = render_pie(aggregate_tickers(), "🏷️ 按 Ticker 权重")
    pie_owner = render_pie(aggregate("owner"), "👥 按 Owner")
    pie_type = render_pie(aggregate("account_type"), "💰 按账户类型（税务桶）")
    pie_broker = render_pie(aggregate("broker"), "🏦 按 Broker")

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Charts</title><style>{CSS}</style></head><body><div class="container">
<h1>📊 持仓权重可视化</h1>
<p class="subtitle">总价值 <strong>${total:,.0f}</strong> · 数据日期 {latest or "今天"}</p>
<div style="margin-bottom:24px;"><a class="btn secondary" href="/">← 持仓</a></div>
{pie_ticker}
{pie_owner}
{pie_type}
{pie_broker}
</div></body></html>"""


@app.get("/thesis-evolution", response_class=HTMLResponse)
def thesis_evolution_view():
    """Per-ticker history of PM ratings over time — see if judgment was stable."""
    import re as _re
    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol, trade_date, rating, final_decision FROM decisions ORDER BY symbol, trade_date"
        ).fetchall()

    by_symbol: dict[str, list] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    # Only show tickers with 2+ data points (otherwise no evolution to see)
    evolving = {s: rs for s, rs in by_symbol.items() if len(rs) >= 2}
    stable = {s: rs for s, rs in by_symbol.items() if len(rs) == 1}

    body = [
        '<h1>📈 Thesis 演变跟踪</h1>',
        '<p class="subtitle">同 ticker 跨多次分析的评级变化 — 看 PM 自己判断稳定性</p>',
        '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>',
    ]

    body.append(f'<div class="status">📊 {len(by_symbol)} 个 ticker · <strong>{len(evolving)}</strong> 个有多次分析 · <strong>{len(stable)}</strong> 个仅 1 次</div>')

    pt_re = _re.compile(r"Price Target.*?\$?([\d.]+)")
    if evolving:
        body.append('<h2 style="margin-top:24px;">🔄 评级演变（≥ 2 次分析）</h2>')
        body.append('<table><thead><tr><th>Ticker</th><th>评级序列（旧→新）</th><th>PT 序列</th><th>稳定性</th></tr></thead><tbody>')
        for sym in sorted(evolving):
            seq = evolving[sym]
            rating_seq = [r["rating"] for r in seq]
            pt_seq = []
            for r in seq:
                m = pt_re.search(r["final_decision"] or "")
                pt_seq.append(f"${m.group(1)}" if m else "—")

            rating_html = " → ".join(
                f'<span class="tag tag-{rt.lower()}" style="padding:2px 8px;font-size:11px;">{rt}</span>'
                for rt in rating_seq
            )
            n_unique = len(set(rating_seq))
            if n_unique == 1:
                stability = '✅ 完全一致'
            elif n_unique == 2:
                stability = '🟡 轻微变化'
            else:
                stability = '⚠️ 明显分歧'
            body.append(
                f'<tr><td><strong><a href="/decisions/{sym}">{sym}</a></strong></td>'
                f'<td>{rating_html}</td>'
                f'<td style="font-size:12px;">{" → ".join(pt_seq)}</td>'
                f'<td>{stability}</td></tr>'
            )
        body.append('</tbody></table>')
    else:
        body.append('<p style="color:var(--fg-muted);">还没有 ticker 被多次分析过。每次重跑分析后这里会自动出现演变。</p>')

    body.append('''
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-top:24px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 为什么 thesis 演变重要</summary>
<ul style="margin-top:12px;font-size:13px;line-height:1.7;">
<li><strong>判断稳定性</strong>：同 ticker 跨多次分析的评级应该相对稳定 — 大幅翻转（Buy → Sell）需要新事件来 justify。</li>
<li><strong>检测 model drift</strong>：如果 PM 在没有新事实的情况下改变立场，可能是 LLM 抽奖性而非真判断。</li>
<li><strong>历史成本基础</strong>：未来 6-12 个月，你能看到"我 5 月给 NVDA Overweight，10 月还是 Overweight，2027 年 1 月变 Hold" — 这种演变本身是有意义的信号。</li>
<li><strong>学习数据</strong>：评级变化 + 实际 alpha = PM 的真实命中率（详见 /runs 反向验证）</li>
</ul>
</details>
''')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Thesis 演变</title><style>{CSS}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/owners", response_class=HTMLResponse)
def owners_view():
    """Group positions by owner (Self / Spouse / Joint / child names)."""
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM positions_snapshot WHERE import_date = ? ORDER BY current_value DESC",
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()

    by_owner: dict[str, list] = {}
    for r in rows:
        by_owner.setdefault(r["owner"] or "Unknown", []).append(r)

    grand_total = sum(r["current_value"] for r in rows)

    sections = []
    sections.append(f'''
<div class="status">
  <strong>{len(rows)}</strong> 仓位 · <strong>{len(by_owner)}</strong> owners · 总价值 <strong>${grand_total:,.0f}</strong>
</div>''')

    # Summary table
    sections.append('<h2 style="margin-top:24px;">📊 按 owner 汇总</h2>')
    sections.append('<table><thead><tr><th>Owner</th><th class="num">仓位数</th><th class="num">价值</th><th class="num">占比</th><th class="num">按账户类型分布</th></tr></thead><tbody>')
    for owner in sorted(by_owner, key=lambda o: -sum(r["current_value"] for r in by_owner[o])):
        items = by_owner[owner]
        sub = sum(r["current_value"] for r in items)
        pct = sub / grand_total * 100 if grand_total else 0
        type_breakdown: dict[str, float] = {}
        for r in items:
            type_breakdown[r["account_type"]] = type_breakdown.get(r["account_type"], 0) + r["current_value"]
        tb = " · ".join(f'{t}: ${v/1000:.0f}K' for t, v in sorted(type_breakdown.items(), key=lambda x: -x[1]))
        sections.append(
            f'<tr><td><strong>👤 {owner}</strong></td>'
            f'<td class="num">{len(items)}</td>'
            f'<td class="num">${sub:,.0f}</td>'
            f'<td class="num">{pct:.1f}%</td>'
            f'<td style="font-size:12px;color:var(--fg-muted);">{tb}</td></tr>'
        )
    sections.append('</tbody></table>')

    # Per-owner top holdings
    for owner in sorted(by_owner, key=lambda o: -sum(r["current_value"] for r in by_owner[o])):
        items = by_owner[owner]
        sub = sum(r["current_value"] for r in items)
        # Aggregate by symbol within owner
        by_sym: dict[str, dict] = {}
        for r in items:
            s = by_sym.setdefault(r["symbol"], {"value": 0, "cost": 0, "shares": 0, "accounts": 0})
            s["value"] += r["current_value"]
            s["cost"] += r["cost_basis_total"] or 0
            s["shares"] += r["quantity"]
            s["accounts"] += 1
        top = sorted(by_sym.items(), key=lambda kv: -kv[1]["value"])[:10]

        sections.append(f'<h2 style="margin-top:32px;">👤 {owner} — top 10 ({len(by_sym)} unique tickers, ${sub:,.0f})</h2>')
        sections.append('<table><thead><tr><th>Ticker</th><th class="num">合计股数</th><th class="num">价值</th><th class="num">成本</th><th class="num">P/L%</th><th class="num">账户数</th></tr></thead><tbody>')
        for sym, d in top:
            pl_pct = ((d["value"] - d["cost"]) / d["cost"] * 100) if d["cost"] else 0
            pl_class = "gain" if pl_pct >= 0 else "loss"
            sections.append(
                f'<tr><td><strong>{sym}</strong></td>'
                f'<td class="num">{d["shares"]:.3f}</td>'
                f'<td class="num">${d["value"]:,.0f}</td>'
                f'<td class="num">${d["cost"]:,.0f}</td>'
                f'<td class="num {pl_class}">{pl_pct:+.1f}%</td>'
                f'<td class="num">{d["accounts"]}</td></tr>'
            )
        sections.append('</tbody></table>')

    body = f'''
<h1>👥 按 Owner 看持仓</h1>
<p class="subtitle">按 Self / Spouse / Joint / 子女 分组聚合</p>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 回到持仓</a></div>
{"".join(sections)}
'''
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Owner 视图</title><style>{CSS}</style></head><body><div class="container">
{body}
</div></body></html>"""


@app.get("/decisions", response_class=HTMLResponse)
def decisions_view():
    import json as _json
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY rating, symbol"
        ).fetchall()

    RATING_ORDER = {"Buy": 0, "Overweight": 1, "Hold": 2, "Underweight": 3, "Sell": 4}
    rows_sorted = sorted(rows, key=lambda r: (RATING_ORDER.get(r["rating"], 99), r["symbol"]))

    counts = {}
    for r in rows:
        counts[r["rating"]] = counts.get(r["rating"], 0) + 1
    chips = " ".join(
        f'<span class="tag tag-{r.lower()}" style="padding:4px 10px;font-size:12px;margin-right:6px">{r}: {counts.get(r,0)}</span>'
        for r in ["Buy", "Overweight", "Hold", "Underweight", "Sell"] if counts.get(r, 0)
    )

    glossary = """
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-bottom:16px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 评级说明（点击展开）</summary>
<table style="margin-top:12px;font-size:13px;">
<thead><tr><th>评级</th><th>中文</th><th>含义</th><th>典型动作</th></tr></thead>
<tbody>
<tr><td><span class="tag tag-buy" style="padding:2px 8px;">Buy</span></td><td>买入</td><td>强烈看多。基本面+趋势+估值全部支持</td><td>大幅加仓 / 新开仓</td></tr>
<tr><td><span class="tag tag-overweight" style="padding:2px 8px;">Overweight</span></td><td><strong>超配</strong></td><td>看多但克制。该股<strong>组合权重应高于基准（如 SPY 指数）</strong>，但估值/技术不支持激进追价</td><td>持有 + 回调小幅加仓</td></tr>
<tr><td><span class="tag tag-hold" style="padding:2px 8px;">Hold</span></td><td>持有</td><td>中性。基本面 OK 但当前不是好的进场点</td><td>已持不动，新资金等待</td></tr>
<tr><td><span class="tag tag-underweight" style="padding:2px 8px;">Underweight</span></td><td><strong>低配</strong></td><td>看空但不清仓。该股<strong>组合权重应低于基准</strong>，建议部分减仓</td><td>分批减 20-35% / 反弹时卖</td></tr>
<tr><td><span class="tag tag-sell" style="padding:2px 8px;">Sell</span></td><td>卖出</td><td>强烈看空。基本面恶化或重大风险</td><td>清仓 100%（应税账户做 TLH）</td></tr>
</tbody>
</table>
<p style="margin-top:8px;font-size:12px;color:var(--fg-muted);">
<strong>关键区分</strong>：Overweight ≠ Buy（强度不同）· Underweight ≠ Sell（保留部分 vs 全清）· "基准权重" 通常指 SPY 指数里该股的权重
</p>
</details>
"""

    body = [f'<h1>📋 PM 分析 & 评级</h1><p class="subtitle">{len(rows)} 个 ticker · 最新分析按 ticker 自动汇总</p>',
            f'<div class="status">{chips}</div>',
            glossary,
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 回到持仓</a></div>',
            '<table style="background:var(--bg-subtle);border-radius:8px;border:1px solid var(--border);overflow:hidden;">',
            '<thead><tr><th>Ticker</th><th>评级</th><th>分析日期</th><th class="num">账户级动作</th><th>反思（如有）</th><th></th></tr></thead>',
            '<tbody>']
    for r in rows_sorted:
        actions = _json.loads(r["account_actions"]) if r["account_actions"] else []
        n_act = sum(1 for a in actions if a.get("action") not in (None, "Hold"))
        reflection = (r["reflection"] or "")[:80]
        body.append(
            f'<tr>'
            f'<td><strong>{r["symbol"]}</strong></td>'
            f'<td><span class="tag tag-{r["rating"].lower()}" style="padding:3px 10px;">{r["rating"]}</span></td>'
            f'<td>{r["trade_date"]}</td>'
            f'<td class="num">{n_act} actions ({len(actions)} accts)</td>'
            f'<td style="color:var(--fg-muted);font-size:12px;">{reflection}</td>'
            f'<td><a href="/decisions/{r["symbol"]}" class="btn small secondary">详情</a></td>'
            f'</tr>'
        )
    body.append('</tbody></table>')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PM 分析 — Portfolio Manager</title><style>{CSS}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.get("/decisions/{ticker}", response_class=HTMLResponse)
def decision_detail(ticker: str):
    import json as _json
    import markdown as _md
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    if not row:
        return HTMLResponse(f"<p>No decision for {ticker}</p><a href='/decisions'>← Back</a>")

    decision_html = _md.markdown(row["final_decision"], extensions=["tables", "fenced_code"])

    rating_tagline = {
        "Buy": "强烈看多 — 基本面+趋势+估值全部支持，建议大幅加仓",
        "Overweight": "超配 — 看多但克制，组合权重应高于基准（SPY 指数权重）",
        "Hold": "持有 — 中性，基本面 OK 但当前不是好的进场点",
        "Underweight": "低配 — 看空但不清仓，组合权重应低于基准，建议部分减仓",
        "Sell": "强烈看空 — 基本面恶化或重大风险，建议清仓",
    }.get(row["rating"], "")

    glossary_html = """
<details style="background:var(--bg-subtle);border-radius:8px;padding:12px 16px;margin-bottom:16px;border:1px solid var(--border);">
<summary style="cursor:pointer;font-weight:500;font-size:14px;">📘 5 档评级体系完整说明</summary>
<table style="margin-top:12px;font-size:13px;">
<thead><tr><th>评级</th><th>含义</th><th>典型动作</th></tr></thead>
<tbody>
<tr><td><span class="tag tag-buy" style="padding:2px 8px;">Buy</span></td><td>强烈看多</td><td>大幅加仓 / 新开仓</td></tr>
<tr><td><span class="tag tag-overweight" style="padding:2px 8px;">Overweight</span></td><td><strong>超配</strong>：权重高于基准但不激进追价</td><td>持有 + 回调小幅加</td></tr>
<tr><td><span class="tag tag-hold" style="padding:2px 8px;">Hold</span></td><td>中性持有</td><td>已持不动</td></tr>
<tr><td><span class="tag tag-underweight" style="padding:2px 8px;">Underweight</span></td><td><strong>低配</strong>：权重低于基准但保留部分</td><td>分批减仓 20-35%</td></tr>
<tr><td><span class="tag tag-sell" style="padding:2px 8px;">Sell</span></td><td>强烈看空</td><td>清仓（应税户做 TLH）</td></tr>
</tbody>
</table>
<p style="margin-top:8px;font-size:12px;color:var(--fg-muted);">
<strong>关键区分</strong>：Overweight ≠ Buy（强度）· Underweight ≠ Sell（保留 vs 全清）· "基准权重" ≈ SPY 指数权重
</p>
</details>
"""

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{ticker} — PM 分析</title><style>{CSS}
table {{ background: var(--bg-subtle); border-radius: 6px; }}
.markdown {{ background: var(--bg-subtle); padding: 24px; border-radius: 8px; border: 1px solid var(--border); }}
.markdown h2 {{ color: var(--fg); margin-top: 24px; font-size: 18px; }}
.markdown strong {{ color: var(--fg); }}
.markdown table {{ width: 100%; margin: 16px 0; }}
.tag-buy {{ background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }}
.tag-overweight {{ background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }}
.tag-hold {{ background: var(--border); color: var(--fg-muted); }}
.tag-underweight {{ background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }}
.tag-sell {{ background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }}
.rating-tagline {{ color: var(--fg-muted); font-size: 14px; margin-top: -4px; margin-bottom: 8px; }}
</style></head><body><div class="container">
<h1>{ticker} <span class="tag tag-{row['rating'].lower()}" style="padding:4px 12px;font-size:14px;margin-left:8px;">{row['rating']}</span></h1>
<p class="rating-tagline">{rating_tagline}</p>
<p class="subtitle">分析日期: {row['trade_date']} · 录入时间: {row['created_at']}</p>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/decisions">← 回到所有评级</a> <a class="btn secondary" href="/">← 持仓页</a></div>
{glossary_html}
<div class="markdown">{decision_html}</div>
</div></body></html>"""


@app.get("/executions", response_class=HTMLResponse)
def executions_view():
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM executions ORDER BY trade_date DESC, execution_id DESC LIMIT 100"
        ).fetchall()

    body = ['<h1>📒 交易记录</h1>',
            f'<p class="subtitle">{len(rows)} 笔最近交易</p>',
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a> <a class="btn secondary" href="/decisions">PM 分析</a></div>']

    if not rows:
        body.append('<p style="color:var(--fg-muted);">还没记录任何交易 — 用持仓页的 Sell 按钮记录</p>')
    else:
        body.append('<table style="background:var(--bg-subtle);border-radius:8px;border:1px solid var(--border);">')
        body.append('<thead><tr><th>日期</th><th>Ticker</th><th>动作</th><th class="num">股数</th><th class="num">价格</th><th class="num">总额</th><th>账户</th><th>备注</th></tr></thead><tbody>')
        for r in rows:
            total = r["shares"] * r["price"]
            sign = "-" if r["action"] == "SELL" else "+"
            cls = "loss" if r["action"] == "SELL" else "gain"
            body.append(
                f'<tr>'
                f'<td>{r["trade_date"]}</td>'
                f'<td><strong>{r["symbol"]}</strong></td>'
                f'<td><span class="tag" style="background:var(--{"danger" if r["action"]=="SELL" else "success"});color:white;padding:2px 8px;">{r["action"]}</span></td>'
                f'<td class="num">{r["shares"]:.3f}</td>'
                f'<td class="num">${r["price"]:.2f}</td>'
                f'<td class="num {cls}">{sign}${total:,.0f}</td>'
                f'<td>{r["account_name"]}</td>'
                f'<td style="font-size:12px;color:var(--fg-muted);">{r["note"] or ""}</td>'
                f'</tr>'
            )
        body.append('</tbody></table>')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易记录</title><style>{CSS}</style></head><body><div class="container">
{''.join(body)}
</div></body></html>"""


@app.post("/run", response_class=HTMLResponse)
def run_analysis(
    mode: str = Form("tickers"),
    tickers: str = Form(""),
    sector: str = Form(""),
    instruction: str = Form(""),
):
    """Spawn analysis subprocess for tickers / sector / full portfolio."""
    import os as _os
    import subprocess as _sp
    from datetime import date, datetimetime as _dt

    run_id = _dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"/tmp/pm_run_{run_id}.log"
    status_path = f"/tmp/pm_run_{run_id}.status"
    project_root = Path(__file__).resolve().parent.parent
    env = dict(_os.environ)
    if instruction.strip():
        env["USER_TAX_CONTEXT"] = instruction.strip()

    if mode == "sector":
        if not sector.strip():
            return _render(message='<div class="msg error">没有提供行业描述</div>')
        cmd_chain = [
            f"uv run python scripts/analyze_sector.py --sector '{sector.strip()}'",
            "uv run python scripts/migrate_decisions_to_db.py",
        ]
        with open(status_path, "w") as f:
            f.write(f"queued\nmode=sector\nsector={sector}\ninstruction={instruction[:200]}\n")
        full_cmd = (
            f"echo running > {status_path} && "
            + " && ".join(cmd_chain)
            + f" && echo done > {status_path}; echo failed >> {status_path}"
        )
        _sp.Popen(["sh", "-c", full_cmd], env=env, cwd=str(project_root),
                  stdout=open(log_path, "w"), stderr=_sp.STDOUT, start_new_session=True)
        msg = (
            f'<div class="msg success">🏭 已启动行业分析 <code>{run_id}</code>: <strong>{sector}</strong>'
            f'{" · 已注入自定义指令" if instruction.strip() else ""}<br>'
            f'LLM 先列 ticker 再逐个跑（5-10 个 × 2 分钟 ≈ 15 分钟）· '
            f'<a href="/runs">查看进度</a></div>'
        )
        return _render(message=msg)

    if mode == "all":
        # Use all currently-held tickers
        latest = latest_snapshot_date()
        if latest:
            with connect() as conn:
                held_rows = conn.execute(
                    "SELECT DISTINCT symbol FROM positions_snapshot WHERE import_date = ?",
                    (latest,),
                ).fetchall()
            ticker_list = [r["symbol"] for r in held_rows]
        else:
            ticker_list = []
        if not ticker_list:
            return _render(message='<div class="msg error">没有持仓数据可分析</div>')
    else:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        if not ticker_list:
            return _render(message='<div class="msg error">没有提供 ticker</div>')

    # Classify each ticker: held vs new
    latest = latest_snapshot_date()
    held: set[str] = set()
    if latest:
        with connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM positions_snapshot WHERE import_date = ?",
                (latest,),
            ).fetchall()
            held = {r["symbol"] for r in rows}

    held_tickers = [t for t in ticker_list if t in held]
    new_tickers = [t for t in ticker_list if t not in held]

    run_id = _dt.now().strftime("%Y%m%d_%H%M%S")
    log_path = f"/tmp/pm_run_{run_id}.log"
    status_path = f"/tmp/pm_run_{run_id}.status"
    project_root = Path(__file__).resolve().parent.parent

    # Build env with optional user instruction override
    env = dict(_os.environ)
    if instruction.strip():
        env["USER_TAX_CONTEXT"] = instruction.strip()

    # Write status: queued
    with open(status_path, "w") as f:
        f.write(f"queued\ntickers={','.join(ticker_list)}\nheld={','.join(held_tickers)}\nnew={','.join(new_tickers)}\ninstruction={instruction[:200]}\n")

    cmds = []
    if held_tickers:
        cmds.append(f"uv run python scripts/analyze_holdings.py --tickers {','.join(held_tickers)}")
    for t in new_tickers:
        cmds.append(f"uv run python scripts/analyze_new_ticker.py --ticker {t}")
    cmds.append("uv run python scripts/migrate_decisions_to_db.py")
    full_cmd = (
        f"echo running > {status_path} && "
        + " && ".join(cmds)
        + f" && echo done > {status_path}; echo failed >> {status_path}"
    )

    _sp.Popen(
        ["sh", "-c", full_cmd],
        env=env,
        cwd=str(project_root),
        stdout=open(log_path, "w"),
        stderr=_sp.STDOUT,
        start_new_session=True,
    )

    desc = []
    if held_tickers:
        desc.append(f"{len(held_tickers)} 只持仓股: {', '.join(held_tickers)}")
    if new_tickers:
        desc.append(f"{len(new_tickers)} 只新股: {', '.join(new_tickers)}")
    instr_note = " · 已注入自定义指令" if instruction.strip() else ""
    msg = (
        f'<div class="msg success">🚀 已启动 run <code>{run_id}</code>: {" + ".join(desc)}{instr_note}<br>'
        f'<a href="/runs">查看进度</a> · 跑完后去 <a href="/decisions">PM 评级</a> 看结果</div>'
    )
    return _render(message=msg)


@app.get("/runs", response_class=HTMLResponse)
def runs_view():
    """List all pm_run_*.log files with their current status."""
    import glob as _g
    logs = sorted(_g.glob("/tmp/pm_run_*.log"), reverse=True)[:20]

    body = ['<h1>🏃 运行历史</h1>',
            '<p class="subtitle">最近 20 次分析任务</p>',
            '<div style="margin-bottom:16px;"><a class="btn secondary" href="/">← 持仓</a></div>']

    if not logs:
        body.append('<p style="color:var(--fg-muted);">还没运行过分析 — 用主页 🚀 按钮启动</p>')
    else:
        body.append('<table><thead><tr><th>Run ID</th><th>状态</th><th>触发 ticker</th><th>最近活动</th><th></th></tr></thead><tbody>')
        for log in logs:
            run_id = log.replace("/tmp/pm_run_", "").replace(".log", "")
            status_path = log.replace(".log", ".status")
            status = "unknown"
            tickers = ""
            if Path(status_path).exists():
                content = Path(status_path).read_text()
                status = content.splitlines()[0] if content else "?"
                for line in content.splitlines():
                    if line.startswith("tickers="):
                        tickers = line.split("=", 1)[1]
            # last log line
            last_line = ""
            try:
                with open(log) as f:
                    last_line = f.readlines()[-1] if f.readlines else ""
            except Exception:
                pass
            badge = {"running": "🟡 进行中", "done": "✅ 完成", "failed": "❌ 失败", "queued": "⏳ 排队"}.get(status, status)
            body.append(
                f'<tr><td><code>{run_id}</code></td>'
                f'<td>{badge}</td>'
                f'<td>{tickers}</td>'
                f'<td style="font-size:11px;color:var(--fg-muted);">{last_line[:80]}</td>'
                f'<td><a href="/runs/{run_id}" class="btn small secondary">日志</a></td></tr>'
            )
        body.append('</tbody></table>')

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>运行历史</title><style>{CSS}</style>
<meta http-equiv="refresh" content="10">
</head><body><div class="container">
{''.join(body)}
<p style="color:var(--fg-muted);font-size:12px;margin-top:24px;">页面每 10 秒自动刷新</p>
</div></body></html>"""


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str):
    log_path = f"/tmp/pm_run_{run_id}.log"
    if not Path(log_path).exists():
        return HTMLResponse(f"<p>Run {run_id} 不存在</p><a href='/runs'>← Back</a>")
    log_content = Path(log_path).read_text()[-10000:]  # tail
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Run {run_id}</title><style>{CSS}
pre {{ background: var(--bg-subtle); padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 12px; max-height: 70vh; overflow-y: auto; }}
</style></head><body><div class="container">
<h1>Run {run_id}</h1>
<div style="margin-bottom:16px;"><a class="btn secondary" href="/runs">← 运行列表</a></div>
<pre>{log_content}</pre>
</div></body></html>"""


@app.get("/api/positions")
def list_positions():
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM positions_snapshot WHERE import_date = ? ORDER BY current_value DESC",
            (latest,),
        ).fetchall()
    from fastapi.responses import JSONResponse
    return JSONResponse([dict(r) for r in rows])


if __name__ == "__main__":
    import uvicorn
    print("\n=== 持仓管理 server ===")
    print("浏览器打开: http://localhost:8765")
    print("Ctrl+C 停止\n")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
