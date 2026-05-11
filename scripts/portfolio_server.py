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
from pathlib import Path
from typing import Optional

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from fastapi import FastAPI, Form  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

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
.tag-buy { background: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }
.tag-overweight { background: color-mix(in srgb, var(--success) 20%, transparent); color: var(--success); }
.tag-hold { background: var(--border); color: var(--fg-muted); }
.tag-underweight { background: color-mix(in srgb, var(--danger) 20%, transparent); color: var(--danger); }
.tag-sell { background: color-mix(in srgb, var(--danger) 30%, transparent); color: var(--danger); }
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
    """Return (accounts, n_rows, total_value).

    `accounts` is a list of dicts ready for `_holdings.html` to render. All
    HTML/formatting decisions are made in the template, not here.
    """
    today = date.today().isoformat()
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   COALESCE(t.last_price, p.last_price) AS authoritative_price,
                   COALESCE(t.last_price, p.last_price) * p.quantity AS authoritative_value
            FROM positions_snapshot p
            LEFT JOIN tickers t ON t.symbol = p.symbol
            WHERE p.import_date = ?
            ORDER BY p.account_name, p.symbol
            """,
            (latest,) if latest else (today,),
        ).fetchall()

    if not rows:
        return [], 0, 0.0

    rows = [
        {**dict(r),
         "last_price": r["authoritative_price"] or 0,
         "current_value": r["authoritative_value"] or 0}
        for r in rows
    ]

    by_account: dict[tuple[str, str], list] = {}
    for r in rows:
        by_account.setdefault((r["account_id"], r["account_name"]), []).append(r)

    accounts: list[dict] = []
    total = 0.0
    for (aid, aname), items in sorted(
        by_account.items(),
        key=lambda kv: -sum(r["current_value"] for r in kv[1]),
    ):
        subtotal = sum(r["current_value"] for r in items)
        total += subtotal
        atype = items[0]["account_type"]
        positions = []
        for r in items:
            cost = r["cost_basis_total"] or 0
            pl_pct = ((r["current_value"] - cost) / cost * 100) if cost else 0.0
            positions.append({
                "snapshot_id": r["snapshot_id"],
                "symbol": r["symbol"],
                "quantity": r["quantity"],
                "last_price": r["last_price"],
                "current_value": r["current_value"],
                "cost": cost,
                "pl_pct": pl_pct,
                "pl_class": "gain" if pl_pct >= 0 else "loss",
                "broker": r["broker"] or "Fidelity",
            })
        accounts.append({
            "account_id": aid,
            "account_name": aname,
            "account_type": atype,
            "account_type_lower": atype.lower(),
            "broker": items[0]["broker"] or "—",
            "owner": items[0]["owner"] or "—",
            "anchor": "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", aid),
            "subtotal": subtotal,
            "n_positions": len(items),
            "positions": positions,
        })
    return accounts, len(rows), total


def _ticker_pool() -> list[dict[str, str]]:
    """Symbols to suggest in the autocomplete: union of tickers table
    and any symbols held in the latest snapshot that aren't there yet."""
    out: list[dict[str, str]] = []
    with connect() as conn:
        for r in conn.execute(
            """
            SELECT COALESCE(t.symbol, ps.symbol) AS s,
                   COALESCE(t.name, '') AS n
              FROM positions_snapshot ps
              LEFT JOIN tickers t ON t.symbol = ps.symbol
             WHERE ps.import_date = COALESCE((SELECT MAX(import_date) FROM positions_snapshot), '')
             GROUP BY COALESCE(t.symbol, ps.symbol)
             UNION
            SELECT symbol AS s, COALESCE(name, '') AS n FROM tickers
            """,
        ).fetchall():
            out.append({"s": r["s"], "n": r["n"] or ""})
    out.sort(key=lambda x: x["s"])
    return out


def _render(message: str = "", level: str = "success"):
    """Render the main holdings page via Jinja2.

    `message` is plain text (may contain inline HTML if needed, gets ``|safe``
    in the template); `level` is one of success / error / info / warning.
    Empty `message` means no flash banner.
    """
    import json as _json
    accounts, n_rows, total_val = _build_holdings_view()
    ticker_pool = _ticker_pool()
    flash = {"text": message, "level": level} if message else None
    return templates.TemplateResponse(
        _dummy_request(),
        "index.html",
        {
            "css": CSS,
            "extra_js": JS,
            "accounts": accounts,
            "n_rows": n_rows,
            "total_val": total_val,
            "latest": latest_snapshot_date() or "(none)",
            "today": date.today().isoformat(),
            "ticker_pool": ticker_pool,
            "ticker_json": _json.dumps(ticker_pool, ensure_ascii=False),
            "flash": flash,
        },
    )




