"""
EPM (Expected Premium Move) is surfaced in decide_from_context output.

`option_engine._translation` computes `expected_premium_move` for the selected
leg; this test pins that the value reaches the BUY signal dict (and the WATCH
fallback) as an advisory field, and that it does NOT alter the entry / SL /
targets or the decision itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines import scalp_strategy as ss  # noqa: E402


def _stub_engines(monkeypatch, *, epm=12.5, method="greeks"):
    monkeypatch.setattr(ss, "compute_sr", lambda *a, **k: {
        "status": "OK", "price": 100.0, "atr": 2.0,
        "support": {"level": 96.0, "strength": 0.6},
        "resistance": {"level": 108.0, "strength": 0.6}})
    monkeypatch.setattr(ss, "detect_regime", lambda *a, **k: {
        "regime": "TRENDING_UP", "confidence": 0.6})
    monkeypatch.setattr(ss, "mtf_alignment", lambda *a, **k: {
        "alignment": 0.5, "magnitude": 40.0, "conflict": False, "htf_dominant": False})
    monkeypatch.setattr(ss, "classify", lambda *a, **k: {
        "state": "SUPPORT_REVERSAL", "direction": "BULLISH", "state_score": 72.0,
        "anchor": {"level": 96.0, "side": "SUPPORT"}, "components": {},
        "false_risk": {"verdict": "CLEAN", "score": 100.0}, "reason": ["stub"],
        "roc_pct": 0.2})
    # one candidate leg + selection, carrying a known translation/EPM
    leg = {"opt_type": "CE", "ltp": 90.0, "strike": 100.0, "token": "T1",
           "tradingsymbol": "X", "expiry": "2026-09-30",
           "sr": {"support": 80.0, "resistance": 120.0, "atr": 6.0},
           "translation_score": 0.7,
           "translation": {"method": method, "expected_premium_move": epm},
           "quality_score": 80.0}
    monkeypatch.setattr(ss, "analyse_leg", lambda *a, **k: dict(leg))
    monkeypatch.setattr(ss, "select_option",
                        lambda *a, **k: {**leg, "final_quality": 80.0, "atm_proximity": 0.9})
    monkeypatch.setattr(ss, "_plan_from_leg", lambda *a, **k: {
        "entry": 90.0, "stop_loss": 78.0, "target_1": 114.0, "target_2": 138.0,
        "trailing_stop": 5.0, "max_hold_sec": 1500})


def _bars():
    return {"5m": [[0, 100, 101, 99, 100, 1000]] * 30}


def test_buy_signal_carries_expected_premium_move(monkeypatch):
    _stub_engines(monkeypatch, epm=12.5, method="greeks")
    d = ss.decide_from_context(_bars(), [{"strike": 100.0, "ce": {"ltp": 90.0}}],
                               atm=100.0, calib=None, config={"filters": {}})
    assert d["decision"] in ("BUY_CE", "BUY_PE"), d.get("reason")
    assert d["expected_premium_move"] == 12.5
    assert d["epm_method"] == "greeks"
    assert "epm_index_move_pts" in d
    assert d["translation_score"] == 0.7
    # advisory only: the plan is untouched
    assert d["entry"] == 90.0 and d["stop_loss"] == 78.0 and d["target_1"] == 114.0


def test_epm_absent_is_none_not_zero(monkeypatch):
    _stub_engines(monkeypatch, epm=None, method="fallback")
    d = ss.decide_from_context(_bars(), [{"strike": 100.0, "ce": {"ltp": 90.0}}],
                               atm=100.0, calib=None, config={"filters": {}})
    assert d["decision"] in ("BUY_CE", "BUY_PE", "WATCH")
    assert d["expected_premium_move"] is None          # never guessed / zero-filled
    assert d["epm_method"] == "fallback"


def test_epm_does_not_change_the_decision(monkeypatch):
    """Same setup, EPM 5 vs EPM 50 -> identical decision / entry / probability."""
    outs = []
    for epm in (5.0, 50.0):
        _stub_engines(monkeypatch, epm=epm)
        d = ss.decide_from_context(_bars(), [{"strike": 100.0, "ce": {"ltp": 90.0}}],
                                   atm=100.0, calib=None, config={"filters": {}})
        outs.append((d["decision"], d["entry"], d["stop_loss"], d["target_1"],
                     d["probability"], d["ev_r"]))
    assert outs[0] == outs[1]


def test_epm_persists_to_scalp_signals_and_snapshots(fresh_db):
    """The new nullable columns exist on a fresh DB and round-trip a value."""
    with fresh_db.db() as conn:
        for t in ("scalp_signals", "live_market_snapshots"):
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
            assert {"expected_premium_move", "epm_method"} <= cols, (t, sorted(cols))

    fresh_db.insert_scalp_signal({
        "signal_id": "EPM-RT-1", "source": "LIVE", "created_ts": "2026-09-08T00:00:00Z",
        "session_date": "2026-09-08", "symbol": "CRUDEOIL", "decision": "BUY_PE",
        "expected_premium_move": 9.37, "epm_method": "greeks"})
    row = fresh_db.get_scalp_signal("EPM-RT-1")
    assert row["expected_premium_move"] == 9.37 and row["epm_method"] == "greeks"

    fresh_db.insert_live_snapshot({
        "ts": "2026-09-08T00:00:00Z", "session_date": "2026-09-08", "symbol": "CRUDEOIL",
        "decision": "BUY_PE", "expected_premium_move": 4.1, "epm_method": "fallback"})
    snap = fresh_db.list_live_snapshots(symbol="CRUDEOIL", limit=1)[0]
    assert snap["expected_premium_move"] == 4.1 and snap["epm_method"] == "fallback"


def test_epm_migrates_onto_a_preexisting_db():
    """A DB created before EPM gets the columns via _migrate (ALTER TABLE ADD)."""
    import sqlite3
    from app import db as _db
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE scalp_signals (id INTEGER PRIMARY KEY, signal_id TEXT, ev_r REAL)")
    c.execute("CREATE TABLE live_market_snapshots (id INTEGER PRIMARY KEY, ts TEXT, ev REAL)")
    c.execute("CREATE TABLE ai_paper_trades (id INTEGER PRIMARY KEY)")
    _db._migrate(c)
    for t in ("scalp_signals", "live_market_snapshots"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        assert {"expected_premium_move", "epm_method"} <= cols, (t, sorted(cols))
