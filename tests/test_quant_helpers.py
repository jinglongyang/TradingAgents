"""Unit tests for /today and /backtest quantitative helpers.

Pure helpers (_rank_with_ties, _compute_rating_calibration) are tested
directly. Helpers that fetch from yfinance (_compute_portfolio_risk,
_compute_vol_targets, _fetch_avg_dollar_volume) are tested by monkey-
patching the fetcher to inject deterministic fixture data."""

from __future__ import annotations

import math

import pandas as pd
import pytest

import scripts.portfolio_server as ps


# --------------------------------------------------------------------------
# _rank_with_ties — average-rank ties, used inside calibration's weighted
# Spearman. Standard pattern: ties get the mean of the positions they occupy.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestRankWithTies:
    def test_strict_order(self):
        assert ps._rank_with_ties([10, 20, 30]) == [1.0, 2.0, 3.0]

    def test_strict_order_unsorted_input(self):
        # Smallest is 5 → rank 1; largest is 30 → rank 3.
        assert ps._rank_with_ties([20, 5, 30]) == [2.0, 1.0, 3.0]

    def test_pair_of_ties(self):
        # Sorted positions: (1,1,2,3) at ranks 1,2,3,4. Two 1's share (1+2)/2=1.5.
        assert ps._rank_with_ties([3.0, 1.0, 2.0, 1.0]) == [4.0, 1.5, 3.0, 1.5]

    def test_all_ties(self):
        # Three equal values at positions 1,2,3 → all get rank 2.
        assert ps._rank_with_ties([5, 5, 5]) == [2.0, 2.0, 2.0]

    def test_three_way_tie_then_singleton(self):
        # [7,7,7,9] → sorted positions 1,2,3,4. Triple gets (1+2+3)/3=2.
        assert ps._rank_with_ties([7, 7, 7, 9]) == [2.0, 2.0, 2.0, 4.0]


# --------------------------------------------------------------------------
# _compute_rating_calibration — weighted Spearman + monotonicity check on the
# (bullishness ordinal, mean_alpha) pairs. Bullishness is Buy=2 … Sell=-2.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestRatingCalibration:
    def test_perfectly_monotone_yields_spearman_one(self):
        by_rating = {
            "Buy":         {"mean_a5": 0.05, "n5": 10},
            "Overweight":  {"mean_a5": 0.02, "n5": 20},
            "Hold":        {"mean_a5": 0.00, "n5": 30},
            "Underweight": {"mean_a5": -0.01, "n5": 10},
            "Sell":        {"mean_a5": -0.03, "n5": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5])
        assert out[5]["spearman"] == pytest.approx(1.0)
        assert out[5]["monotone"] is True
        assert out[5]["n_buckets"] == 5
        # Buckets returned sorted by bullishness desc (Buy first).
        assert [b[0] for b in out[5]["buckets"]] == [2, 1, 0, -1, -2]

    def test_inversion_breaks_monotone_keeps_corr_positive(self):
        # Hold beats Overweight — strict monotonicity fails but the overall
        # ordering is still mostly correct, so Spearman should be positive.
        by_rating = {
            "Buy":         {"mean_a5": 0.05, "n5": 10},
            "Overweight":  {"mean_a5": 0.01, "n5": 20},
            "Hold":        {"mean_a5": 0.03, "n5": 30},
            "Underweight": {"mean_a5": -0.01, "n5": 10},
            "Sell":        {"mean_a5": -0.03, "n5": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5])
        assert out[5]["monotone"] is False
        assert out[5]["spearman"] is not None
        assert 0 < out[5]["spearman"] < 1

    def test_reversed_ordering_yields_negative_spearman(self):
        by_rating = {
            "Buy":  {"mean_a5": -0.03, "n5": 10},
            "Hold": {"mean_a5": 0.0, "n5": 30},
            "Sell": {"mean_a5": 0.05, "n5": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5])
        assert out[5]["spearman"] == pytest.approx(-1.0)
        assert out[5]["monotone"] is False

    def test_too_few_buckets_returns_none(self):
        by_rating = {
            "Buy":  {"mean_a5": 0.05, "n5": 10},
            "Sell": {"mean_a5": -0.03, "n5": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5])
        assert out[5]["spearman"] is None
        assert out[5]["monotone"] is None
        assert out[5]["n_buckets"] == 2

    def test_missing_mean_or_zero_n_excluded(self):
        by_rating = {
            "Buy":         {"mean_a5": None, "n5": 10},     # mean missing → drop
            "Overweight":  {"mean_a5": 0.02, "n5": 0},      # n=0 → drop
            "Hold":        {"mean_a5": 0.00, "n5": 30},
            "Underweight": {"mean_a5": -0.01, "n5": 10},
            "Sell":        {"mean_a5": -0.03, "n5": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5])
        # Only Hold/Underweight/Sell survive — exactly 3 buckets → Spearman defined.
        assert out[5]["n_buckets"] == 3
        assert out[5]["spearman"] == pytest.approx(1.0)
        assert out[5]["monotone"] is True

    def test_multiple_windows_independent(self):
        by_rating = {
            "Buy":  {"mean_a5": 0.05, "n5": 10, "mean_a20": -0.02, "n20": 10},
            "Hold": {"mean_a5": 0.00, "n5": 30, "mean_a20": 0.00, "n20": 30},
            "Sell": {"mean_a5": -0.03, "n5": 5, "mean_a20": 0.04, "n20": 5},
        }
        out = ps._compute_rating_calibration(by_rating, [5, 20])
        assert out[5]["spearman"] == pytest.approx(1.0)
        assert out[20]["spearman"] == pytest.approx(-1.0)  # signal flips at 20d


