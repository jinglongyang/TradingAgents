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
td.num { text-align: right; font-variant-numeric: tabular-nums; }
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
    <a class="btn secondary" href="/decisions">📋 PM 分析 & 评级</a>
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

    body = [f'<h1>📋 PM 分析 & 评级</h1><p class="subtitle">{len(rows)} 个 ticker · 最新分析按 ticker 自动汇总</p>',
            f'<div class="status">{chips}</div>',
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
</style></head><body><div class="container">
<h1>{ticker} <span class="tag tag-{row['rating'].lower()}" style="padding:4px 12px;font-size:14px;margin-left:8px;">{row['rating']}</span></h1>
<p class="subtitle">分析日期: {row['trade_date']} · 录入时间: {row['created_at']}</p>
<div style="margin-bottom:24px;"><a class="btn secondary" href="/decisions">← 回到所有评级</a> <a class="btn secondary" href="/">← 持仓页</a></div>
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
