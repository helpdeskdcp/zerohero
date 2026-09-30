"""app.market_snapshot.get_regime -- wires the existing, already-live
app.engines.regime_mtf.detect_regime() to market_hub.session_bars() as a
standalone, symbol-agnostic read. No new classifier, no network."""
from app import market_snapshot as MS


def _trending_bars(n=30, start=100.0, step=0.6):
    """A clean uptrend: n bars, close rising by `step` each bar."""
    bars = []
    price = start
    for i in range(n):
        o = price
        c = price + step
        h = c + 0.1
        l = o - 0.1
        bars.append({"bar_start": f"2026-09-30T09:{15 + i:02d}:00Z", "o": o, "h": h, "l": l, "c": c, "v": 1000})
        price = c
    return bars


def test_get_regime_returns_a_real_classification_from_trending_bars(monkeypatch):
    monkeypatch.setattr(MS.market_hub, "_now_ist_date", lambda: "2026-09-30")
    monkeypatch.setattr(MS.market_hub, "session_bars",
                        lambda sym, sess, tf="5m": _trending_bars() if tf in ("3m", "5m") else [])
    out = MS.get_regime("NIFTY")
    assert out["underlying"] == "NIFTY" and out["session_date"] == "2026-09-30"
    assert out["regime"]["regime"] in (
        "TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY", "LOW_VOLATILITY",
        "BREAKOUT_REGIME", "REVERSAL_REGIME", "UNSTABLE")
    assert "mtf_alignment" in out and "alignment" in out["mtf_alignment"]
    assert out["bars_available"]["5m"] == 30 and out["bars_available"]["1m"] == 0


def test_get_regime_is_unstable_with_too_few_bars(monkeypatch):
    monkeypatch.setattr(MS.market_hub, "_now_ist_date", lambda: "2026-09-30")
    monkeypatch.setattr(MS.market_hub, "session_bars", lambda sym, sess, tf="5m": [])
    out = MS.get_regime("NIFTY")
    assert out["regime"]["regime"] == "UNSTABLE"


def test_get_regime_never_raises_when_session_bars_errors(monkeypatch):
    monkeypatch.setattr(MS.market_hub, "_now_ist_date", lambda: "2026-09-30")

    def _boom(sym, sess, tf="5m"):
        raise RuntimeError("db locked")
    monkeypatch.setattr(MS.market_hub, "session_bars", _boom)
    out = MS.get_regime("NIFTY")
    assert out["regime"]["regime"] == "UNSTABLE"     # fails closed, never raises


def test_get_regime_accepts_an_explicit_session_date(monkeypatch):
    seen = []
    monkeypatch.setattr(MS.market_hub, "session_bars",
                        lambda sym, sess, tf="5m": seen.append(sess) or [])
    MS.get_regime("NIFTY", session_date="2026-08-15")
    assert all(s == "2026-08-15" for s in seen)
