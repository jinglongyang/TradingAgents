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
import threading
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


# Decisions saved before schemas.py inserted a blank line after the
# "Per-Account Actions" header would otherwise collapse the bullet block
# into one <p>, since python-markdown requires a blank line between a
# paragraph and a following ``-`` list. Insert it on render for back-compat.
_LIST_FIX_RE = re.compile(r"(\*\*[^\n*]+\*\*:)\n(-)")


def _render_pm_markdown(md_text: str) -> str:
    import markdown as _md
    fixed = _LIST_FIX_RE.sub(r"\1\n\n\2", md_text or "")
    return _md.markdown(fixed, extensions=["tables", "fenced_code"])


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
function openAccountEdit(acctId, acctName, currentBroker, currentType, currentOwner, currentAlias) {
    document.getElementById('acct-edit-id').value = acctId;
    document.getElementById('acct-edit-name-input').value = acctName;
    document.getElementById('acct-edit-alias-input').value = currentAlias || '';
    document.getElementById('acct-edit-broker-select').value = currentBroker || 'Fidelity';
    document.getElementById('acct-edit-type-select').value = currentType || 'Taxable';
    document.getElementById('acct-edit-owner-input').value = currentOwner || 'Self';
    document.getElementById('acct-edit-title').textContent = `编辑账户: ${currentAlias || acctName}`;
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


def _load_holdings_rows() -> list[dict]:
    """Single source for the latest snapshot — used by both views."""
    today = date.today().isoformat()
    latest = latest_snapshot_date()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.*,
                   COALESCE(t.last_price, p.last_price) AS authoritative_price,
                   COALESCE(t.last_price, p.last_price) * p.quantity AS authoritative_value,
                   a.alias AS alias
            FROM positions_snapshot p
            LEFT JOIN tickers t ON t.symbol = p.symbol
            LEFT JOIN accounts a ON a.account_id = p.account_id
            WHERE p.import_date = ?
            ORDER BY p.symbol, p.account_name
            """,
            (latest,) if latest else (today,),
        ).fetchall()
    return [
        {**dict(r),
         "last_price": r["authoritative_price"] or 0,
         "current_value": r["authoritative_value"] or 0}
        for r in rows
    ]


def _build_tickers_view():
    """Return (tickers, n_rows, total_value) — flat list grouped by symbol,
    each entry has aggregated totals plus the per-account breakdown."""
    rows = _load_holdings_rows()
    if not rows:
        return [], 0, 0.0
    by_sym: dict[str, list] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    tickers: list[dict] = []
    total = 0.0
    for sym, items in sorted(by_sym.items()):  # alphabetical
        sub_value = sum(r["current_value"] for r in items)
        sub_cost = sum(r["cost_basis_total"] or 0 for r in items)
        sub_qty = sum(r["quantity"] for r in items)
        total += sub_value
        pl_pct = ((sub_value - sub_cost) / sub_cost * 100) if sub_cost else 0.0
        # Use the first row's price (they should all be equal — pulled from tickers table)
        price = items[0]["last_price"]
        accounts = []
        for r in sorted(items, key=lambda x: -x["current_value"]):
            cost = r["cost_basis_total"] or 0
            apl = (r["current_value"] - cost) / cost * 100 if cost else 0
            alias = (r.get("alias") or "").strip()
            accounts.append({
                "snapshot_id": r["snapshot_id"],
                "account_id": r["account_id"],
                "account_name": r["account_name"],
                "alias": alias,
                "display_name": alias or r["account_name"],
                "account_type": r["account_type"],
                "account_type_lower": (r["account_type"] or "").lower(),
                "broker": r["broker"] or "—",
                "owner": r["owner"] or "—",
                "quantity": r["quantity"],
                "current_value": r["current_value"],
                "cost": cost,
                "pl_pct": apl,
                "pl_class": "gain" if apl >= 0 else "loss",
            })
        tickers.append({
            "symbol": sym,
            "price": price,
            "total_value": sub_value,
            "total_cost": sub_cost,
            "total_quantity": sub_qty,
            "pl_pct": pl_pct,
            "pl_class": "gain" if pl_pct >= 0 else "loss",
            "n_accounts": len(items),
            "accounts": accounts,
        })
    return tickers, len(rows), total


def _build_holdings_view():
    """Return (accounts, n_rows, total_value) for the by-account view."""
    rows = _load_holdings_rows()
    if not rows:
        return [], 0, 0.0

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
        alias = (items[0].get("alias") or "").strip()
        accounts.append({
            "account_id": aid,
            "account_name": aname,
            "alias": alias,
            "display_name": alias or aname,
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


def _render(message: str = "", level: str = "success", view: str = "account"):
    """Render the main holdings page via Jinja2.

    `view` is "account" (default — grouped by account, sorted by value) or
    "ticker" (flat list grouped by symbol, alphabetical).
    `message` is plain text (may contain inline HTML if needed, gets ``|safe``
    in the template); `level` is one of success / error / info / warning.
    Empty `message` means no flash banner.
    """
    import json as _json
    if view == "ticker":
        tickers, n_rows, total_val = _build_tickers_view()
        accounts = []
    else:
        view = "account"
        accounts, n_rows, total_val = _build_holdings_view()
        tickers = []
    ticker_pool = _ticker_pool()
    flash = {"text": message, "level": level} if message else None
    return templates.TemplateResponse(
        _dummy_request(),
        "index.html",
        {
            "css": CSS,
            "extra_js": JS,
            "view": view,
            "accounts": accounts,
            "tickers": tickers,
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
def index(msg: str = "", view: str = "account"):
    init_db()
    return _render(message=msg or "", view=view)


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
    backfill: Optional[str] = Form(None),
    redirect_anchor: str = Form(""),
):
    try:
        symbol_clean = symbol.strip().upper()
        price = last_price or 0.0
        is_backfill = bool(backfill) and backfill not in ("0", "false", "no", "")

        # Auto-fetch current price from yfinance if not provided
        if price <= 0:
            try:
                import yfinance as _yf
                from datetime import datetime as _dt
                hist = _yf.Ticker(symbol_clean).history(period="5d")
                if len(hist) > 0:
                    price = round(float(hist["Close"].iloc[-1]), 3)
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

        # Record as a BUY execution unless the user marked this as a backfill
        # (e.g. importing pre-existing positions from a non-Fidelity broker).
        # When price is unknown we fall back to avg-cost so the execution
        # carries a meaningful number even for cost-basis-only entries.
        if not is_backfill and quantity > 0:
            exec_price = price if price > 0 else (
                (cost_basis_total or 0.0) / quantity if quantity else 0.0
            )
            record_execution(
                trade_date=date.today().isoformat(),
                account_id=account_id.strip(),
                account_name=account_name.strip(),
                symbol=symbol_clean,
                action="BUY",
                shares=quantity,
                price=exec_price,
                note="via /add",
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


_enrich_state: dict = {"running": False, "started_at": None, "total": 0, "done": 0, "failed": []}
_enrich_lock = threading.Lock()


def _fetch_next_earnings(t) -> str | None:
    """Pull the next upcoming earnings date as ISO 'YYYY-MM-DD' from yfinance.

    yfinance exposes two surfaces: ``Ticker.calendar`` (dict, values are
    ``datetime.date`` objects) and ``Ticker.earnings_dates`` (DataFrame
    indexed by ``pd.Timestamp``). Probe both and pick the soonest future date.
    Returns None if neither has usable data."""
    from datetime import date as _date, datetime as _dt

    candidates: list[_date] = []
    today = _date.today()

    def _to_date(x):
        if isinstance(x, _dt):
            return x.date()
        if isinstance(x, _date):
            return x
        if hasattr(x, "to_pydatetime"):
            try:
                return x.to_pydatetime().date()
            except Exception:
                return None
        return None

    try:
        cal = t.calendar
        if isinstance(cal, dict):
            for k in ("Earnings Date", "earnings_date", "earningsDate"):
                v = cal.get(k)
                if not v:
                    continue
                for x in (v if isinstance(v, (list, tuple)) else [v]):
                    d = _to_date(x)
                    if d and d >= today:
                        candidates.append(d)
    except Exception:
        pass

    try:
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            for ts in ed.index:
                d = _to_date(ts)
                if d and d >= today:
                    candidates.append(d)
    except Exception:
        pass

    return min(candidates).isoformat() if candidates else None


def _enrich_worker(targets: list[str]) -> None:
    """Background worker: pulls metadata for the given tickers one by one.

    Each ticker commits independently so a kill/crash mid-batch keeps the
    partial progress; the next /enrich-tickers re-scans for remaining NULLs.

    yfinance's .info omits beta for ETFs and some micro-caps. When that
    happens, fall back to a 6-month linear regression of the ticker's daily
    returns against SPY so portfolio-level β coverage stays high.
    """
    import yfinance as _yf
    import numpy as _np
    spy_ret = None
    try:
        try:
            spy_hist = _yf.Ticker("SPY").history(period="6mo", auto_adjust=True)["Close"]
            spy_ret = spy_hist.pct_change().dropna()
        except Exception:
            spy_ret = None

        for sym in targets:
            try:
                t = _yf.Ticker(sym)
                info = t.info
                beta = info.get("beta")
                if beta is None and spy_ret is not None:
                    try:
                        hist = t.history(period="6mo", auto_adjust=True)["Close"]
                        r = hist.pct_change().dropna()
                        j = r.index.intersection(spy_ret.index)
                        if len(j) >= 30:
                            var = float(_np.var(spy_ret.loc[j]))
                            if var > 0:
                                cov = float(_np.cov(r.loc[j], spy_ret.loc[j])[0][1])
                                beta = round(cov / var, 3)
                    except Exception:
                        pass
                earnings_date = _fetch_next_earnings(t)
                with connect() as conn:
                    conn.execute(
                        """
                        UPDATE tickers SET
                            name = COALESCE(?, name),
                            sector = COALESCE(?, sector),
                            industry = COALESCE(?, industry),
                            market_cap = COALESCE(?, market_cap),
                            beta = COALESCE(?, beta),
                            earnings_date = ?,
                            earnings_updated_at = datetime('now')
                        WHERE symbol = ?
                        """,
                        (
                            info.get("longName") or info.get("shortName"),
                            info.get("sector"),
                            info.get("industry"),
                            info.get("marketCap"),
                            beta,
                            earnings_date,
                            sym,
                        ),
                    )
            except Exception:
                with _enrich_lock:
                    _enrich_state["failed"].append(sym)
            with _enrich_lock:
                _enrich_state["done"] += 1
    finally:
        with _enrich_lock:
            _enrich_state["running"] = False


@app.post("/enrich-tickers")
def enrich_tickers():
    """Kick off a background fetch of sector/industry/beta/market_cap/name from yfinance.

    Returns immediately; the worker thread persists each ticker as it
    completes. Re-running while a batch is in progress is a no-op (per the
    _enrich_state guard). Check progress at /enrich-tickers/status or just
    refresh /targets — sector / beta counts update live as rows persist.
    """
    from urllib.parse import quote as _quote

    with _enrich_lock:
        if _enrich_state["running"]:
            done, total = _enrich_state["done"], _enrich_state["total"]
            return RedirectResponse(
                url="/?msg=" + _quote(f"已在跑（{done}/{total}），刷新查看进度"),
                status_code=303,
            )

    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol FROM tickers WHERE sector IS NULL OR name IS NULL OR beta IS NULL ORDER BY symbol"
        ).fetchall()
        targets = [r["symbol"] for r in rows]

    if not targets:
        return RedirectResponse(url="/?msg=" + _quote("所有 ticker 元数据已完整"), status_code=303)

    with _enrich_lock:
        _enrich_state.update({
            "running": True,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(targets),
            "done": 0,
            "failed": [],
        })
    threading.Thread(target=_enrich_worker, args=(targets,), daemon=True).start()

    return RedirectResponse(
        url="/?msg=" + _quote(f"✓ 后台开始拉 {len(targets)} ticker（刷新页面看进度）"),
        status_code=303,
    )


@app.get("/enrich-tickers/status")
def enrich_status() -> dict:
    """JSON: current progress of the most-recent enrich batch."""
    with _enrich_lock:
        return dict(_enrich_state)


@app.post("/refresh-earnings")
def refresh_earnings():
    """Re-fetch earnings_date for every ticker in the snapshot.

    Unlike /enrich-tickers (which only rescans NULL columns), earnings dates
    rotate quarterly so this endpoint always covers the full set.
    """
    from urllib.parse import quote as _quote

    with _enrich_lock:
        if _enrich_state["running"]:
            return RedirectResponse(url="/today?msg=" + _quote("已在跑 enrich"), status_code=303)

    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM positions_snapshot WHERE import_date = (SELECT MAX(import_date) FROM positions_snapshot)"
        ).fetchall()
        targets = [r["symbol"] for r in rows]

    if not targets:
        return RedirectResponse(url="/today?msg=" + _quote("没 ticker 可刷新"), status_code=303)

    with _enrich_lock:
        _enrich_state.update({
            "running": True,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "total": len(targets),
            "done": 0,
            "failed": [],
        })
    threading.Thread(target=_enrich_worker, args=(targets,), daemon=True).start()
    return RedirectResponse(
        url="/today?msg=" + _quote(f"✓ 后台刷新 {len(targets)} ticker earnings（刷新页面看进度）"),
        status_code=303,
    )


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
                    price = round(float(hist["Close"].iloc[-1]), 3)
                except Exception:
                    missing.append(symbol)
                    continue
            else:
                ser = data[symbol].dropna()
                if len(ser) == 0:
                    missing.append(symbol)
                    continue
                price = round(float(ser.iloc[-1]), 3)

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
    alias: str = Form(""),
    redirect_anchor: str = Form(""),
):
    """Update account_name, account_type, broker, owner on every row of this
    account; upsert the optional alias into the accounts lookup table."""
    try:
        from datetime import datetime as _dt
        alias_clean = alias.strip()
        with connect() as conn:
            conn.execute(
                """
                UPDATE positions_snapshot
                SET account_name = ?, account_type = ?, broker = ?, owner = ?
                WHERE account_id = ?
                """,
                (account_name.strip(), account_type, broker, owner.strip(), account_id),
            )
            if alias_clean:
                conn.execute(
                    """
                    INSERT INTO accounts (account_id, alias, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        alias = excluded.alias,
                        updated_at = excluded.updated_at
                    """,
                    (account_id, alias_clean, _dt.now().isoformat(timespec="seconds")),
                )
            else:
                # Clearing the alias: keep the row but null it out so display falls back.
                conn.execute(
                    "UPDATE accounts SET alias = NULL, updated_at = ? WHERE account_id = ?",
                    (_dt.now().isoformat(timespec="seconds"), account_id),
                )
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


_SECTOR_ETFS = [
    # (symbol, English name, emoji, plain-language description, GICS sector key
    # that matches the values stored in tickers.sector)
    ("XLK", "Technology", "🖥️", "大型科技股（半导体 / 软件 / 硬件）", "Technology"),
    ("XLV", "Health Care", "🏥", "大型医药 / 生物科技 / 医疗器械", "Healthcare"),
    ("XLF", "Financials", "🏦", "大型银行 / 保险 / 资管", "Financial Services"),
    ("XLY", "Consumer Discretionary", "🛍️", "非必需消费（汽车 / 零售 / 旅游）", "Consumer Cyclical"),
    ("XLP", "Consumer Staples", "🛒", "必需消费（食品 / 日用品 / 烟酒）", "Consumer Defensive"),
    ("XLI", "Industrials", "🏭", "工业（航空 / 国防 / 工程）", "Industrials"),
    ("XLE", "Energy", "⛽", "能源（石油 / 天然气 / 油服）", "Energy"),
    ("XLB", "Materials", "⛏️", "原材料（化工 / 金属 / 林业）", "Basic Materials"),
    ("XLU", "Utilities", "💡", "公用事业（电力 / 水务 / 天然气）", "Utilities"),
    ("XLRE", "Real Estate", "🏢", "房地产 REIT（商办 / 数据中心 / 仓储）", "Real Estate"),
    ("XLC", "Communication Services", "📡", "通信（社交 / 流媒体 / 电信）", "Communication Services"),
    ("SPY", "S&P 500 (benchmark)", "📊", "标普 500 大盘基准（参考线）", None),
]


@app.get("/sectors", response_class=HTMLResponse)
def sectors_view():
    """Sector ETF performance dashboard with portfolio exposure + top holdings."""
    import yfinance as yf

    # 1. Per-sector portfolio exposure (sum of holdings whose tickers.sector
    # matches this ETF's GICS sector).
    latest = latest_snapshot_date()
    sector_exposure: dict[str, dict] = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT t.sector AS sector, ps.symbol AS symbol,
                   SUM(ps.quantity * COALESCE(t.last_price, ps.last_price)) AS value
              FROM positions_snapshot ps
              LEFT JOIN tickers t ON t.symbol = ps.symbol
             WHERE ps.import_date = ?
             GROUP BY t.sector, ps.symbol
            """,
            (latest or date.today().isoformat(),),
        ).fetchall()
    grand_total = sum((r["value"] or 0) for r in rows)
    for r in rows:
        sec = r["sector"] or "Unknown"
        slot = sector_exposure.setdefault(sec, {"total": 0.0, "tickers": []})
        slot["total"] += r["value"] or 0
        slot["tickers"].append((r["symbol"], r["value"] or 0))

    # 2. Per-ETF price history + top holdings (slow yfinance calls).
    results = []
    for symbol, name, emoji, blurb, gics in _SECTOR_ETFS:
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period="1y")
            if len(hist) < 2:
                continue
            latest_px = float(hist["Close"].iloc[-1])

            def pct(days: int) -> float | None:
                if len(hist) < days + 1:
                    return None
                past = float(hist["Close"].iloc[-1 - days])
                return (latest_px - past) / past * 100 if past else None

            # YTD = first trading day of this calendar year
            ytd = None
            year_mask = hist.index.year == hist.index[-1].year
            if year_mask.any():
                first = float(hist["Close"][year_mask].iloc[0])
                ytd = (latest_px - first) / first * 100 if first else None
            # 1-year = first row in the 1y history
            first_1y = float(hist["Close"].iloc[0])
            d365 = (latest_px - first_1y) / first_1y * 100 if first_1y else None

            top_holdings: list[tuple[str, float]] = []
            try:
                fd = t.funds_data
                th = fd.top_holdings
                # Index is the symbol, "Holding Percent" column has the weight 0..1
                for sym in th.index[:3]:
                    weight = float(th.loc[sym, "Holding Percent"])
                    top_holdings.append((str(sym), weight * 100))
            except Exception:
                pass

            exposure = sector_exposure.get(gics, {"total": 0.0, "tickers": []}) if gics else None
            results.append({
                "symbol": symbol, "name": name, "emoji": emoji, "blurb": blurb,
                "gics": gics, "latest": latest_px,
                "d1": pct(1), "d5": pct(5), "d30": pct(22), "d90": pct(66),
                "ytd": ytd, "d365": d365,
                "top_holdings": top_holdings,
                "exposure_total": exposure["total"] if exposure else None,
                "exposure_pct": (exposure["total"] / grand_total * 100) if exposure and grand_total else None,
                "exposure_n": len(exposure["tickers"]) if exposure else 0,
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
            "cls_ytd": cls(r["ytd"]), "fmt_ytd": fmt(r["ytd"]),
            "cls_d365": cls(r["d365"]), "fmt_d365": fmt(r["d365"]),
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


_ACTION_BUCKETS = {
    "Reduce": "reduce", "Sell": "reduce", "Trim": "reduce",
    "Add": "add", "Buy": "add", "Initiate": "add", "Open": "add",
}
_RATING_WEIGHT = {"Buy": 3, "Overweight": 2, "Hold": 1, "Underweight": 0, "Sell": -1}
_TAX_WEIGHT = {"TaxDeferred": 3, "Roth": 3, "ChildEdu": 2, "Taxable": 1, "Unknown": 1}
_PRICE_TARGET_RE = re.compile(r"\*\*Price Target[^*]*\*\*:\s*\$?([\d.]+)", re.IGNORECASE)
_STOP_LOSS_RE = re.compile(r"\*\*Stop Loss\*\*:\s*\$?([\d.]+)", re.IGNORECASE)


def _extract_price_levels(md: str) -> tuple[float | None, float | None]:
    """Pull (price_target, stop_loss) out of a decision's markdown body.

    Returns (None, None) when fields are absent (some old decisions and most
    holdings-only PMs don't include explicit targets)."""
    if not md:
        return None, None
    pt = _PRICE_TARGET_RE.search(md)
    sl = _STOP_LOSS_RE.search(md)
    try:
        pt_val = float(pt.group(1)) if pt else None
    except ValueError:
        pt_val = None
    try:
        sl_val = float(sl.group(1)) if sl else None
    except ValueError:
        sl_val = None
    return pt_val, sl_val


_CORR_CACHE_TTL_DAYS = 7
_CORR_LOOKBACK_DAYS = 90


def _load_correlations(tickers: list[str]) -> dict[tuple[str, str], float]:
    """Return a dict keyed by alphabetically-ordered pair → correlation.

    Reads from ``ticker_correlations``; fetches and persists any missing or
    stale (>7 days) pair via yfinance daily closes over the last 90 days.
    Pair keys are ``(min, max)`` so callers can look up symmetrically."""
    if len(tickers) < 2:
        return {}
    tickers = sorted(set(tickers))
    pairs_needed = [(a, b) for i, a in enumerate(tickers) for b in tickers[i + 1:]]

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol_a, symbol_b, correlation FROM ticker_correlations
            WHERE updated_at > datetime('now', '-{_CORR_CACHE_TTL_DAYS} days')
              AND symbol_a IN ({','.join('?' * len(tickers))})
              AND symbol_b IN ({','.join('?' * len(tickers))})
            """,
            tickers + tickers,
        ).fetchall()
    cached = {(r["symbol_a"], r["symbol_b"]): r["correlation"] for r in rows}
    missing = [p for p in pairs_needed if p not in cached]

    if missing:
        # Fetch all involved tickers in one yfinance call to amortise cost,
        # then compute correlations only for the missing pairs.
        try:
            import yfinance as _yf
            import pandas as _pd
            data = _yf.download(
                tickers, period=f"{_CORR_LOOKBACK_DAYS}d",
                progress=False, auto_adjust=True,
            )["Close"]
            if isinstance(data, _pd.Series):
                data = data.to_frame()
            returns = data.pct_change().dropna()
            corr_matrix = returns.corr()
        except Exception:
            corr_matrix = None

        if corr_matrix is not None:
            with connect() as conn:
                for a, b in missing:
                    if a not in corr_matrix.columns or b not in corr_matrix.columns:
                        continue
                    v = corr_matrix.loc[a, b]
                    if v != v:  # NaN check
                        continue
                    v_float = float(v)
                    conn.execute(
                        """
                        INSERT INTO ticker_correlations
                            (symbol_a, symbol_b, correlation, period_days, updated_at)
                        VALUES (?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(symbol_a, symbol_b) DO UPDATE SET
                            correlation = excluded.correlation,
                            period_days = excluded.period_days,
                            updated_at  = excluded.updated_at
                        """,
                        (a, b, v_float, _CORR_LOOKBACK_DAYS),
                    )
                    cached[(a, b)] = v_float
    return cached


def _priority_score(item: dict) -> float:
    """Higher = do first. Composite of rating strength, account tax efficiency,
    ticker drift, sector drift, expected return, and dollar magnitude.

    Sector drift gets a sign-aware multiplier (negative = sector overweight =
    penalty for adding more there) so e.g. five tickers all on-target in an
    already-overweight sector get pushed below an under-weighted sector's
    Overweights."""
    import math
    rating = _RATING_WEIGHT.get(item.get("rating"), 1)
    tax = _TAX_WEIGHT.get(item.get("account_type"), 1)
    value = max(item.get("est_value") or 0, 0)
    exp_return = item.get("expected_return")  # fraction, e.g. 0.18 = +18%
    return_bonus = (exp_return * 100 * 10) if exp_return is not None else 0
    drift = item.get("drift_pct")  # positive = under target by N%
    drift_bonus = (drift * 50) if drift is not None else 0
    sector_drift = item.get("sector_drift_pct")
    sector_bonus = (sector_drift * 30) if sector_drift is not None else 0
    return (
        rating * 10000
        + tax * 1000
        + drift_bonus
        + sector_bonus
        + return_bonus
        + math.log10(value + 1) * 5
    )


@app.get("/targets", response_class=HTMLResponse)
def targets_view():
    """Edit per-ticker target portfolio weights.

    Lists every ticker present in the latest snapshot plus any ticker with
    a saved target (so user-defined targets for not-yet-held tickers also
    appear). Current weight is computed from the snapshot; target is
    user-editable and persisted to ``target_weights``.
    """
    with connect() as conn:
        snapshot_rows = conn.execute(
            """
            SELECT p.symbol,
                   SUM(p.current_value) AS total_value,
                   COALESCE(t.sector, 'Unknown') AS sector
            FROM positions_snapshot p
            LEFT JOIN tickers t ON t.symbol = p.symbol
            WHERE p.import_date = (SELECT MAX(import_date) FROM positions_snapshot)
            GROUP BY p.symbol, t.sector
            """
        ).fetchall()
        target_rows = conn.execute("SELECT symbol, target_pct FROM target_weights").fetchall()
        sector_target_rows = conn.execute("SELECT sector, target_pct FROM sector_targets").fetchall()

    portfolio_total = sum(r["total_value"] or 0 for r in snapshot_rows)
    targets = {r["symbol"]: r["target_pct"] for r in target_rows}
    sector_targets = {r["sector"]: r["target_pct"] for r in sector_target_rows}
    held = {r["symbol"]: (r["total_value"] or 0) for r in snapshot_rows}
    sym_sector = {r["symbol"]: r["sector"] for r in snapshot_rows}

    # Aggregate by sector
    sector_value: dict[str, float] = {}
    for r in snapshot_rows:
        sector_value[r["sector"]] = sector_value.get(r["sector"], 0) + (r["total_value"] or 0)

    sector_rows = []
    all_sectors = set(sector_value.keys()) | set(sector_targets.keys())
    for sec in sorted(all_sectors):
        value = sector_value.get(sec, 0)
        cur_pct = (value / portfolio_total * 100) if portfolio_total else 0
        tgt_pct = sector_targets.get(sec)
        drift = (tgt_pct - cur_pct) if tgt_pct is not None else None
        sector_rows.append({
            "sector": sec,
            "current_value": value,
            "current_pct": cur_pct,
            "target_pct": tgt_pct,
            "drift": drift,
        })

    rows = []
    all_symbols = set(held.keys()) | set(targets.keys())
    for sym in sorted(all_symbols):
        value = held.get(sym, 0)
        cur_pct = (value / portfolio_total * 100) if portfolio_total else 0
        tgt_pct = targets.get(sym)
        drift = (tgt_pct - cur_pct) if tgt_pct is not None else None
        rows.append({
            "symbol": sym,
            "sector": sym_sector.get(sym, "Unknown"),
            "current_value": value,
            "current_pct": cur_pct,
            "target_pct": tgt_pct,
            "drift": drift,
        })

    target_total = sum(t for t in targets.values()) if targets else 0
    sector_target_total = sum(t for t in sector_targets.values()) if sector_targets else 0
    return templates.TemplateResponse(
        _dummy_request(), "targets.html",
        {
            "css": CSS,
            "rows": rows,
            "sector_rows": sector_rows,
            "portfolio_total": portfolio_total,
            "target_total": target_total,
            "sector_target_total": sector_target_total,
            "n_with_target": sum(1 for r in rows if r["target_pct"] is not None),
            "n_with_sector_target": sum(1 for r in sector_rows if r["target_pct"] is not None),
        },
    )


@app.post("/targets/set-sector")
def set_sector_target(sector: str = Form(...), target_pct: str = Form("")):
    """Upsert a single sector target, or delete the row when blank."""
    sec = sector.strip()
    with connect() as conn:
        if not target_pct.strip():
            conn.execute("DELETE FROM sector_targets WHERE sector = ?", (sec,))
        else:
            try:
                pct = float(target_pct)
            except ValueError:
                return RedirectResponse(url="/targets", status_code=303)
            conn.execute(
                """
                INSERT INTO sector_targets (sector, target_pct, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(sector) DO UPDATE SET
                    target_pct = excluded.target_pct,
                    updated_at = excluded.updated_at
                """,
                (sec, max(pct, 0)),
            )
    return RedirectResponse(url="/targets", status_code=303)


@app.post("/targets/set")
def set_target_weight(symbol: str = Form(...), target_pct: str = Form("")):
    """Upsert a single target weight, or delete the row when target_pct is blank."""
    sym = symbol.strip().upper()
    with connect() as conn:
        if not target_pct.strip():
            conn.execute("DELETE FROM target_weights WHERE symbol = ?", (sym,))
        else:
            try:
                pct = float(target_pct)
            except ValueError:
                return RedirectResponse(url="/targets", status_code=303)
            conn.execute(
                """
                INSERT INTO target_weights (symbol, target_pct, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(symbol) DO UPDATE SET
                    target_pct = excluded.target_pct,
                    updated_at = excluded.updated_at
                """,
                (sym, max(pct, 0)),
            )
    return RedirectResponse(url="/targets", status_code=303)


@app.post("/today/execute")
def today_execute(
    action_type: str = Form(...),
    account_id: str = Form(...),
    account_name: str = Form(...),
    account_type: str = Form("Taxable"),
    symbol: str = Form(...),
    shares: float = Form(...),
    price: float = Form(...),
    note: Optional[str] = Form(None),
):
    """One-click execution from /today: writes the execution row AND mutates
    the snapshot in the same shape as /add (for BUY) or /sell (for SELL).

    Mirrors the existing endpoints rather than calling them so the redirect
    stays on /today instead of jumping to the holdings home page.
    """
    sym = symbol.strip().upper()
    aid = account_id.strip()
    aname = account_name.strip()
    action = action_type.upper()
    today = date.today().isoformat()
    note_final = (note.strip() if note else "") or "via /today"

    if action not in ("BUY", "SELL"):
        return _render(message=f"✗ 不支持的动作: {action}", level="error")

    if action == "BUY":
        cost = shares * price
        add_position(
            account_id=aid, account_name=aname,
            account_type=account_type, symbol=sym,
            quantity=shares, last_price=price, cost_basis_total=cost,
            broker="Manual",
        )
        record_execution(
            trade_date=today, account_id=aid, account_name=aname, symbol=sym,
            action="BUY", shares=shares, price=price, note=note_final,
        )
    else:  # SELL
        record_execution(
            trade_date=today, account_id=aid, account_name=aname, symbol=sym,
            action="SELL", shares=shares, price=price, note=note_final,
        )
        latest = latest_snapshot_date()
        with connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, quantity FROM positions_snapshot
                WHERE import_date = ? AND account_id = ? AND symbol = ?
                """,
                (latest, aid, sym),
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

    return RedirectResponse(url="/today", status_code=303)


@app.post("/account-cash")
def set_account_cash(account_id: str = Form(...), cash: float = Form(0.0)):
    """Persist the user-entered cash balance for one account.

    Used by /today to drive budget-constrained allocation. Cash sits in a
    tiny standalone table — broker statements aren't imported, this is a
    purely manual field the user updates after checking each account.
    """
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO account_cash (account_id, cash, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(account_id) DO UPDATE SET
                cash = excluded.cash,
                updated_at = excluded.updated_at
            """,
            (account_id.strip(), max(cash, 0)),
        )
    return RedirectResponse(url="/today", status_code=303)


@app.get("/today", response_class=HTMLResponse)
def today_view():
    """Consolidated execution list grouped by account.

    Each account card shows cash freed by reduces vs cash needed for adds,
    so the user can see at a glance whether a given account can fund its
    own buys from same-account sells (cash doesn't move across accounts).
    Adds within an account are sorted by a composite priority score so the
    most important ones come first when cash is tight.
    """
    import json as _json
    with connect() as conn:
        latest_row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM decisions"
        ).fetchone()
        trade_date = latest_row["d"] if latest_row else None
        decisions = conn.execute(
            "SELECT symbol, rating, account_actions, final_decision "
            "FROM decisions WHERE trade_date = ? ORDER BY symbol",
            (trade_date,),
        ).fetchall() if trade_date else []
        snapshot = {
            (r["account_id"], r["symbol"]): dict(r)
            for r in conn.execute(
                """
                SELECT account_id, account_name, account_type, symbol,
                       quantity, last_price, current_value
                FROM positions_snapshot
                WHERE import_date = (SELECT MAX(import_date) FROM positions_snapshot)
                """
            ).fetchall()
        }
        target_rows = conn.execute("SELECT symbol, target_pct FROM target_weights").fetchall()
        sector_target_rows = conn.execute("SELECT sector, target_pct FROM sector_targets").fetchall()
        ticker_meta_rows = conn.execute("SELECT symbol, sector, beta, earnings_date FROM tickers").fetchall()
    sector_lookup = {r["symbol"]: r["sector"] for r in ticker_meta_rows if r["sector"]}
    beta_lookup = {r["symbol"]: r["beta"] for r in ticker_meta_rows if r["beta"] is not None}

    # Earnings window: flag any decision whose ticker reports earnings within 7
    # calendar days so users can avoid stacking add/reduce orders on top of an
    # event. Distance is signed days; <0 = past, 0..7 = imminent.
    from datetime import datetime as _dt
    _today = date.today()
    earnings_lookup: dict[str, dict] = {}
    for r in ticker_meta_rows:
        if not r["earnings_date"]:
            continue
        try:
            ed = _dt.strptime(r["earnings_date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        delta = (ed - _today).days
        earnings_lookup[r["symbol"]] = {"date": r["earnings_date"], "days": delta}

    # Per-ticker portfolio aggregates for drift calculation
    target_weights = {r["symbol"]: r["target_pct"] for r in target_rows}
    sector_target_weights = {r["sector"]: r["target_pct"] for r in sector_target_rows}
    ticker_total: dict[str, float] = {}
    sector_total: dict[str, float] = {}
    for (_aid, sym), row in snapshot.items():
        v = row.get("current_value") or 0
        ticker_total[sym] = ticker_total.get(sym, 0) + v
        sec = sector_lookup.get(sym, "Unknown")
        sector_total[sec] = sector_total.get(sec, 0) + v
    portfolio_total = sum(ticker_total.values())

    # account_name → first observed account_id (decisions only carry the name)
    name_to_account: dict[str, str] = {}
    account_meta: dict[str, dict] = {}
    for (aid, _sym), row in snapshot.items():
        name_to_account.setdefault(row["account_name"], aid)
        account_meta.setdefault(aid, {
            "account_id": aid,
            "account_name": row["account_name"],
            "account_type": row["account_type"],
        })

    items: list[dict] = []
    for d in decisions:
        actions = _json.loads(d["account_actions"]) if d["account_actions"] else []
        price_target, stop_loss = _extract_price_levels(d["final_decision"])
        for a in actions:
            account_name = a.get("account_name", "")
            account_id = name_to_account.get(account_name) or f"name:{account_name}"
            pos = snapshot.get((account_id, d["symbol"])) if account_id else None
            qty = pos["quantity"] if pos else None
            price = pos["last_price"] if pos else None
            size_pct = a.get("size_pct")
            est_shares = (qty * size_pct / 100.0) if (qty and size_pct) else None
            est_value = (est_shares * price) if (est_shares and price) else None

            # Expected return = upside to PM's 12m target. Downside = drop to
            # stop loss. Risk/reward ratio is exposed so the user can compare
            # picks beyond rating + dollar size.
            expected_return = ((price_target - price) / price) if (price_target and price and price > 0) else None
            downside = ((price - stop_loss) / price) if (stop_loss and price and price > 0) else None
            rr_ratio = (expected_return / downside) if (expected_return and downside and downside > 0) else None

            # Drift: how far the *ticker* (across all accounts) sits from the
            # user-defined target weight. Positive = under target → priority
            # bonus. Capital should flow where the gap is biggest first.
            target_pct = target_weights.get(d["symbol"])
            current_pct = (ticker_total.get(d["symbol"], 0) / portfolio_total * 100) if portfolio_total else 0
            drift_pct = (target_pct - current_pct) if target_pct is not None else None

            # Sector drift: catches "every ticker on-target but the sector is
            # 50% overweight" concentration. Sector pulled from tickers table.
            sector = sector_lookup.get(d["symbol"], "Unknown")
            sector_target = sector_target_weights.get(sector)
            sector_current_pct = (sector_total.get(sector, 0) / portfolio_total * 100) if portfolio_total else 0
            sector_drift_pct = (sector_target - sector_current_pct) if sector_target is not None else None

            beta = beta_lookup.get(d["symbol"])
            # Risk-weighted reduce sizing: beta × $ approximates market-risk
            # dollar exposure. Sells with bigger risk exposure get sorted
            # ahead — the goal of derisking is to cut risk, not just cash.
            risk_value = (est_value or 0) * (beta if beta is not None else 1.0)

            earnings = earnings_lookup.get(d["symbol"])
            items.append({
                "ticker": d["symbol"],
                "rating": d["rating"],
                "account_id": account_id,
                "account_name": account_name,
                "account_type": a.get("account_type", ""),
                "action": a.get("action", ""),
                "earnings_date": earnings["date"] if earnings else None,
                "earnings_in_days": earnings["days"] if earnings else None,
                "size_pct": size_pct,
                "current_qty": qty,
                "current_price": price,
                "est_shares": est_shares,
                "est_value": est_value,
                "price_target": price_target,
                "stop_loss": stop_loss,
                "expected_return": expected_return,
                "downside": downside,
                "rr_ratio": rr_ratio,
                "target_pct": target_pct,
                "current_pct": current_pct,
                "drift_pct": drift_pct,
                "sector": sector,
                "sector_target_pct": sector_target,
                "sector_current_pct": sector_current_pct,
                "sector_drift_pct": sector_drift_pct,
                "beta": beta,
                "risk_value": risk_value,
                "rationale": a.get("rationale", ""),
                "bucket": _ACTION_BUCKETS.get(a.get("action", ""), "hold"),
            })

    # Group by account
    by_account: dict[str, dict] = {}
    for it in items:
        aid = it["account_id"]
        if aid not in by_account:
            meta = account_meta.get(aid, {})
            by_account[aid] = {
                "account_id": aid,
                "account_name": it["account_name"] or meta.get("account_name", aid),
                "account_type": it["account_type"] or meta.get("account_type", ""),
                "reduces": [],
                "adds": [],
                "holds": [],
            }
        by_account[aid][it["bucket"] + "s"].append(it)

    # Load user-entered cash balances + today's executions for done-marking
    today_iso = date.today().isoformat()
    with connect() as conn:
        cash_rows = conn.execute("SELECT account_id, cash FROM account_cash").fetchall()
        exec_rows = conn.execute(
            "SELECT account_id, symbol, action FROM executions WHERE trade_date = ?",
            (today_iso,),
        ).fetchall()
    account_cash = {r["account_id"]: r["cash"] for r in cash_rows}
    executed_set = {(r["account_id"], r["symbol"], r["action"]) for r in exec_rows}

    # Tag each item as executed if today's executions cover it
    _bucket_to_action = {"add": "BUY", "reduce": "SELL"}
    for it in items:
        ea = _bucket_to_action.get(it["bucket"])
        it["executed"] = bool(ea and (it["account_id"], it["ticker"], ea) in executed_set)

    # Two-pass priority: compute base score, then walk adds in priority order
    # and discount items that overlap heavily with an already-prioritised add.
    # AVGO+AMD+NVDA together isn't 3x diversified — the 3rd buy should get
    # pushed down behind less-correlated alternatives.
    add_tickers = sorted({it["ticker"] for it in items if it["bucket"] == "add"})
    corr_lookup = _load_correlations(add_tickers) if len(add_tickers) >= 2 else {}

    def _corr(a: str, b: str) -> float | None:
        return corr_lookup.get(tuple(sorted([a, b])))

    add_items = [it for it in items if it["bucket"] == "add"]
    add_items.sort(key=lambda x: -_priority_score(x))
    seen_tickers: list[str] = []
    for it in add_items:
        t = it["ticker"]
        max_corr, max_other = 0.0, None
        for other in seen_tickers:
            if other == t:
                continue
            c = _corr(t, other)
            if c is not None and c > max_corr:
                max_corr, max_other = c, other
        # Apply correlation penalty linearly above 0.4 — US large-cap tech
        # rarely exceeds 0.7 over 90 days, so a stricter threshold never
        # fires. 500 scaling makes a 0.6 correlation cost ~100 points
        # (similar to one tier of tax_weight).
        penalty = max(0.0, max_corr - 0.4) * 500 if max_corr > 0.4 else 0
        it["corr_max"] = max_corr if max_corr > 0 else None
        it["corr_with"] = max_other
        it["corr_penalty"] = penalty
        it["adjusted_priority"] = _priority_score(it) - penalty
        seen_tickers.append(t)

    # Per-account: sort reduces by $ desc (biggest derisk first), adds by
    # priority score desc (most important first when cash runs out). Then
    # greedy-allocate available cash (user-entered + reduce proceeds) down
    # the prioritised add list so the user can see exactly which adds are
    # fundable, partially fundable, or blocked by the budget.
    accounts = []
    for acct in by_account.values():
        # Reduces: sort by beta-weighted dollar exposure (risk_value), so a
        # high-beta position trims ahead of a low-beta one of the same $ size.
        acct["reduces"].sort(key=lambda x: -(x["risk_value"] or 0))
        # Adds: sort by correlation-adjusted priority (set above). Falls back
        # to base score for items missing adjusted_priority (shouldn't happen
        # but defensive).
        acct["adds"].sort(key=lambda x: -(x.get("adjusted_priority") or _priority_score(x)))
        acct["cash_in"] = sum((x["est_value"] or 0) for x in acct["reduces"])
        acct["cash_out"] = sum((x["est_value"] or 0) for x in acct["adds"])
        acct["net"] = acct["cash_in"] - acct["cash_out"]
        acct["has_action"] = bool(acct["reduces"] or acct["adds"])
        acct["existing_cash"] = account_cash.get(acct["account_id"], 0)
        acct["available"] = acct["existing_cash"] + acct["cash_in"]

        budget = acct["available"]
        for add in acct["adds"]:
            need = add["est_value"] or 0
            if need <= 0:
                add["alloc_status"] = "unknown"
                add["alloc_amount"] = 0
                continue
            if budget >= need:
                add["alloc_status"] = "full"
                add["alloc_amount"] = need
                budget -= need
            elif budget > 0:
                add["alloc_status"] = "partial"
                add["alloc_amount"] = budget
                budget = 0
            else:
                add["alloc_status"] = "skip"
                add["alloc_amount"] = 0
        acct["leftover"] = budget
        accounts.append(acct)

    # Account order: those with actions first, sorted by total $ activity desc;
    # hold-only accounts last so the page leads with what needs doing.
    accounts.sort(key=lambda a: (
        not a["has_action"],
        -(a["cash_in"] + a["cash_out"]),
    ))

    totals = {
        "cash_in": sum(a["cash_in"] for a in accounts),
        "cash_out": sum(a["cash_out"] for a in accounts),
        "n_reduces": sum(len(a["reduces"]) for a in accounts),
        "n_adds": sum(len(a["adds"]) for a in accounts),
        "n_holds": sum(len(a["holds"]) for a in accounts),
    }
    totals["net"] = totals["cash_in"] - totals["cash_out"]

    # Portfolio-level risk header: weighted beta, top-5 concentration, cash %.
    # Beta-weighted by current $ exposure; ticker_total already aggregates value
    # across accounts. Uses only tickers with known beta; coverage shown so the
    # user can judge whether the number is representative.
    beta_weighted_sum = sum(
        ticker_total[s] * beta_lookup[s]
        for s in ticker_total
        if s in beta_lookup
    )
    beta_covered_value = sum(ticker_total[s] for s in ticker_total if s in beta_lookup)
    portfolio_beta = (beta_weighted_sum / beta_covered_value) if beta_covered_value else None
    beta_coverage_pct = (beta_covered_value / portfolio_total * 100) if portfolio_total else 0

    top5 = sorted(ticker_total.items(), key=lambda kv: -kv[1])[:5]
    top5_value = sum(v for _, v in top5)
    top5_pct = (top5_value / portfolio_total * 100) if portfolio_total else 0
    top5_tickers = ", ".join(t for t, _ in top5)

    cash_total = sum(account_cash.values())
    invested_plus_cash = portfolio_total + cash_total
    cash_pct = (cash_total / invested_plus_cash * 100) if invested_plus_cash else 0

    risk = {
        "portfolio_beta": portfolio_beta,
        "beta_coverage_pct": beta_coverage_pct,
        "top5_pct": top5_pct,
        "top5_tickers": top5_tickers,
        "cash_total": cash_total,
        "cash_pct": cash_pct,
        "portfolio_total": portfolio_total,
    }

    # σ / Sharpe / Sortino / drawdown — buy-and-hold-current-basket proxy over
    # the past year. Empty dict on data miss; template guards on key presence.
    if portfolio_total > 0:
        weights = {s: v / portfolio_total for s, v in ticker_total.items() if v > 0}
        risk.update(_compute_portfolio_risk(weights))

    # Vol-target sizing — attach (annualized 60d vol, suggested $) to every
    # add/reduce so the user can sanity-check LLM size_pct against a risk
    # budget. Computed once for the union of symbols across all items.
    symbols_in_view = sorted({it["ticker"] for acct in accounts for bucket in ("adds", "reduces") for it in acct[bucket]})
    vol_targets = _compute_vol_targets(symbols_in_view, portfolio_total)
    adv_map = _fetch_avg_dollar_volume(symbols_in_view, days=20)
    for acct in accounts:
        for bucket in ("adds", "reduces"):
            for it in acct[bucket]:
                vt = vol_targets.get(it["ticker"])
                it["vol_annual"] = vt["vol_annual"] if vt else None
                it["vol_target_dollars"] = vt["vol_target_dollars"] if vt else None
                it["vol_target_pct_portfolio"] = vt["vol_target_pct_portfolio"] if vt else None
                # Convenience flag — LLM suggested >2× the vol-target sizing.
                ev = it.get("est_value") or 0
                vtd = vt["vol_target_dollars"] if vt else 0
                it["vol_oversize"] = bool(vt and vtd > 0 and bucket == "adds" and ev > 2 * vtd)

                # Liquidity: est_value as % of 20d avg $ volume. >5% is a
                # rough threshold for non-trivial slippage; consider splitting.
                adv = adv_map.get(it["ticker"])
                it["adv_dollars"] = adv
                it["pct_of_adv"] = (ev / adv * 100) if (adv and adv > 0 and ev > 0) else None
                it["adv_oversize"] = bool(it["pct_of_adv"] is not None and it["pct_of_adv"] > 5)

    return templates.TemplateResponse(
        _dummy_request(), "today.html",
        {
            "css": CSS,
            "trade_date": trade_date,
            "accounts": accounts,
            "totals": totals,
            "n_tickers": len({d["symbol"] for d in decisions}),
            "risk": risk,
        },
    )


_BACKTEST_CACHE: dict[str, object] = {"key": None, "result": None}
_TECH_CACHE: dict[str, object] = {"key": None, "result": None}
_RETURNS_CACHE: dict[str, object] = {"key": None, "data": None, "vol": None}
_ADV_CACHE: dict[str, object] = {"key": None, "data": None}

# Annual risk-free rate used by Sharpe / Sortino. 4.5% ≈ 3-month T-bill yield
# at the time of writing. Hardcoded — fetching live is overkill for a sanity
# metric that only matters at 1-decimal precision.
RISK_FREE_RATE_ANNUAL = 0.045

# Per-position risk budget for vol-targeted sizing. position_$ × annualized_vol
# = budget_$ → position_$ = (budget_pct × portfolio_total) / vol. Stocks with
# 50% vol get half the dollars of stocks with 25% vol so each position
# contributes the same expected daily PnL swing.
RISK_BUDGET_PER_POSITION_PCT = 1.0

# Concentration alert thresholds (% of total portfolio value). Tunable; these
# defaults are conservative-for-retail picks. Ticker 10% mirrors the SEC's
# 13D ownership threshold as a rough "this is a big bet" gut check; sector
# 35% catches an entire-portfolio tech tilt; owner 80% surfaces household
# wealth concentration (one person holding ~all the money).
CONCENTRATION_TICKER_MAX_PCT = 10.0
CONCENTRATION_SECTOR_MAX_PCT = 35.0
CONCENTRATION_OWNER_MAX_PCT = 80.0


def _fetch_returns_matrix(symbols: list[str], days: int = 252) -> "pd.DataFrame | None":
    """Daily simple-return DataFrame for ``symbols`` over the last ``days``
    trading days, with hourly cache. Returns None when yfinance fails."""
    import pandas as _pd
    import yfinance as _yf
    from datetime import date as _date, timedelta as _td
    import time as _time

    syms = tuple(sorted(set(symbols)))
    if not syms:
        return None
    key = (syms, days, int(_time.time() / 3600))
    if _RETURNS_CACHE["key"] == key:
        return _RETURNS_CACHE["data"]

    end = _date.today() + _td(days=1)
    start = end - _td(days=int(days * 1.5) + 14)  # buffer for weekends/holidays
    try:
        raw = _yf.download(list(syms), start=start, end=end, progress=False, auto_adjust=True)["Close"]
    except Exception:
        return None
    if isinstance(raw, _pd.Series):
        raw = raw.to_frame()
    if raw.index.tz is not None:
        raw.index = raw.index.tz_localize(None)
    rets = raw.pct_change().dropna(how="all").tail(days)

    _RETURNS_CACHE["key"] = key
    _RETURNS_CACHE["data"] = rets
    _RETURNS_CACHE["vol"] = None  # invalidate dependent vol cache
    return rets


def _compute_portfolio_risk(ticker_weights: dict[str, float]) -> dict:
    """Buy-and-hold-of-current-basket risk metrics over the last ~252 trading
    days.

    Weights are current $ exposure / portfolio_total. We construct the daily
    portfolio return as ``Σ w_i r_i,t`` using those frozen weights, then derive
    annualized σ, Sharpe (vs RISK_FREE_RATE_ANNUAL), Sortino (downside-only σ),
    historical max drawdown, and current drawdown from peak.

    This is an ex-ante risk snapshot, not a P&L reconstruction — it tells the
    user "if I'd held today's basket for the past year, what would risk have
    looked like." For a P&L history we'd need to replay executions, which the
    /performance page already does."""
    import math as _math
    import pandas as _pd

    syms = [s for s, w in ticker_weights.items() if w and w > 0]
    total_w = sum(ticker_weights[s] for s in syms)
    if not syms or total_w <= 0:
        return {}

    # +SPY so we can show benchmark Sharpe side-by-side. Missing data on SPY is
    # not fatal — we just suppress the benchmark line.
    rets = _fetch_returns_matrix(syms + ["SPY"], days=252)
    if rets is None or rets.empty:
        return {}

    # Restrict to columns we actually got data for; renormalize.
    available = [s for s in syms if s in rets.columns]
    if not available:
        return {}
    w_norm = _pd.Series(
        {s: ticker_weights[s] / sum(ticker_weights[k] for k in available) for s in available}
    )
    port_rets = (rets[available].fillna(0).mul(w_norm, axis=1)).sum(axis=1)
    port_rets = port_rets[port_rets != 0]  # drop pre-listing days where every weight is 0
    if len(port_rets) < 20:
        return {}

    daily_mu = float(port_rets.mean())
    daily_sigma = float(port_rets.std(ddof=1))
    ann_mu = daily_mu * 252
    ann_sigma = daily_sigma * _math.sqrt(252)

    excess_ann = ann_mu - RISK_FREE_RATE_ANNUAL
    # 1e-6 = effectively-zero floor. Constant-return series gives float-noise σ
    # which would otherwise yield astronomical Sharpe (e.g. 5e16) from ε in the
    # denominator. Returning None is more honest.
    sharpe = (excess_ann / ann_sigma) if ann_sigma > 1e-6 else None

    downside = port_rets[port_rets < 0]
    down_sigma_ann = float(downside.std(ddof=1)) * _math.sqrt(252) if len(downside) > 1 else None
    sortino = (excess_ann / down_sigma_ann) if (down_sigma_ann and down_sigma_ann > 1e-6) else None

    cum = (1 + port_rets).cumprod()
    running_peak = cum.cummax()
    drawdown = (cum / running_peak - 1)
    max_dd = float(drawdown.min())
    current_dd = float(drawdown.iloc[-1])

    # SPY benchmark for the same window (raw, not weighted).
    spy_sharpe = None
    if "SPY" in rets.columns:
        spy_rets = rets["SPY"].dropna()
        if len(spy_rets) >= 20:
            spy_ann_mu = float(spy_rets.mean()) * 252
            spy_ann_sigma = float(spy_rets.std(ddof=1)) * _math.sqrt(252)
            spy_sharpe = ((spy_ann_mu - RISK_FREE_RATE_ANNUAL) / spy_ann_sigma) if spy_ann_sigma > 1e-6 else None

    return {
        "sigma_annual": ann_sigma,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "current_drawdown": current_dd,
        "spy_sharpe": spy_sharpe,
        "lookback_days": int(len(port_rets)),
        "coverage_syms": len(available),
        "total_syms": len(syms),
    }


def _compute_vol_targets(symbols: list[str], portfolio_total: float) -> dict[str, dict]:
    """Per-ticker realized annualized vol + the dollar size that puts the
    position at RISK_BUDGET_PER_POSITION_PCT of portfolio risk.

    Uses the same 252d returns matrix as _compute_portfolio_risk. Returns
    {symbol: {vol_annual, vol_target_$, vol_target_pct_portfolio}}."""
    import math as _math

    if not symbols or portfolio_total <= 0:
        return {}
    rets = _fetch_returns_matrix(symbols, days=252)
    if rets is None or rets.empty:
        return {}

    budget_dollars = portfolio_total * (RISK_BUDGET_PER_POSITION_PCT / 100.0)
    out: dict[str, dict] = {}
    for sym in symbols:
        if sym not in rets.columns:
            continue
        col = rets[sym].dropna().tail(60)  # 60-day realized vol — responsive but not noisy
        if len(col) < 20:
            continue
        sigma_ann = float(col.std(ddof=1)) * _math.sqrt(252)
        if sigma_ann <= 1e-6:
            continue
        target_dollars = budget_dollars / sigma_ann
        out[sym] = {
            "vol_annual": sigma_ann,
            "vol_target_dollars": target_dollars,
            "vol_target_pct_portfolio": (target_dollars / portfolio_total * 100),
        }
    return out


def _compute_rating_calibration(by_rating: dict, windows: list[int]) -> dict:
    """Rank correlation between rating bullishness and realized α at each
    forward window.

    Maps bullishness ordinally (Buy=2, Overweight=1, Hold=0, Underweight=-1,
    Sell=-2), weights each bucket by its N, and computes Spearman rank
    correlation against mean α. Positive = rating ordering is predictive.

    Also flags strict monotonicity: do the buckets line up bullish→bearish in
    α order? Two checks fail independently — a strong corr with one inversion
    is more useful than knowing only "not perfectly monotone"."""
    import math as _math

    bullishness = {"Buy": 2, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -2}
    calib: dict[int, dict] = {}
    for n in windows:
        pts: list[tuple[int, float, int]] = []  # (bullishness, mean_a, N)
        for rating, agg in by_rating.items():
            if rating not in bullishness:
                continue
            ma = agg.get(f"mean_a{n}")
            ni = agg.get(f"n{n}", 0)
            if ma is None or ni <= 0:
                continue
            pts.append((bullishness[rating], float(ma), int(ni)))
        if len(pts) < 3:
            calib[n] = {"spearman": None, "monotone": None, "n_buckets": len(pts), "buckets": pts}
            continue
        # Weighted Pearson on ranks ≡ a sample-weighted Spearman.
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ws = [p[2] for p in pts]
        # Rank xs (already ints) and ys.
        x_ranks = _rank_with_ties(xs)
        y_ranks = _rank_with_ties(ys)
        sw = sum(ws)
        mx = sum(w * r for w, r in zip(ws, x_ranks)) / sw
        my = sum(w * r for w, r in zip(ws, y_ranks)) / sw
        cov = sum(w * (xr - mx) * (yr - my) for w, xr, yr in zip(ws, x_ranks, y_ranks)) / sw
        vx = sum(w * (xr - mx) ** 2 for w, xr in zip(ws, x_ranks)) / sw
        vy = sum(w * (yr - my) ** 2 for w, yr in zip(ws, y_ranks)) / sw
        denom = _math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0
        spearman = (cov / denom) if denom > 0 else None
        # Monotonicity: sort by bullishness desc, check α decreases (or at
        # least non-increases).
        ordered = sorted(pts, key=lambda p: -p[0])
        monotone = all(ordered[i][1] >= ordered[i + 1][1] for i in range(len(ordered) - 1))
        calib[n] = {
            "spearman": spearman,
            "monotone": monotone,
            "n_buckets": len(pts),
            "buckets": ordered,
        }
    return calib


def _compute_concentration_breaches() -> dict:
    """Surface positions / sectors / owners that exceed configured caps.

    Reads the most recent positions_snapshot, aggregates value three ways
    (ticker, sector, owner), and returns the rows that breach the threshold
    in each dimension, sorted by overshoot magnitude (worst first). The cap
    is reported alongside so the template can render "actual / threshold"
    without re-deriving constants."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.symbol, p.account_id, p.account_name, p.owner,
                   COALESCE(t.last_price, p.last_price) * p.quantity AS value,
                   t.sector AS sector
            FROM positions_snapshot p
            LEFT JOIN tickers t ON t.symbol = p.symbol
            WHERE p.import_date = (SELECT MAX(import_date) FROM positions_snapshot)
            """
        ).fetchall()

    total = sum((r["value"] or 0) for r in rows)
    if total <= 0:
        return {"ticker": [], "sector": [], "owner": [], "total": 0}

    ticker_totals: dict[str, float] = {}
    sector_totals: dict[str, float] = {}
    owner_totals: dict[str, float] = {}
    for r in rows:
        v = r["value"] or 0
        if v <= 0:
            continue
        ticker_totals[r["symbol"]] = ticker_totals.get(r["symbol"], 0) + v
        sec = r["sector"] or "Unknown"
        sector_totals[sec] = sector_totals.get(sec, 0) + v
        owner_totals[r["owner"] or "—"] = owner_totals.get(r["owner"] or "—", 0) + v

    def _breaches(buckets: dict[str, float], cap: float) -> list[dict]:
        out = []
        for name, value in buckets.items():
            pct = value / total * 100
            if pct > cap:
                out.append({
                    "name": name,
                    "value": value,
                    "pct": pct,
                    "cap": cap,
                    "overshoot": pct - cap,
                })
        return sorted(out, key=lambda x: -x["overshoot"])

    return {
        "ticker": _breaches(ticker_totals, CONCENTRATION_TICKER_MAX_PCT),
        "sector": _breaches(sector_totals, CONCENTRATION_SECTOR_MAX_PCT),
        "owner": _breaches(owner_totals, CONCENTRATION_OWNER_MAX_PCT),
        "total": total,
    }


def _fetch_avg_dollar_volume(symbols: list[str], days: int = 20) -> dict[str, float]:
    """Average daily dollar volume = close × volume averaged over last ``days``
    trading days. Used to flag /today reduce/add sizes that are large relative
    to a ticker's normal liquidity (>5% of ADV is a rough rule of thumb for
    when market-impact slippage stops being negligible).

    yfinance with ``auto_adjust=True`` returns split-adjusted close *and*
    volume, so the product is internally consistent for cross-split history.
    Cached hourly — ADV moves slowly enough that intraday refreshes are noise."""
    import pandas as _pd
    import yfinance as _yf
    from datetime import date as _date, timedelta as _td
    import time as _time

    syms = tuple(sorted(set(symbols)))
    if not syms:
        return {}
    key = (syms, days, int(_time.time() / 3600))
    if _ADV_CACHE["key"] == key:
        return _ADV_CACHE["data"]

    end = _date.today() + _td(days=1)
    start = end - _td(days=int(days * 2) + 14)
    try:
        raw = _yf.download(list(syms), start=start, end=end, progress=False, auto_adjust=True)
    except Exception:
        return {}
    if raw is None or raw.empty:
        return {}
    try:
        close = raw["Close"]
        vol = raw["Volume"]
    except Exception:
        return {}
    if isinstance(close, _pd.Series):
        close = close.to_frame(name=syms[0])
        vol = vol.to_frame(name=syms[0])

    out: dict[str, float] = {}
    for sym in syms:
        if sym not in close.columns or sym not in vol.columns:
            continue
        c = close[sym].dropna().tail(days)
        v = vol[sym].dropna().tail(days)
        df = _pd.concat([c, v], axis=1, join="inner").dropna()
        if df.empty:
            continue
        dollar_vol = float((df.iloc[:, 0] * df.iloc[:, 1]).mean())
        if dollar_vol > 0:
            out[sym] = dollar_vol

    _ADV_CACHE["key"] = key
    _ADV_CACHE["data"] = out
    return out


def _rank_with_ties(xs: list[float]) -> list[float]:
    """Average-rank ties so Spearman is well-defined on small bucket counts."""
    indexed = sorted(enumerate(xs), key=lambda t: t[1])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _compute_rsi(close: "pd.Series", n: int = 14) -> float | None:
    """Wilder-smoothed RSI(n). EWMA with alpha=1/n mirrors the original
    Wilder average without storing the prior-day smoothing state."""
    import math as _math
    if len(close) < n + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    last_loss = float(avg_loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(avg_gain.iloc[-1]) / last_loss
    rsi = 100 - 100 / (1 + rs)
    return None if _math.isnan(rsi) else rsi


def _compute_macd(close: "pd.Series") -> dict | None:
    """Standard 12/26/9 MACD. Returns the latest values plus the prior bar's
    histogram so the caller can tell if momentum is accelerating or decaying."""
    import math as _math
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    last = float(hist.iloc[-1])
    if _math.isnan(last):
        return None
    return {
        "macd": float(macd.iloc[-1]),
        "signal": float(signal.iloc[-1]),
        "hist": last,
        "prev_hist": float(hist.iloc[-2]) if len(hist) > 1 else None,
    }


def _compute_atr(high: "pd.Series", low: "pd.Series", close: "pd.Series", n: int = 14) -> float | None:
    """Wilder ATR(n). Same Wilder-as-EWMA trick as _compute_rsi."""
    import math as _math
    import pandas as _pd
    if len(close) < n + 1:
        return None
    prev_close = close.shift(1)
    tr = _pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    last = float(atr.iloc[-1])
    return None if _math.isnan(last) else last


def _compute_technicals() -> dict:
    """Per-ticker technical snapshot: SMA50/200 cross, 52-week range, plus
    RSI(14) / MACD(12,26,9) / ATR(14).

    One yfinance batch fetches 1y of daily OHLC for every symbol in the
    latest positions snapshot; everything else is pandas on that frame. Joined
    against the latest PM rating so conflicts (e.g. Overweight + death cross)
    surface in the same view."""
    import yfinance as _yf
    import pandas as _pd
    from datetime import date as _date, timedelta as _td

    with connect() as conn:
        symbols = [r["symbol"] for r in conn.execute(
            "SELECT DISTINCT symbol FROM positions_snapshot "
            "WHERE import_date = (SELECT MAX(import_date) FROM positions_snapshot)"
        ).fetchall()]
        rating_rows = conn.execute(
            """
            WITH latest AS (
                SELECT symbol, MAX(trade_date) AS d FROM decisions GROUP BY symbol
            )
            SELECT d.symbol, d.rating, d.trade_date FROM decisions d
            JOIN latest l ON l.symbol = d.symbol AND l.d = d.trade_date
            """
        ).fetchall()
    rating_by_sym = {r["symbol"]: (r["rating"], r["trade_date"]) for r in rating_rows}

    if not symbols:
        return {"rows": [], "as_of": _date.today().isoformat()}

    end = _date.today() + _td(days=1)
    start = end - _td(days=400)  # 400d buffer for 200 SMA + weekend gaps
    try:
        raw = _yf.download(symbols, start=start, end=end, progress=False, auto_adjust=True)
    except Exception as e:
        return {"error": f"yfinance error: {e}", "items": [], "as_of": _date.today().isoformat()}
    # yfinance returns a flat columns frame for one symbol, multi-index for
    # multiple; normalize so per-symbol slicing is uniform downstream.
    try:
        close_df = raw["Close"]
        high_df = raw["High"]
        low_df = raw["Low"]
    except Exception as e:
        return {"error": f"yfinance shape error: {e}", "items": [], "as_of": _date.today().isoformat()}
    if isinstance(close_df, _pd.Series):
        close_df = close_df.to_frame(name=symbols[0])
        high_df = high_df.to_frame(name=symbols[0])
        low_df = low_df.to_frame(name=symbols[0])
    data = close_df  # SMA/52w block downstream still calls this ``data``

    rows: list[dict] = []
    for sym in symbols:
        if sym not in data.columns:
            continue
        col = data[sym].dropna()
        if col.empty:
            continue
        price = float(col.iloc[-1])
        sma50 = float(col.tail(50).mean()) if len(col) >= 50 else None
        sma200 = float(col.tail(200).mean()) if len(col) >= 200 else None

        # 52-week window. If we have <252 trading days, use what we have.
        win = col.tail(252) if len(col) >= 252 else col
        w_high = float(win.max())
        w_low = float(win.min())
        # 0% = at 52w low, 100% = at 52w high. Helps spot stretched buys /
        # capitulation entries at a glance.
        pos_pct = ((price - w_low) / (w_high - w_low) * 100) if w_high > w_low else None
        off_high_pct = ((price - w_high) / w_high * 100) if w_high > 0 else None

        # Trend regime + crossover detection. We walk the last 60 days of the
        # SMA-difference series and pick up the most recent sign flip to tell
        # the user how fresh the regime is.
        cross_label, cross_days = "—", None
        if sma50 is not None and sma200 is not None:
            trend = "golden" if sma50 > sma200 else "death"
            cross_label = "🌟 金叉" if trend == "golden" else "💀 死叉"
            if len(col) >= 200:
                s50 = col.rolling(50).mean()
                s200 = col.rolling(200).mean()
                diff = (s50 - s200).dropna()
                if len(diff) >= 2:
                    sign = (diff > 0).astype(int)
                    flips = sign.diff().fillna(0)
                    flip_idx = flips[flips != 0].index
                    if len(flip_idx) > 0:
                        last_flip = flip_idx[-1]
                        cross_days = (col.index[-1] - last_flip).days

        rating, rating_date = rating_by_sym.get(sym, (None, None))
        # Conflict = PM thesis and trend disagree. Bullish call against a
        # death cross (or bearish against a golden cross) is the interesting
        # case to look at twice.
        conflict = None
        if rating and sma50 is not None and sma200 is not None:
            if rating in ("Overweight", "Buy") and sma50 < sma200:
                conflict = "rating_vs_death"
            elif rating in ("Underweight", "Sell") and sma50 > sma200:
                conflict = "rating_vs_golden"

        rsi = _compute_rsi(col)
        macd = _compute_macd(col)
        atr = None
        atr_pct = None
        if sym in high_df.columns and sym in low_df.columns:
            atr = _compute_atr(high_df[sym].dropna(), low_df[sym].dropna(), col)
            atr_pct = (atr / price * 100) if (atr is not None and price > 0) else None
        macd_direction = None
        if macd is not None:
            if macd["prev_hist"] is None:
                macd_direction = "flat"
            elif macd["hist"] > macd["prev_hist"]:
                macd_direction = "accelerating" if macd["hist"] > 0 else "decelerating_down"
            elif macd["hist"] < macd["prev_hist"]:
                macd_direction = "decelerating" if macd["hist"] > 0 else "accelerating_down"
            else:
                macd_direction = "flat"

        rows.append({
            "symbol": sym,
            "rating": rating,
            "rating_date": rating_date,
            "price": price,
            "sma50": sma50,
            "sma200": sma200,
            "trend_label": cross_label,
            "trend": "golden" if (sma50 and sma200 and sma50 > sma200) else (
                "death" if (sma50 and sma200 and sma50 < sma200) else None
            ),
            "rsi": rsi,
            "rsi_zone": ("overbought" if rsi is not None and rsi > 70 else
                         "oversold" if rsi is not None and rsi < 30 else
                         "neutral" if rsi is not None else None),
            "macd_hist": macd["hist"] if macd else None,
            "macd_direction": macd_direction,
            "atr_pct": atr_pct,
            "cross_days": cross_days,
            "w52_high": w_high,
            "w52_low": w_low,
            "pos_pct": pos_pct,
            "off_high_pct": off_high_pct,
            "conflict": conflict,
        })

    # Surface conflicts first, then bearish (death), then bullish (golden).
    # Within each, recent crossovers (low cross_days) lead — fresh regime
    # changes are more actionable than year-old ones.
    def _rank(it):
        c = 0 if it["conflict"] else 1
        t = {"death": 0, "golden": 1, None: 2}[it["trend"]]
        d = it["cross_days"] if it["cross_days"] is not None else 9999
        return (c, t, d, it["symbol"])

    rows.sort(key=_rank)

    counts = {
        "golden": sum(1 for x in rows if x["trend"] == "golden"),
        "death": sum(1 for x in rows if x["trend"] == "death"),
        "conflict": sum(1 for x in rows if x["conflict"]),
    }

    return {
        "rows": rows,
        "counts": counts,
        "as_of": _date.today().isoformat(),
    }


@app.get("/tech", response_class=HTMLResponse)
def tech_view():
    """Technical-analysis snapshot per ticker, joined to latest PM rating."""
    import time as _time
    cache_key = int(_time.time() / 3600)
    if _TECH_CACHE["key"] != cache_key:
        _TECH_CACHE["result"] = _compute_technicals()
        _TECH_CACHE["key"] = cache_key
    tech = _TECH_CACHE["result"]

    return templates.TemplateResponse(
        _dummy_request(), "tech.html",
        {"css": CSS, "tech": tech},
    )


def _compute_backtest(windows: list[int]) -> dict:
    """Forward-return backtest of (trade_date, symbol, rating) decisions.

    For each decision, computes forward return at each window (in trading
    days). Reports per-rating mean return, win rate, and alpha vs SPY. Pulls
    daily closes from yfinance in one batch and reuses them across windows.
    """
    import yfinance as _yf
    import pandas as _pd
    from datetime import timedelta as _td, date as _date

    with connect() as conn:
        decisions = conn.execute(
            "SELECT trade_date, symbol, rating FROM decisions"
        ).fetchall()
    if not decisions:
        return {"rows": [], "by_rating": {}, "n_total": 0, "windows": windows, "as_of": None}

    symbols = sorted({d["symbol"] for d in decisions} | {"SPY"})
    min_date = min(d["trade_date"] for d in decisions)
    # Pull from 7 days before earliest decision to give yfinance a buffer for
    # weekends; extend through today.
    start = (_pd.to_datetime(min_date) - _pd.Timedelta(days=7)).date()
    end = _date.today() + _td(days=1)

    try:
        data = _yf.download(symbols, start=start, end=end, progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return {"error": f"yfinance error: {e}", "rows": [], "by_rating": {}, "n_total": 0, "windows": windows}

    if isinstance(data, _pd.Series):
        data = data.to_frame()
    data.index = data.index.tz_localize(None) if data.index.tz else data.index

    def _entry_idx(idx: _pd.DatetimeIndex, td: str) -> int | None:
        """First trading day at or after td."""
        td_ts = _pd.to_datetime(td)
        pos = idx.searchsorted(td_ts)
        return int(pos) if pos < len(idx) else None

    def _fwd_return(sym: str, td: str, n: int) -> float | None:
        if sym not in data.columns:
            return None
        col = data[sym].dropna()
        if col.empty:
            return None
        i0 = _entry_idx(col.index, td)
        if i0 is None or i0 + n >= len(col):
            return None
        p0 = float(col.iloc[i0])
        p1 = float(col.iloc[i0 + n])
        return (p1 / p0 - 1) if p0 > 0 else None

    rows = []
    for d in decisions:
        sym, rating, td = d["symbol"], d["rating"], d["trade_date"]
        rec = {"symbol": sym, "rating": rating, "trade_date": td}
        for n in windows:
            r = _fwd_return(sym, td, n)
            b = _fwd_return("SPY", td, n)
            rec[f"r{n}"] = r
            rec[f"b{n}"] = b
            rec[f"a{n}"] = (r - b) if (r is not None and b is not None) else None
        rows.append(rec)

    # Aggregate by rating. Bullish ratings should produce positive alpha;
    # bearish should produce negative alpha (and we show the SAME alpha
    # number — the user mentally negates for shorts. Avoiding sign flips
    # keeps the table honest: spread = bullish_alpha − bearish_alpha is the
    # predictive-power metric to watch.)
    by_rating: dict[str, dict] = {}
    for rating in _RATING_ORDER:
        subset = [r for r in rows if r["rating"] == rating]
        agg = {"n": len(subset)}
        for n in windows:
            vals = [r[f"r{n}"] for r in subset if r[f"r{n}"] is not None]
            alphas = [r[f"a{n}"] for r in subset if r[f"a{n}"] is not None]
            agg[f"n{n}"] = len(vals)
            agg[f"mean_r{n}"] = (sum(vals) / len(vals)) if vals else None
            agg[f"win_r{n}"] = (sum(1 for v in vals if v > 0) / len(vals)) if vals else None
            agg[f"mean_a{n}"] = (sum(alphas) / len(alphas)) if alphas else None
        by_rating[rating] = agg

    # Bull − bear alpha spread = the headline number. Positive means
    # ratings have predictive power on average.
    spreads = {}
    for n in windows:
        bull = (by_rating.get("Overweight", {}).get(f"mean_a{n}") or 0)
        bear = (by_rating.get("Underweight", {}).get(f"mean_a{n}") or 0)
        bull_n = by_rating.get("Overweight", {}).get(f"n{n}", 0)
        bear_n = by_rating.get("Underweight", {}).get(f"n{n}", 0)
        spreads[n] = {"value": bull - bear, "n_bull": bull_n, "n_bear": bear_n}

    rows.sort(key=lambda r: (r["trade_date"], r["symbol"]))

    # Rating calibration: does bullishness order predict α order?
    calibration = _compute_rating_calibration(by_rating, windows)
    return {
        "rows": rows,
        "by_rating": by_rating,
        "spreads": spreads,
        "calibration": calibration,
        "n_total": len(rows),
        "windows": windows,
        "as_of": _date.today().isoformat(),
    }


@app.get("/backtest", response_class=HTMLResponse)
def backtest_view():
    """Per-rating forward return + alpha vs SPY. Cached for 1h since the
    underlying decisions table only updates after nightly migrate."""
    import time as _time
    # 2d included so users see something even when the system is young.
    # 5d and 20d are the conventional swing- and monthly-horizon checks.
    windows = [2, 5, 20]
    cache_key = f"{windows}-{int(_time.time() / 3600)}"
    if _BACKTEST_CACHE["key"] != cache_key:
        _BACKTEST_CACHE["result"] = _compute_backtest(windows)
        _BACKTEST_CACHE["key"] = cache_key
    bt = _BACKTEST_CACHE["result"]

    return templates.TemplateResponse(
        _dummy_request(), "backtest.html",
        {"css": CSS, "bt": bt, "rating_order": _RATING_ORDER},
    )


@app.get("/alerts", response_class=HTMLResponse)
def alerts_view():
    """Stop-loss / price-target proximity monitor.

    For each ticker, takes the latest decision (most recent trade_date), parses
    stop_loss / price_target from the markdown, and compares against the
    latest close. Surfaces breaches (price <= stop, or price >= target) plus
    near-stop warnings so the user can react without re-reading every report.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT symbol, MAX(trade_date) AS d FROM decisions GROUP BY symbol
            )
            SELECT d.trade_date, d.symbol, d.rating, d.final_decision,
                   t.last_price, t.last_updated
            FROM decisions d
            JOIN latest l ON l.symbol = d.symbol AND l.d = d.trade_date
            LEFT JOIN tickers t ON t.symbol = d.symbol
            ORDER BY d.symbol
            """
        ).fetchall()

    # Bullish-thesis ratings expect price ↑; bearish expect price ↓. Stop and
    # target sit on opposite sides for each. For Hold, levels (if any) are
    # informational only — skip status flagging.
    bullish = {"Buy", "Overweight"}
    bearish = {"Underweight", "Sell"}

    items = []
    for r in rows:
        price_target, stop_loss = _extract_price_levels(r["final_decision"])
        price = r["last_price"]
        rating = r["rating"]
        if not price or (stop_loss is None and price_target is None):
            continue

        # Distance is always signed *toward the thesis playing out*:
        #   bullish: target above (positive Δ = more upside left)
        #            stop below (positive Δ = cushion remaining)
        #   bearish: target below (positive Δ = more downside expected)
        #            stop above (positive Δ = headroom before stopped out)
        if rating in bearish:
            dist_to_stop_pct = ((stop_loss - price) / price * 100) if stop_loss else None
            dist_to_target_pct = ((price - price_target) / price * 100) if price_target else None
            stop_hit = stop_loss is not None and price >= stop_loss
            target_hit = price_target is not None and price <= price_target
        else:
            dist_to_stop_pct = ((price - stop_loss) / price * 100) if stop_loss else None
            dist_to_target_pct = ((price_target - price) / price * 100) if price_target else None
            stop_hit = stop_loss is not None and price <= stop_loss
            target_hit = price_target is not None and price >= price_target

        if rating == "Hold":
            status, status_label = "ok", "✓ (Hold)"
        elif stop_hit:
            status, status_label = "stop_breached", "🛑 触发止损"
        elif target_hit:
            status, status_label = "target_hit", "🎯 触及目标"
        elif dist_to_stop_pct is not None and dist_to_stop_pct < 5:
            status, status_label = "stop_warn", "⚠️ 接近止损"
        else:
            status, status_label = "ok", "✓"

        items.append({
            "symbol": r["symbol"],
            "rating": rating,
            "trade_date": r["trade_date"],
            "price": price,
            "stop_loss": stop_loss,
            "price_target": price_target,
            "dist_to_stop_pct": dist_to_stop_pct,
            "dist_to_target_pct": dist_to_target_pct,
            "status": status,
            "status_label": status_label,
            "last_updated": r["last_updated"],
            "is_bearish": rating in bearish,
        })

    _status_rank = {"stop_breached": 0, "target_hit": 1, "stop_warn": 2, "ok": 3}
    items.sort(key=lambda x: (_status_rank[x["status"]], x["symbol"]))

    counts = {k: sum(1 for it in items if it["status"] == k) for k in _status_rank}
    concentration = _compute_concentration_breaches()

    return templates.TemplateResponse(
        _dummy_request(), "alerts.html",
        {
            "css": CSS, "items": items, "counts": counts, "total": len(items),
            "concentration": concentration,
        },
    )


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

    decision_html = _render_pm_markdown(row["final_decision"])
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


_CACHE_FRESHNESS_MIN = 30  # minutes — recent decisions reused unless force=1


def _find_inflight_run(want_meta: dict[str, str]) -> str | None:
    """Return a run_id with the same parameters that is still in flight."""
    import glob as _g
    for meta_path in sorted(_g.glob("/tmp/pm_run_*.meta"), reverse=True)[:50]:
        try:
            meta = {}
            for line in Path(meta_path).read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    meta[k] = v
        except OSError:
            continue
        # Match the parameters we care about
        if not all(meta.get(k, "") == v for k, v in want_meta.items()):
            continue
        run_id = Path(meta_path).stem.replace("pm_run_", "")
        status_path = f"/tmp/pm_run_{run_id}.status"
        pid_path = f"/tmp/pm_run_{run_id}.pid"
        effective, _ = _classify_run(f"/tmp/pm_run_{run_id}.log", status_path, pid_path)
        if effective in ("queued", "running"):
            return run_id
    return None


def _cached_decisions(tickers: list[str], instruction: str = "",
                      freshness_min: int = _CACHE_FRESHNESS_MIN) -> dict[str, str]:
    """Return {symbol: trade_date} for tickers with a decision created within
    the freshness window AND with a matching instruction. Used to skip a
    re-run when nothing has changed.

    decisions.created_at is stored as UTC (sqlite ``datetime('now')`` default),
    so we compare against UTC — never use ``localtime`` here, that's a 7-8h bug.

    Instruction match: empty input matches NULL/empty stored instructions;
    non-empty must match exactly. A run with a different instruction is a
    different question and should not be cache-hit.
    """
    if not tickers:
        return {}
    norm_instruction = (instruction or "").strip()
    with connect() as conn:
        placeholders = ",".join("?" * len(tickers))
        if norm_instruction:
            sql = f"""
                SELECT symbol, MAX(created_at) AS latest_at, trade_date
                  FROM decisions
                 WHERE symbol IN ({placeholders})
                   AND created_at >= datetime('now', ?)
                   AND COALESCE(instruction, '') = ?
                 GROUP BY symbol
            """
            params = (*tickers, f"-{freshness_min} minutes", norm_instruction)
        else:
            sql = f"""
                SELECT symbol, MAX(created_at) AS latest_at, trade_date
                  FROM decisions
                 WHERE symbol IN ({placeholders})
                   AND created_at >= datetime('now', ?)
                   AND COALESCE(instruction, '') = ''
                 GROUP BY symbol
            """
            params = (*tickers, f"-{freshness_min} minutes")
        rows = conn.execute(sql, params).fetchall()
    return {r["symbol"]: r["trade_date"] for r in rows}


@app.post("/run", response_class=HTMLResponse)
def run_analysis(
    mode: str = Form("tickers"),
    tickers: str = Form(""),
    sector: str = Form(""),
    instruction: str = Form(""),
    force: int = Form(0),
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
        if not force:
            existing = _find_inflight_run({
                "mode": "sector", "sector": sector,
                "instruction": instruction[:500],
            })
            if existing:
                return _render(
                    message=(
                        f"🔁 同样的行业分析 <code>{existing}</code> 还在跑，自动跳到那个进度。"
                        f' 想强制重跑就回到首页 🚀 modal 用相同参数 + 勾选"强制重跑"。'
                        f' <a href="/runs/{existing}">查看进度</a>'
                    ),
                    level="success",
                )
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

    # Dedup checks — skip when force=1.
    if not force:
        # 1. In-flight: same ticker list + same instruction still running?
        canon_tickers = ",".join(sorted(ticker_list))
        existing = _find_inflight_run({
            "tickers": ",".join(ticker_list),
            "instruction": instruction[:500],
        })
        if existing:
            return _render(
                message=(
                    f"🔁 相同参数 ({canon_tickers}) 的 run <code>{existing}</code> 还在跑，"
                    f'跳到那个进度。 <a href="/runs/{existing}">查看 →</a>'
                ),
                level="success",
            )

        # 2. Cache: all tickers have a decision created within freshness window
        # AND with a matching instruction?
        cached = _cached_decisions(ticker_list, instruction=instruction)
        if cached and all(t in cached for t in ticker_list):
            links = " · ".join(f'<a href="/decisions/{t}">{t}</a>' for t in ticker_list)
            return _render(
                message=(
                    f'🗄️ {_CACHE_FRESHNESS_MIN} 分钟内已分析过这些 ticker，跳过重跑。'
                    f' 查看: {links} · '
                    f'<a href="/?force_msg=use_force">强制重跑见说明</a>'
                ),
                level="success",
            )

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

    report_path = out_dir / "REPORT.md"
    if report_path.exists():
        report_html = _render_pm_markdown(report_path.read_text(encoding="utf-8"))
    else:
        report_html = "<p style='color:var(--fg-muted);'>REPORT.md not found in output dir.</p>"

    failed_path = out_dir / "failed.txt"
    failed_tickers: list[str] = []
    if failed_path.exists():
        failed_tickers = [
            line.strip()
            for line in failed_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

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
            "failed_tickers": failed_tickers,
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
