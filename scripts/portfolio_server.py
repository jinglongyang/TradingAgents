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

from datetime import date
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
    openModal('sell-modal');
}
function openAccountEdit(acctId, acctName, currentBroker, currentType, currentOwner) {
    document.getElementById('acct-edit-id').value = acctId;
    document.getElementById('acct-edit-name-input').value = acctName;
    document.getElementById('acct-edit-broker-select').value = currentBroker || 'Fidelity';
    document.getElementById('acct-edit-type-select').value = currentType || 'Taxable';
    document.getElementById('acct-edit-owner-input').value = currentOwner || 'Self';
    document.getElementById('acct-edit-title').textContent = `编辑账户: ${acctName}`;
    openModal('acct-edit-modal');
}
function openEdit(snapshotId, sym, qty, price, cost, broker) {
    document.getElementById('edit-snapshot-id').value = snapshotId;
    document.getElementById('edit-symbol').value = sym;
    document.getElementById('edit-qty').value = qty;
    document.getElementById('edit-price').value = price;
    document.getElementById('edit-cost').value = cost;
    document.getElementById('edit-broker').value = broker || 'Fidelity';
    document.getElementById('edit-title').textContent = `Edit ${sym}`;
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
            ORDER BY account_name, current_value DESC
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
    <div class="account-meta">{len(items)} 仓位 · ${sub:,.0f}</div>
  </div>
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

  <div class="status">
    最新 snapshot: <strong>{latest}</strong> · {n_rows} 仓位 · 总价值 <strong>${total_val:,.0f}</strong> · 今天: {today}
  </div>

  {message}

  <div class="toolbar">
    <button onclick="openModal('add-modal')">➕ 添加新持仓</button>
    <button class="success" onclick="openModal('run-modal')">🚀 运行 PM 分析</button>
    <a class="btn secondary" href="/owners">👥 按 Owner 看</a>
    <a class="btn secondary" href="/drift">⚠️ Drift Alert</a>
    <a class="btn secondary" href="/decisions">📋 PM 分析 & 评级</a>
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
        <div class="field"><label>总成本</label><input name="cost_basis_total" type="number" step="0.01"></div>
      </div>
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
  </div>
</div>

<!-- Edit Modal -->
<div class="modal-backdrop" id="edit-modal">
  <div class="modal">
    <h3 id="edit-title">Edit</h3>
    <form method="post" action="/edit">
      <input type="hidden" id="edit-snapshot-id" name="snapshot_id">
      <div class="field"><label>Ticker <span class="hint">readonly</span></label><input id="edit-symbol" readonly style="background:var(--bg-subtle);"></div>
      <div class="row">
        <div class="field"><label>持股数</label><input id="edit-qty" name="quantity" type="number" step="0.001" required></div>
        <div class="field"><label>当前价</label><input id="edit-price" name="last_price" type="number" step="0.01"></div>
      </div>
      <div class="field"><label>总成本</label><input id="edit-cost" name="cost_basis_total" type="number" step="0.01"></div>
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
def index():
    init_db()
    return _render()


@app.post("/add", response_class=HTMLResponse)
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
        msg = f'<div class="msg success">✓ 已添加 <strong>{symbol.upper()}</strong> 到 {account_name}</div>'
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


@app.post("/account-edit", response_class=HTMLResponse)
def account_edit(
    account_id: str = Form(...),
    account_name: str = Form(...),
    account_type: str = Form(...),
    broker: str = Form(...),
    owner: str = Form(...),
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
        msg = (
            f'<div class="msg success">✓ 已更新 <strong>{n}</strong> 行 '
            f'(name=<strong>{account_name}</strong>, owner={owner}, type={account_type}, broker={broker})</div>'
        )
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


@app.post("/edit", response_class=HTMLResponse)
def edit(
    snapshot_id: int = Form(...),
    quantity: float = Form(...),
    last_price: Optional[float] = Form(None),
    cost_basis_total: Optional[float] = Form(None),
    broker: Optional[str] = Form(None),
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
            row = conn.execute("SELECT symbol FROM positions_snapshot WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        sym = row["symbol"] if row else "?"
        msg = f'<div class="msg success">✓ 已更新 <strong>{sym}</strong></div>'
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


@app.post("/sell", response_class=HTMLResponse)
def sell(
    symbol: str = Form(...),
    account_id: str = Form(...),
    account_name: str = Form(...),
    shares: float = Form(...),
    price: float = Form(...),
    note: Optional[str] = Form(None),
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

        proceeds = shares * price
        msg = f'<div class="msg success">✓ 卖出 {shares} 股 <strong>{symbol}</strong> @ ${price:.2f} = ${proceeds:,.0f}（已记 executions）</div>'
    except Exception as e:  # noqa: BLE001
        msg = f'<div class="msg error">✗ 错误: {e}</div>'
    return _render(message=msg)


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
    from datetime import datetime as _dt

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