@app.get("/", response_class=HTMLResponse)
def index(msg: str = ""):
    init_db()
    return _render(message=msg or "")


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
        symbol_clean = symbol.strip().upper()
        price = last_price or 0.0

        # Auto-fetch current price from yfinance if not provided
        if price <= 0:
            try:
                import yfinance as _yf
                from datetime import datetime as _dt
                hist = _yf.Ticker(symbol_clean).history(period="5d")
                if len(hist) > 0:
                    price = float(hist["Close"].iloc[-1])
                    # Upsert canonical price into tickers
                    with connect() as conn:
                        conn.execute(
                            """
                            INSERT INTO tickers (symbol, last_price, last_updated)
                            VALUES (?, ?, ?)
                            ON CONFLICT(symbol) DO UPDATE SET
                                last_price = excluded.last_price,
                                last_updated = excluded.last_updated
                            """,
                            (symbol_clean, price, _dt.now().isoformat(timespec="seconds")),
                        )
            except Exception:
                pass  # leave price=0 if yfinance fails

        snap_id = add_position(
            account_id=account_id.strip(), account_name=account_name.strip(),
            account_type=account_type, symbol=symbol_clean,
            quantity=quantity, last_price=price,
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
    except Exception as e:  # noqa: BLE001
        return _render(message=f"✗ 错误: {e}", level="error")


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
            return _render(message="行不存在", level="error")
        conn.execute("DELETE FROM positions_snapshot WHERE snapshot_id = ?", (snapshot_id,))
        anchor = "acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", row["account_id"])
    return RedirectResponse(url=f"/#{anchor}", status_code=303)


