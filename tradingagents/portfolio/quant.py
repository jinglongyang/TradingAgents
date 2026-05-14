"""Pure quantitative helpers for the portfolio dashboard.

Functions here are price-/math-only and do not touch the database. The
portfolio_server.py routes import what they need and combine these with
DB-backed reads (positions, decisions, ticker fundamentals).

Kept deliberately stateless except for two hourly fetch caches — yfinance
batch downloads are slow enough that re-fetching every page load would
double the latency of /today and /backtest.

Public surface (used by tests + portfolio_server):
    Constants  : RISK_FREE_RATE_ANNUAL, RISK_BUDGET_PER_POSITION_PCT,
                 CONCENTRATION_TICKER_MAX_PCT, CONCENTRATION_SECTOR_MAX_PCT,
                 CONCENTRATION_OWNER_MAX_PCT
    Caches     : _RETURNS_CACHE, _ADV_CACHE (reset by tests)
    Fetchers   : _fetch_returns_matrix, _fetch_avg_dollar_volume
    Risk       : _compute_portfolio_risk, _compute_vol_targets
    Momentum   : _compute_momentum_12_1
    Indicators : _compute_rsi, _compute_macd, _compute_atr
    Calibration: _compute_rating_calibration, _rank_with_ties
"""

from __future__ import annotations

# Hourly caches keyed on (sorted symbol tuple, days, hour bucket). Re-keying
# lets symbol-set changes invalidate the entry without an explicit clear.
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


def _compute_momentum_12_1(symbols: list[str]) -> dict[str, float]:
    """12-1 month momentum: return from ~252 trading days ago to ~21 days ago,
    *excluding* the last 21 days. Skipping the most recent month avoids the
    well-known short-term reversal effect that contaminates a raw 12m return.

    Uses the existing returns matrix cache for cheap reuse alongside the
    /today σ/Sharpe and vol-target computations."""
    rets = _fetch_returns_matrix(symbols, days=252)
    if rets is None or rets.empty:
        return {}
    if len(rets) < 60:
        return {}

    # Window: drop the last 21 daily returns, then compound the rest.
    window = rets.iloc[:-21] if len(rets) > 21 else rets
    if window.empty:
        return {}
    out: dict[str, float] = {}
    for sym in symbols:
        if sym not in window.columns:
            continue
        col = window[sym].dropna()
        if len(col) < 30:
            continue
        compound = float((1 + col).prod() - 1)
        out[sym] = compound
    return out


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