# --------------------------------------------------------------------------
# _compute_portfolio_risk — σ, Sharpe, Sortino, drawdown over a frozen-weight
# return series. We inject a deterministic DataFrame so expected numerics can
# be derived in closed form.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestPortfolioRisk:
    @staticmethod
    def _make_rets(values: dict[str, list[float]]) -> pd.DataFrame:
        n = max(len(v) for v in values.values())
        idx = pd.date_range("2025-01-02", periods=n, freq="B")
        # Right-pad shorter series with NaN so columns can have different
        # effective lengths (used to test the "skip too-short" branch).
        padded = {k: (v + [float("nan")] * (n - len(v))) for k, v in values.items()}
        return pd.DataFrame(padded, index=idx)

    def test_alternating_returns_yields_expected_sigma(self, monkeypatch):
        # ±1% alternating: mean 0, daily sigma exactly 0.01 (with ddof=1 over
        # an even-length window). Annualized ≈ 0.01 × sqrt(252).
        rets = self._make_rets({
            "AAA": [0.01, -0.01] * 50,
            "SPY": [0.005, -0.005] * 50,
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        out = ps._compute_portfolio_risk({"AAA": 1.0})
        assert out["sigma_annual"] == pytest.approx(0.01 * math.sqrt(252), rel=0.02)
        # μ ≈ 0 → Sharpe ≈ -rf/σ (negative because rf > 0).
        assert out["sharpe"] is not None
        assert out["sharpe"] < 0
        assert out["lookback_days"] == 100
        # spy_sharpe also negative (μ≈0, σ>0).
        assert out["spy_sharpe"] is not None and out["spy_sharpe"] < 0

    def test_constant_positive_returns_undefined_sharpe(self, monkeypatch):
        # σ = 0 → Sharpe/Sortino undefined; drawdown stays at 0.
        rets = self._make_rets({
            "AAA": [0.001] * 100,
            "SPY": [0.0005] * 100,
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        out = ps._compute_portfolio_risk({"AAA": 1.0})
        assert out["sigma_annual"] == pytest.approx(0.0, abs=1e-9)
        assert out["sharpe"] is None
        assert out["sortino"] is None
        assert out["max_drawdown"] == pytest.approx(0.0)
        assert out["current_drawdown"] == pytest.approx(0.0)

    def test_drawdown_recorded_on_decline(self, monkeypatch):
        # 30 days of -2% returns. Peak is day-1 cum equity (0.98); trough is
        # day-30 (0.98^30). Drawdown convention follows empyrical: relative to
        # the peak *within* the observed series — no implicit 1.0 anchor.
        rets = self._make_rets({
            "AAA": [-0.02] * 30,
            "SPY": [0.0] * 30,
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        out = ps._compute_portfolio_risk({"AAA": 1.0})
        # cum[0]=0.98, cum[29]=0.98^30 → dd = 0.98^30/0.98 − 1 = 0.98^29 − 1.
        expected_dd = 0.98 ** 29 - 1
        assert out["max_drawdown"] == pytest.approx(expected_dd, rel=0.01)
        assert out["current_drawdown"] == pytest.approx(expected_dd, rel=0.01)

    def test_renormalizes_when_one_symbol_missing(self, monkeypatch):
        # BBB requested but not in the returns matrix → weights renormalize
        # onto AAA only. Result should equal AAA-only computation.
        rets = self._make_rets({
            "AAA": [0.01, -0.01] * 50,
            "SPY": [0.005, -0.005] * 50,
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        full = ps._compute_portfolio_risk({"AAA": 1.0})
        partial = ps._compute_portfolio_risk({"AAA": 0.5, "BBB": 0.5})
        assert partial["sigma_annual"] == pytest.approx(full["sigma_annual"])
        assert partial["coverage_syms"] == 1
        assert partial["total_syms"] == 2

    def test_returns_empty_on_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: None)
        assert ps._compute_portfolio_risk({"AAA": 1.0}) == {}

    def test_returns_empty_on_no_weights(self):
        assert ps._compute_portfolio_risk({}) == {}
        assert ps._compute_portfolio_risk({"AAA": 0}) == {}


# --------------------------------------------------------------------------
# _compute_vol_targets — per-symbol annualized vol + dollar size at 1% risk
# budget. Half-vol → double target $.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestVolTargets:
    @staticmethod
    def _make_rets(values: dict[str, list[float]]) -> pd.DataFrame:
        n = max(len(v) for v in values.values())
        idx = pd.date_range("2025-01-02", periods=n, freq="B")
        # Right-pad shorter series with NaN so columns can have different
        # effective lengths (used to test the "skip too-short" branch).
        padded = {k: (v + [float("nan")] * (n - len(v))) for k, v in values.items()}
        return pd.DataFrame(padded, index=idx)

    def test_higher_vol_yields_smaller_dollar_target(self, monkeypatch):
        rets = self._make_rets({
            "HI": [0.02, -0.02] * 40,    # ~2% daily → ~31% annualized
            "LO": [0.005, -0.005] * 40,  # ~0.5% daily → ~8% annualized
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        out = ps._compute_vol_targets(["HI", "LO"], 1_000_000)
        assert set(out.keys()) == {"HI", "LO"}
        # Vol ratio = 4 → target $ ratio ≈ 1/4.
        assert out["LO"]["vol_target_dollars"] == pytest.approx(
            out["HI"]["vol_target_dollars"] * 4, rel=0.05
        )
        # 1% portfolio budget → product vol_annual × target_$ ≈ 10,000.
        expected_budget = 1_000_000 * (ps.RISK_BUDGET_PER_POSITION_PCT / 100.0)
        for sym in ("HI", "LO"):
            product = out[sym]["vol_annual"] * out[sym]["vol_target_dollars"]
            assert product == pytest.approx(expected_budget, rel=0.01)

    def test_returns_empty_on_zero_total(self, monkeypatch):
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: self._make_rets({"AAA": [0.01] * 30}))
        assert ps._compute_vol_targets(["AAA"], 0) == {}

    def test_returns_empty_on_empty_symbols(self):
        assert ps._compute_vol_targets([], 100_000) == {}

    def test_returns_empty_on_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: None)
        assert ps._compute_vol_targets(["AAA"], 100_000) == {}

    def test_too_short_series_skipped(self, monkeypatch):
        # <20 days of data → skip symbol entirely (noisy vol estimate).
        rets = self._make_rets({
            "SHORT": [0.01] * 10,
            "OK":    [0.01, -0.01] * 30,
        })
        monkeypatch.setattr(ps, "_fetch_returns_matrix", lambda s, days=252: rets)
        out = ps._compute_vol_targets(["SHORT", "OK"], 1_000_000)
        assert "SHORT" not in out
        assert "OK" in out


# --------------------------------------------------------------------------
# RSI / MACD / ATR — pure pandas indicators for /tech.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestIndicators:
    def test_rsi_pure_uptrend_pegs_at_100(self):
        # No losses → avg_loss=0 → guard returns 100 (instead of inf).
        close = pd.Series([100 + i for i in range(30)])
        assert ps._compute_rsi(close) == pytest.approx(100.0)

    def test_rsi_pure_downtrend_pegs_at_zero(self):
        close = pd.Series([100 - i for i in range(30)])
        rsi = ps._compute_rsi(close)
        assert rsi is not None
        assert rsi == pytest.approx(0.0, abs=0.01)

    def test_rsi_returns_none_when_too_short(self):
        assert ps._compute_rsi(pd.Series([100, 101, 102])) is None

    def test_macd_hist_positive_in_uptrend(self):
        close = pd.Series([100 + i * 0.5 for i in range(50)])
        out = ps._compute_macd(close)
        assert out is not None
        assert out["hist"] > 0
        assert out["macd"] > out["signal"]

    def test_macd_returns_none_when_too_short(self):
        assert ps._compute_macd(pd.Series([100] * 20)) is None

    def test_atr_zero_when_flat(self):
        flat = pd.Series([100.0] * 30)
        assert ps._compute_atr(flat, flat, flat) == pytest.approx(0.0)

    def test_atr_increases_with_range(self):
        close = pd.Series([100.0] * 30)
        narrow = ps._compute_atr(close * 1.01, close * 0.99, close)
        wide = ps._compute_atr(close * 1.05, close * 0.95, close)
        assert wide > narrow > 0


# --------------------------------------------------------------------------
# _compute_concentration_breaches — DB-backed; we patch connect() to a
# fixture so the test stays hermetic.
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestConcentrationBreaches:
    @staticmethod
    def _stub_connect(rows: list[dict]):
        """Return a context-manager factory whose .execute(...).fetchall()
        yields ``rows`` — matches the sqlite3 row-as-dict interface our code
        consumes via __getitem__."""
        class _Cursor:
            def fetchall(self):
                return rows

        class _Conn:
            def execute(self, *_args, **_kw):
                return _Cursor()
            def __enter__(self): return self
            def __exit__(self, *_): return False

        return lambda: _Conn()

    def test_ticker_breach_only_when_over_cap(self, monkeypatch):
        rows = [
            {"symbol": "BIG",    "account_id": "a1", "account_name": "A", "owner": "Self", "value": 30_000, "sector": "Tech"},
            {"symbol": "SMALL1", "account_id": "a1", "account_name": "A", "owner": "Self", "value": 5_000,  "sector": "Tech"},
            {"symbol": "SMALL2", "account_id": "a1", "account_name": "A", "owner": "Self", "value": 5_000,  "sector": "Health"},
        ]
        # Total 40k. BIG = 75% (breach @10), SMALLs = 12.5% each (breach @10).
        monkeypatch.setattr(ps, "connect", self._stub_connect(rows))
        out = ps._compute_concentration_breaches()
        names = {b["name"] for b in out["ticker"]}
        assert names == {"BIG", "SMALL1", "SMALL2"}
        # Sorted by overshoot desc.
        assert out["ticker"][0]["name"] == "BIG"
        assert out["ticker"][0]["overshoot"] == pytest.approx(65.0, abs=0.1)

    def test_sector_breach_aggregates_across_tickers(self, monkeypatch):
        # Tech sector aggregates BIG + SMALL1 = 35k = 87.5% > 35% cap.
        rows = [
            {"symbol": "BIG",    "account_id": "a1", "account_name": "A", "owner": "Self", "value": 30_000, "sector": "Tech"},
            {"symbol": "SMALL1", "account_id": "a1", "account_name": "A", "owner": "Self", "value": 5_000,  "sector": "Tech"},
            {"symbol": "DEF",    "account_id": "a1", "account_name": "A", "owner": "Self", "value": 5_000,  "sector": "Defense"},
        ]
        monkeypatch.setattr(ps, "connect", self._stub_connect(rows))
        out = ps._compute_concentration_breaches()
        assert len(out["sector"]) == 1
        assert out["sector"][0]["name"] == "Tech"
        assert out["sector"][0]["pct"] == pytest.approx(87.5)

    def test_empty_snapshot_returns_empty_buckets(self, monkeypatch):
        monkeypatch.setattr(ps, "connect", self._stub_connect([]))
        out = ps._compute_concentration_breaches()
        assert out == {"ticker": [], "sector": [], "owner": [], "total": 0}

    def test_owner_breach_when_one_owner_holds_most(self, monkeypatch):
        rows = [
            {"symbol": "A", "account_id": "a1", "account_name": "Self acct",   "owner": "Self",   "value": 85_000, "sector": "Tech"},
            {"symbol": "B", "account_id": "a2", "account_name": "Spouse acct", "owner": "Spouse", "value": 15_000, "sector": "Tech"},
        ]
        monkeypatch.setattr(ps, "connect", self._stub_connect(rows))
        out = ps._compute_concentration_breaches()
        # Tech sector = 100% > 35% → also breaches sector.
        assert out["owner"][0]["name"] == "Self"
        assert out["owner"][0]["pct"] == pytest.approx(85.0)
        assert any(b["name"] == "Tech" for b in out["sector"])


# --------------------------------------------------------------------------
# _fetch_avg_dollar_volume — guard rails only (deterministic content path
# would require mocking the full yfinance multi-index DataFrame which is
# more brittle than the math it tests).
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchAvgDollarVolume:
    def test_empty_symbols_returns_empty(self):
        assert ps._fetch_avg_dollar_volume([], days=20) == {}

    def test_yfinance_exception_returns_empty(self, monkeypatch):
        # Force the internal yfinance.download call to blow up — function
        # should swallow it and return {} rather than propagate.
        import yfinance as _yf

        def _boom(*_a, **_kw):
            raise RuntimeError("yfinance offline")

        monkeypatch.setattr(_yf, "download", _boom)
        # Bust the hourly cache so we actually re-enter the fetch path.
        ps._ADV_CACHE["key"] = None
        ps._ADV_CACHE["data"] = None
        assert ps._fetch_avg_dollar_volume(["AAPL"], days=20) == {}