@app.post("/enrich-tickers")
def enrich_tickers():
    """Pull sector / industry / name / market_cap into tickers table from yfinance.

    Slower than /update-prices (one .info call per ticker, no batch API) — run
    occasionally to fill in metadata, then use /update-prices for daily price refresh.
    """
    import yfinance as _yf
    from urllib.parse import quote as _quote

    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM tickers WHERE sector IS NULL OR name IS NULL ORDER BY symbol"
        ).fetchall()
        targets = [r["symbol"] for r in rows]

    if not targets:
        return RedirectResponse(url="/?msg=" + _quote("所有 ticker 元数据已完整"), status_code=303)

    updated = 0
    failed: list[str] = []
    with connect() as conn:
        for sym in targets:
            try:
                info = _yf.Ticker(sym).info
                conn.execute(
                    """
                    UPDATE tickers SET
                        name = COALESCE(?, name),
                        sector = COALESCE(?, sector),
                        industry = COALESCE(?, industry),
                        market_cap = COALESCE(?, market_cap)
                    WHERE symbol = ?
                    """,
                    (
                        info.get("longName") or info.get("shortName"),
                        info.get("sector"),
                        info.get("industry"),
                        info.get("marketCap"),
                        sym,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    updated += 1
            except Exception:
                failed.append(sym)

    msg = f"✓ 拉了 {updated}/{len(targets)} ticker 元数据"
    if failed:
        msg += f" · 失败: {', '.join(failed[:8])}"
    return RedirectResponse(url="/?msg=" + _quote(msg), status_code=303)


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
        return _render(
            message=f"✓ 已删除账户 <code>{account_id[:24]}</code> 的 <strong>{n}</strong> 行持仓（executions 保留）",
            level="success",
        )
    except Exception as e:  # noqa: BLE001
        return _render(message=f"✗ 错误: {e}", level="error")


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
        return _render(message=f"✗ 错误: {e}", level="error")


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
        from datetime import datetime as _dt
        price = last_price or 0.0
        cost = cost_basis_total or 0.0
        with connect() as conn:
            # First fetch the row's symbol so we can update tickers too
            existing = conn.execute(
                "SELECT symbol, account_id FROM positions_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            sym = existing["symbol"] if existing else None
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
            # If user provided a price, propagate to the canonical tickers table
            # and sync every other row holding the same symbol so all accounts agree.
            if sym and price > 0:
                conn.execute(
                    """
                    INSERT INTO tickers (symbol, last_price, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        last_price = excluded.last_price,
                        last_updated = excluded.last_updated
                    """,
                    (sym, price, _dt.now().isoformat(timespec="seconds")),
                )
                conn.execute(
                    """
                    UPDATE positions_snapshot
                    SET last_price = ?, current_value = ? * quantity
                    WHERE symbol = ? AND snapshot_id != ?
                    """,
                    (price, price, sym, snapshot_id),
                )
            row = existing
        # Always derive from account_id, ignore stale redirect_anchor
        anchor = ("acct-" + re.sub(r"[^a-zA-Z0-9_-]", "_", row["account_id"])) if row else ""
        return RedirectResponse(url=f"/#{anchor}" if anchor else "/", status_code=303)
    except Exception as e:  # noqa: BLE001
        return _render(message=f"✗ 错误: {e}", level="error")


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
        return _render(message=f"✗ 错误: {e}", level="error")


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
        decision_row = conn.execute(
            "SELECT * FROM decisions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        executions = [dict(e) for e in conn.execute(
            "SELECT * FROM executions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 10",
            (ticker,),
        ).fetchall()]
        lots_count = conn.execute(
            "SELECT COUNT(*) c FROM cost_basis_lots WHERE symbol = ?", (ticker,),
        ).fetchone()["c"]

    positions: list[dict] = []
    for r in pos_rows:
        cost = r["cost_basis_total"] or 0
        pl_pct = (r["current_value"] - cost) / cost * 100 if cost else 0
        positions.append({
            "account_name": r["account_name"],
            "account_type": r["account_type"],
            "broker": r["broker"] or "—",
            "owner": r["owner"] or "—",
            "quantity": r["quantity"],
            "current_value": r["current_value"],
            "cost": cost, "pl_pct": pl_pct,
            "pl_class": "gain" if pl_pct >= 0 else "loss",
        })

    total_value = sum(r["current_value"] for r in pos_rows)
    total_cost = sum(r["cost_basis_total"] or 0 for r in pos_rows)
    total_shares = sum(r["quantity"] for r in pos_rows)
    pl = total_value - total_cost
    pl_pct = pl / total_cost * 100 if total_cost else 0
    totals = {
        "value": total_value, "cost": total_cost, "shares": total_shares,
        "pl": pl, "pl_pct": pl_pct,
        "pl_class": "gain" if pl >= 0 else "loss",
    }

    decision = None
    if decision_row:
        actions = _json.loads(decision_row["account_actions"] or "[]")
        decision = {
            "rating": decision_row["rating"],
            "trade_date": decision_row["trade_date"],
            "n_actions": len(actions),
        }

    return templates.TemplateResponse(
        _dummy_request(), "lookup.html",
        {
            "css": CSS, "ticker": ticker, "positions": positions, "totals": totals,
            "decision": decision, "executions": executions, "lots_count": lots_count,
        },
    )


@app.get("/lots", response_class=HTMLResponse)
def lots_view(symbol: str = ""):
    """Cost-basis lot ledger — each purchase tracked individually."""
    with connect() as conn:
        if symbol:
            raw = conn.execute(
                "SELECT * FROM cost_basis_lots WHERE symbol = ? ORDER BY purchase_date",
                (symbol.upper(),),
            ).fetchall()
        else:
            raw = conn.execute(
                "SELECT * FROM cost_basis_lots ORDER BY purchase_date DESC LIMIT 200"
            ).fetchall()

    from collections import defaultdict
    today = date.today()
    by_sym = defaultdict(list)
    for r in raw:
        purchase = datetime.strptime(r["purchase_date"], "%Y-%m-%d").date()
        days = (today - purchase).days
        by_sym[r["symbol"]].append({
            **dict(r),
            "days_held": days,
            "term": "长期" if days > 365 else f"短期 ({365 - days} 天后变长期)",
            "subtotal": r["shares"] * r["cost_per_share"],
        })

    groups = [
        {
            "symbol": sym,
            "total_shares": sum(l["shares"] for l in lots),
            "total_cost": sum(l["subtotal"] for l in lots),
            "lots": lots,
        }
        for sym, lots in sorted(by_sym.items())
    ]

    return templates.TemplateResponse(
        _dummy_request(), "lots.html",
        {"css": CSS, "groups": groups},
    )


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

    return templates.TemplateResponse(
        _dummy_request(), "wash_sale.html",
        {
            "css": CSS, "alerts": alerts,
            "n_sells": len(sells), "n_buys": len(buys),
        },
    )


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
        return _error_page("至少需要 2 个持仓才能计算相关性")

    try:
        data = yf.download(tickers, period="90d", progress=False, auto_adjust=True)["Close"]
        # Handle single-ticker DataFrame edge case
        if isinstance(data, pd.Series):
            data = data.to_frame()
        returns = data.pct_change().dropna()
        corr = returns.corr()
    except Exception as e:
        return _error_page(f"yfinance 拉数据失败: <code>{e}</code>")

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

    return templates.TemplateResponse(
        _dummy_request(), "correlation.html",
        {
            "css": CSS, "n": n,
            "svg": "".join(svg),
            "high_pairs": high_pairs,
        },
    )


@app.get("/tlh", response_class=HTMLResponse)
def tlh_view():
    """Tax-Loss Harvest candidates: taxable accounts with unrealized losses."""
    latest = latest_snapshot_date()
    with connect() as conn:
        raw = conn.execute(
            """
            SELECT * FROM positions_snapshot
            WHERE import_date = ? AND account_type = 'Taxable'
              AND current_value > 0 AND cost_basis_total > 0
              AND current_value < cost_basis_total
            ORDER BY (cost_basis_total - current_value) DESC
            """,
            (latest,) if latest else (date.today().isoformat(),),
        ).fetchall()

    rows = []
    total_loss = 0.0
    total_savings = 0.0
    for r in raw:
        loss = r["cost_basis_total"] - r["current_value"]
        loss_pct = loss / r["cost_basis_total"] * 100 if r["cost_basis_total"] else 0
        savings_lt = loss * 0.238  # 20% LTCG + 3.8% NIIT
        savings_st = loss * 0.37
        total_loss += loss
        total_savings += savings_lt
        rows.append({
            **dict(r),
            "loss": loss, "loss_pct": loss_pct,
            "savings_lt": savings_lt, "savings_st": savings_st,
        })

    return templates.TemplateResponse(
        _dummy_request(), "tlh.html",
        {
            "css": CSS, "rows": rows,
            "total_loss": total_loss, "total_savings": total_savings,
        },
    )


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

    for r in rows:
        weight_class = ""
        if r["weight"] > 15:
            weight_class = "loss"
        elif r["weight"] < 0.3 and r["rating"] in ("Buy", "Overweight"):
            weight_class = "loss"
        r["weight_class"] = weight_class

    return templates.TemplateResponse(
        _dummy_request(), "drift.html",
        {"css": CSS, "rows": rows},
    )


@app.get("/performance", response_class=HTMLResponse)
def performance_view(windows: str = "1,5,30,90"):
    """PM forward-test: actual return + alpha vs SPY for each decision."""
    import yfinance as yf
    from datetime import datetime as _dt, timedelta as _td

    try:
        window_days = [int(w) for w in windows.split(",") if w.strip()]
    except ValueError:
        window_days = [1, 5, 30, 90]

    with connect() as conn:
        decisions = conn.execute(
            "SELECT trade_date, symbol, rating FROM decisions ORDER BY trade_date, symbol"
        ).fetchall()

    if not decisions:
        return _error_page("还没有任何 PM 决策 — 先去主页 🚀 跑一次分析")

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

    # Maturity hint: for each window, how many decisions are old enough to settle?
    today = date.today()
    decision_ages = [
        (today - _dt.strptime(d["trade_date"], "%Y-%m-%d").date()).days
        for d in decisions
    ]
    oldest = max(decision_ages) if decision_ages else 0
    next_window = next((w for w in sorted(window_days) if w > oldest), None)
    if next_window is not None:
        days_until = next_window - oldest
        maturity_hint = (
            f"最早的决策 <strong>{oldest}</strong> 天前 · "
            f"下一个窗口 <strong>{next_window}d</strong> 还要 <strong>{days_until}</strong> 天才有数据"
        )
    else:
        maturity_hint = f"最早的决策 <strong>{oldest}</strong> 天前 · 所有窗口都已成熟"

    return templates.TemplateResponse(
        _dummy_request(), "performance.html",
        {
            "css": CSS, "rows": rows, "summary": summary,
            "n_decisions": len(decisions),
            "window_days": window_days,
            "rating_order": _RATING_ORDER,
            "maturity_hint": maturity_hint,
        },
    )


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
        return _error_page("yfinance 拉取板块数据失败")

    spy_d30 = next((r["d30"] for r in results if r["symbol"] == "SPY"), 0) or 0
    spy_d90 = next((r["d90"] for r in results if r["symbol"] == "SPY"), 0) or 0

    def cls(v):
        if v is None:
            return ""
        return "gain" if v > 0 else "loss"

    def fmt(v):
        return "—" if v is None else f"{v:+.2f}%"

    rows = []
    for r in sorted(results, key=lambda x: -(x["d30"] or -999)):
        rel30 = (r["d30"] - spy_d30) if r["d30"] is not None else None
        rel90 = (r["d90"] - spy_d90) if r["d90"] is not None else None
        rows.append({
            **r, "is_spy": r["symbol"] == "SPY",
            "cls_d1": cls(r["d1"]), "fmt_d1": fmt(r["d1"]),
            "cls_d5": cls(r["d5"]), "fmt_d5": fmt(r["d5"]),
            "cls_d30": cls(r["d30"]), "fmt_d30": fmt(r["d30"]),
            "cls_d90": cls(r["d90"]), "fmt_d90": fmt(r["d90"]),
            "cls_rel30": cls(rel30), "fmt_rel30": fmt(rel30),
            "cls_rel90": cls(rel90), "fmt_rel90": fmt(rel90),
        })

    return templates.TemplateResponse(
        _dummy_request(), "sectors.html",
        {"css": CSS, "rows": rows},
    )


@app.get("/tickers", response_class=HTMLResponse)
def tickers_view():
    """Canonical ticker registry — single source of truth for prices/metadata."""
    with connect() as conn:
        ref_counts = {r["symbol"]: r["c"] for r in conn.execute(
            """
            SELECT symbol, COUNT(*) c FROM positions_snapshot
            WHERE import_date = (SELECT MAX(import_date) FROM positions_snapshot)
            GROUP BY symbol
            """
        ).fetchall()}
        rows = [
            {**dict(r), "n_refs": ref_counts.get(r["symbol"], 0)}
            for r in conn.execute("SELECT * FROM tickers ORDER BY symbol").fetchall()
        ]
    return templates.TemplateResponse(
        _dummy_request(), "tickers.html",
        {
            "css": CSS,
            "rows": rows,
            "total_refs": sum(ref_counts.values()),
            "n_with_meta": sum(1 for r in rows if r["sector"]),
        },
    )


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
        return _error_page("当前没有持仓数据 — 先 import broker CSV 或 ➕ 添加持仓")

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

    def build_pie(data: list[tuple[str, float]], title: str, size: int = 320) -> dict | None:
        total_v = sum(v for _, v in data)
        if not total_v:
            return None
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
            arcs.append(
                f'<path d="{path}" fill="{color}" stroke="var(--bg)" stroke-width="1.5">'
                f'<title>{label}: ${value:,.0f} ({value/total_v*100:.1f}%)</title>'
                f'</path>'
            )
            start_angle = end_angle
        legend = [
            {
                "label": label, "value": value,
                "pct": value / total_v * 100,
                "color": PALETTE[i % len(PALETTE)],
            }
            for i, (label, value) in enumerate(data)
        ]
        return {"title": title, "size": size, "arcs": "".join(arcs), "legend": legend}

    pies = [
        p for p in [
            build_pie(aggregate_tickers(), "🏷️ 按 Ticker 权重"),
            build_pie(aggregate("owner"), "👥 按 Owner"),
            build_pie(aggregate("account_type"), "💰 按账户类型（税务桶）"),
            build_pie(aggregate("broker"), "🏦 按 Broker"),
        ] if p
    ]
    return templates.TemplateResponse(
        _dummy_request(), "charts.html",
        {"css": CSS, "pies": pies, "total": total, "latest": latest},
    )


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

    pt_re = _re.compile(r"Price Target.*?\$?([\d.]+)")
    rows_out = []
    for sym in sorted(evolving):
        seq = evolving[sym]
        rating_seq = [r["rating"] for r in seq]
        pt_seq = []
        for r in seq:
            m = pt_re.search(r["final_decision"] or "")
            pt_seq.append(f"${m.group(1)}" if m else "—")
        n_unique = len(set(rating_seq))
        if n_unique == 1:
            stability = "✅ 完全一致"
        elif n_unique == 2:
            stability = "🟡 轻微变化"
        else:
            stability = "⚠️ 明显分歧"
        rows_out.append({
            "symbol": sym,
            "rating_seq": rating_seq,
            "pt_seq": pt_seq,
            "stability": stability,
        })

    return templates.TemplateResponse(
        _dummy_request(), "thesis_evolution.html",
        {
            "css": CSS, "rows": rows_out,
            "n_total": len(by_symbol),
            "n_stable": len(stable),
        },
    )


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

    owners: list[dict] = []
    for owner in sorted(by_owner, key=lambda o: -sum(r["current_value"] for r in by_owner[o])):
        items = by_owner[owner]
        subtotal = sum(r["current_value"] for r in items)
        type_breakdown: dict[str, float] = {}
        for r in items:
            type_breakdown[r["account_type"]] = type_breakdown.get(r["account_type"], 0) + r["current_value"]
        tb = " · ".join(
            f"{t}: ${v/1000:.0f}K"
            for t, v in sorted(type_breakdown.items(), key=lambda x: -x[1])
        )
        by_sym: dict[str, dict] = {}
        for r in items:
            s = by_sym.setdefault(r["symbol"], {"value": 0, "cost": 0, "shares": 0, "accounts": 0})
            s["value"] += r["current_value"]
            s["cost"] += r["cost_basis_total"] or 0
            s["shares"] += r["quantity"]
            s["accounts"] += 1
        top = []
        for sym, d in sorted(by_sym.items(), key=lambda kv: -kv[1]["value"])[:10]:
            pl_pct = ((d["value"] - d["cost"]) / d["cost"] * 100) if d["cost"] else 0
            top.append({
                "symbol": sym, "shares": d["shares"], "value": d["value"],
                "cost": d["cost"], "pl_pct": pl_pct,
                "pl_class": "gain" if pl_pct >= 0 else "loss",
                "accounts": d["accounts"],
            })
        owners.append({
            "owner": owner, "n_positions": len(items), "subtotal": subtotal,
            "pct": subtotal / grand_total * 100 if grand_total else 0,
            "type_breakdown": tb, "n_unique": len(by_sym), "top": top,
        })

    return templates.TemplateResponse(
        _dummy_request(), "owners.html",
        {
            "css": CSS, "owners": owners,
            "n_positions": len(rows), "grand_total": grand_total,
        },
    )


_RATING_ORDER = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
_RATING_RANK = {r: i for i, r in enumerate(_RATING_ORDER)}
_RATING_TAGLINE = {
    "Buy": "强烈看多 — 基本面+趋势+估值全部支持，建议大幅加仓",
    "Overweight": "超配 — 看多但克制，组合权重应高于基准（SPY 指数权重）",
    "Hold": "持有 — 中性，基本面 OK 但当前不是好的进场点",
    "Underweight": "低配 — 看空但不清仓，组合权重应低于基准，建议部分减仓",
    "Sell": "强烈看空 — 基本面恶化或重大风险，建议清仓",
}


@app.get("/decisions", response_class=HTMLResponse)
def decisions_view():
    import json as _json
    with connect() as conn:
        raw = conn.execute("SELECT * FROM decisions ORDER BY rating, symbol").fetchall()

    counts: dict[str, int] = {r: 0 for r in _RATING_ORDER}
    rows = []
    for r in raw:
        counts[r["rating"]] = counts.get(r["rating"], 0) + 1
        actions = _json.loads(r["account_actions"]) if r["account_actions"] else []
        rows.append({
            "symbol": r["symbol"],
            "rating": r["rating"],
            "trade_date": r["trade_date"],
            "n_total": len(actions),
            "n_actions": sum(1 for a in actions if a.get("action") not in (None, "Hold")),
            "reflection": r["reflection"] or "",
        })
    rows.sort(key=lambda r: (_RATING_RANK.get(r["rating"], 99), r["symbol"]))

    return templates.TemplateResponse(
        _dummy_request(), "decisions_list.html",
        {
            "css": CSS, "rows": rows,
            "rating_order": _RATING_ORDER, "counts": counts,
        },
    )


@app.get("/decisions/{ticker}", response_class=HTMLResponse)
def decision_detail(ticker: str):
    import markdown as _md
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM decisions WHERE symbol = ? ORDER BY trade_date DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
    if not row:
        return _error_page(
            f"<strong>{ticker}</strong> 还没有 PM 决策记录",
            back_url="/decisions", back_label="回到评级列表",
        )

    decision_html = _md.markdown(row["final_decision"], extensions=["tables", "fenced_code"])
    return templates.TemplateResponse(
        _dummy_request(), "decision_detail.html",
        {
            "css": CSS, "ticker": ticker,
            "rating": row["rating"],
            "rating_tagline": _RATING_TAGLINE.get(row["rating"], ""),
            "trade_date": row["trade_date"],
            "created_at": row["created_at"],
            "decision_html": decision_html,
        },
    )


@app.get("/executions", response_class=HTMLResponse)
def executions_view():
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM executions ORDER BY trade_date DESC, execution_id DESC LIMIT 100"
        ).fetchall()]
    return templates.TemplateResponse(
        _dummy_request(), "executions.html",
        {"css": CSS, "rows": rows},
    )


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
            return _render(message='没有提供行业描述', level='error')
        cmd_chain = [
            f"uv run python scripts/analyze_sector.py --sector '{sector.strip()}'",
            "uv run python scripts/migrate_decisions_to_db.py",
        ]
        with open(status_path, "w") as f:
            f.write("queued\n")
        with open(f"/tmp/pm_run_{run_id}.meta", "w") as f:
            f.write(f"mode=sector\nsector={sector}\ninstruction={instruction[:500]}\n")
        full_cmd = (
            f"echo running > {status_path} && "
            + " && ".join(cmd_chain)
            + f" && echo done > {status_path} || echo failed > {status_path}"
        )
        proc = _sp.Popen(["sh", "-c", full_cmd], env=env, cwd=str(project_root),
                  stdout=open(log_path, "w"), stderr=_sp.STDOUT, start_new_session=True)
        with open(f"/tmp/pm_run_{run_id}.pid", "w") as f:
            f.write(str(proc.pid))
        msg = (
            f'🏭 已启动行业分析 <code>{run_id}</code>: <strong>{sector}</strong>'
            f'{" · 已注入自定义指令" if instruction.strip() else ""}<br>'
            f'LLM 先列 ticker 再逐个跑（5-10 个 × 2 分钟 ≈ 15 分钟）· '
            f'<a href="/runs">查看进度</a>'
        )
        return _render(message=msg, level="success")

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
            return _render(message='没有持仓数据可分析', level='error')
    else:
        # Accept any of: comma / space / tab / Chinese comma / Chinese dunhao / semicolon
        ticker_list = [t.upper() for t in re.split(r"[\s,，、;]+", tickers.strip()) if t]
        if not ticker_list:
            return _render(message='没有提供 ticker', level='error')

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

    # Status file holds just the state word (queued/running/done/failed);
    # the shell command rewrites it as the run progresses. Metadata that
    # must survive those rewrites goes to .meta.
    with open(status_path, "w") as f:
        f.write("queued\n")
    meta_path = f"/tmp/pm_run_{run_id}.meta"
    with open(meta_path, "w") as f:
        f.write(
            f"tickers={','.join(ticker_list)}\n"
            f"held={','.join(held_tickers)}\n"
            f"new={','.join(new_tickers)}\n"
            f"instruction={instruction[:500]}\n"
        )

    cmds = []
    if held_tickers:
        cmds.append(f"uv run python scripts/analyze_holdings.py --tickers {','.join(held_tickers)}")
    for t in new_tickers:
        cmds.append(f"uv run python scripts/analyze_new_ticker.py --ticker {t}")
    cmds.append("uv run python scripts/migrate_decisions_to_db.py")
    full_cmd = (
        f"echo running > {status_path} && "
        + " && ".join(cmds)
        + f" && echo done > {status_path} || echo failed > {status_path}"
    )

    proc = _sp.Popen(
        ["sh", "-c", full_cmd],
        env=env,
        cwd=str(project_root),
        stdout=open(log_path, "w"),
        stderr=_sp.STDOUT,
        start_new_session=True,
    )
    # Persist PID for cancel + liveness checks. start_new_session=True means
    # this PID is also the process group leader — killpg(pid, SIGTERM) kills
    # the entire chain (sh -> uv -> python).
    with open(f"/tmp/pm_run_{run_id}.pid", "w") as f:
        f.write(str(proc.pid))

    desc = []
    if held_tickers:
        desc.append(f"{len(held_tickers)} 只持仓股: {', '.join(held_tickers)}")
    if new_tickers:
        desc.append(f"{len(new_tickers)} 只新股: {', '.join(new_tickers)}")
    instr_note = " · 已注入自定义指令" if instruction.strip() else ""
    msg = (
        f'🚀 已启动 run <code>{run_id}</code>: {" + ".join(desc)}{instr_note}<br>'
        f'<a href="/runs">查看进度</a> · 跑完后去 <a href="/decisions">PM 评级</a> 看结果'
    )
    return _render(message=msg, level="success")


def _find_output_dir_for_run(log_path: str) -> Path | None:
    """Scan a pm_run log for the 'All outputs written to' line to locate the
    analyze_holdings output dir. Returns None if not yet written."""
    try:
        content = Path(log_path).read_text()
    except Exception:
        return None
    m = re.search(r"All outputs written to (\S+)", content)
    if not m:
        return None
    p = Path(m.group(1))
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    return p if p.exists() else None


_STATUS_BADGE = {
    "running": "🟡 进行中",
    "done": "✅ 完成",
    "failed": "❌ 失败",
    "queued": "⏳ 排队",
    "cancelled": "🛑 已取消",
    "stalled": "⏱️ 超时无活动",
    "orphan": "💀 进程已退出",
}

# A run is considered stalled if its log file has not been touched
# for this many seconds — typical analyze_holdings writes log lines
# every 2-3s during LLM calls, so 5 minutes is generous.
_STALLED_SECONDS = 300


def _pid_alive(pid: int) -> bool:
    try:
        import os as _os
        _os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _classify_run(log_path: str, status_path: str, pid_path: str) -> tuple[str, int | None]:
    """Refine the raw status with liveness + stall detection.

    Returns (effective_status, pid_or_None). Effective statuses:
    queued / running / done / failed / cancelled / stalled / orphan.
    """
    import os as _os
    raw = "unknown"
    if Path(status_path).exists():
        content = Path(status_path).read_text()
        raw = content.splitlines()[0].strip() if content else "unknown"

    pid: int | None = None
    if Path(pid_path).exists():
        try:
            pid = int(Path(pid_path).read_text().strip())
        except ValueError:
            pid = None

    if raw not in ("running", "queued"):
        return raw, pid

    # raw says "running" — verify
    if pid is not None and not _pid_alive(pid):
        return "orphan", pid

    # PID alive (or unknown) — check log mtime for stall
    try:
        age = _os.path.getmtime(log_path)
        import time as _t
        if _t.time() - age > _STALLED_SECONDS:
            return "stalled", pid
    except OSError:
        pass

    return raw, pid


def _dummy_request():
    from starlette.requests import Request as _Req
    return _Req(scope={"type": "http", "headers": [], "method": "GET", "path": "/"})


def _error_page(message: str, *, title: str = "⚠️ 出错了",
                back_url: str = "/", back_label: str = "返回",
                status_code: int = 200):
    return templates.TemplateResponse(
        _dummy_request(), "error.html",
        {
            "css": CSS, "title": title, "message": message,
            "back_url": back_url, "back_label": back_label,
        },
        status_code=status_code,
    )


@app.get("/runs", response_class=HTMLResponse)
def runs_view():
    """List all pm_run_*.log files with their current status."""
    import glob as _g
    logs = sorted(_g.glob("/tmp/pm_run_*.log"), reverse=True)[:20]
    runs = []
    for log in logs:
        run_id = log.replace("/tmp/pm_run_", "").replace(".log", "")
        status_path = log.replace(".log", ".status")
        pid_path = log.replace(".log", ".pid")
        meta_path = log.replace(".log", ".meta")
        effective, pid = _classify_run(log, status_path, pid_path)
        meta: dict[str, str] = {}
        # Prefer .meta; fall back to status for legacy runs that stored
        # tickers/instruction there.
        for p in (meta_path, status_path):
            if Path(p).exists():
                for line in Path(p).read_text().splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        meta.setdefault(k, v)
        tickers = meta.get("tickers", "") or meta.get("sector", "")
        instruction = meta.get("instruction", "")
        last_line = ""
        try:
            with open(log) as f:
                lines = f.readlines()
            last_line = lines[-1] if lines else ""
        except Exception:
            pass
        runs.append({
            "run_id": run_id,
            "status": effective,
            "badge": _STATUS_BADGE.get(effective, effective),
            "tickers": tickers,
            "instruction": instruction,
            "last_line": last_line,
            "has_result": _find_output_dir_for_run(log) is not None,
            "can_cancel": effective in ("running", "queued", "stalled"),
            "can_retry": effective in ("failed", "cancelled", "stalled", "orphan") and bool(tickers),
        })
    return templates.TemplateResponse(
        _dummy_request(),
        "runs_list.html",
        {"css": CSS, "runs": runs},
    )


@app.post("/runs/{run_id}/cancel")
def run_cancel(run_id: str):
    """Send SIGTERM to the entire process group of a still-running job."""
    import os as _os
    import signal as _sig
    pid_path = f"/tmp/pm_run_{run_id}.pid"
    status_path = f"/tmp/pm_run_{run_id}.status"
    if not Path(pid_path).exists():
        return RedirectResponse(url="/runs?msg=PID+file+missing", status_code=303)
    try:
        pid = int(Path(pid_path).read_text().strip())
    except ValueError:
        return RedirectResponse(url="/runs?msg=Invalid+PID", status_code=303)
    try:
        # start_new_session=True made pid the PG leader — kill the whole tree
        _os.killpg(pid, _sig.SIGTERM)
    except ProcessLookupError:
        pass
    # Wait briefly, then SIGKILL anything that survived
    import time as _t
    _t.sleep(0.5)
    try:
        _os.killpg(pid, _sig.SIGKILL)
    except ProcessLookupError:
        pass
    with open(status_path, "w") as f:
        f.write("cancelled\n")
    return RedirectResponse(url="/runs", status_code=303)


@app.post("/runs/{run_id}/retry")
def run_retry(run_id: str):
    """Re-spawn with the same tickers + instruction as the original run."""
    meta_path = f"/tmp/pm_run_{run_id}.meta"
    legacy_status_path = f"/tmp/pm_run_{run_id}.status"
    meta: dict[str, str] = {}
    for p in (meta_path, legacy_status_path):
        if Path(p).exists():
            for line in Path(p).read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    meta.setdefault(k, v)
    tickers = meta.get("tickers", "")
    instruction = meta.get("instruction", "")
    sector = meta.get("sector", "")
    if tickers:
        return run_analysis(mode="tickers", tickers=tickers, sector="", instruction=instruction)
    if sector:
        return run_analysis(mode="sector", tickers="", sector=sector, instruction=instruction)
    return RedirectResponse(url="/runs?msg=No+tickers+to+retry", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str):
    log_path = f"/tmp/pm_run_{run_id}.log"
    if not Path(log_path).exists():
        return _error_page(f"Run <code>{run_id}</code> 不存在", back_url="/runs", back_label="运行列表")
    log_content = Path(log_path).read_text()[-10000:]
    return templates.TemplateResponse(
        _dummy_request(),
        "runs_detail.html",
        {
            "css": CSS,
            "run_id": run_id,
            "log_content": log_content,
            "has_result": _find_output_dir_for_run(log_path) is not None,
        },
    )


@app.get("/runs/{run_id}/result", response_class=HTMLResponse)
def run_result(run_id: str):
    """Render the REPORT.md + decisions table for a completed run."""
    log_path = f"/tmp/pm_run_{run_id}.log"
    if not Path(log_path).exists():
        return _error_page(f"Run <code>{run_id}</code> 不存在", back_url="/runs", back_label="运行列表")
    out_dir = _find_output_dir_for_run(log_path)
    if out_dir is None:
        return _error_page(
            f"Run <code>{run_id}</code> 还没产出 — analyze_holdings 还没写 outputs/。可能还在跑，也可能失败了。",
            back_url=f"/runs/{run_id}", back_label="查看日志",
        )

    import markdown as _md

    report_path = out_dir / "REPORT.md"
    if report_path.exists():
        report_html = _md.markdown(report_path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    else:
        report_html = "<p style='color:var(--fg-muted);'>REPORT.md not found in output dir.</p>"

    log_text = Path(log_path).read_text()
    m = re.search(r"Will analyze \d+ ticker\(s\):\s*([^\n]+)", log_text)
    tickers_for_run = [t.strip() for t in (m.group(1) if m else "").split(",") if t.strip()]
    decisions: list[dict] = []
    if tickers_for_run:
        with connect() as conn:
            placeholders = ",".join("?" * len(tickers_for_run))
            seen: set[str] = set()
            for r in conn.execute(
                f"""
                SELECT symbol, trade_date, rating, created_at
                  FROM decisions
                 WHERE symbol IN ({placeholders})
                 ORDER BY decision_id DESC
                """,
                tickers_for_run,
            ).fetchall():
                if r["symbol"] in seen:
                    continue
                seen.add(r["symbol"])
                decisions.append(dict(r))

    project_root = Path(__file__).resolve().parent.parent
    rel_dir = out_dir.relative_to(project_root) if str(out_dir).startswith(str(project_root)) else out_dir

    return templates.TemplateResponse(
        _dummy_request(),
        "runs_result.html",
        {
            "css": CSS,
            "run_id": run_id,
            "rel_dir": str(rel_dir),
            "decisions": decisions,
            "report_html": report_html,
        },
    )


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
