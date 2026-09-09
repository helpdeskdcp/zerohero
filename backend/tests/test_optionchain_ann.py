"""
app/optionchain/ ANN confirmation layer (Layer 4) -- offline.

  * oc_feat        -- deterministic, bounded feature vector
  * OcAnnConfirm   -- INSUFFICIENT never filters; OK picks a TRAIN threshold
  * walk_forward   -- purged, TRAIN/OOS session-disjoint
  * _verdict       -- INSUFFICIENT_SAMPLE gate; base non-positive -> NO-GO (ANN
                      cannot rescue); GO only on a real pooled + per-fold lift
  * build_records  -- replay plumbing on a synthetic market_history.db
No network, no live-app import.
"""
import random
import sqlite3

import pytest

from app.optionchain import ann_confirm as AC
from app.optionchain import ann_backtest as BT
from app.optionchain.structure import OptionStructureState


def _state(**over):
    base = dict(
        underlying="NIFTY", expiry="15SEP2026", ts="2026-09-10T06:30:00Z",
        spot=23450.0, atm_strike=23450.0, source="unit",
        quality={"dqs": 80.0, "verdict": "PASS"},
        capability={"structure_blocks_ok": 5},
        max_pain={"status": "ok", "distance_pct": 0.9, "magnet_pull": "MODERATE"},
        pcr_regime={"status": "ok", "pcr_oi": 1.3, "pcr_vol": 1.1},
        oi_walls={"status": "ok", "dist_res_pct": 1.4, "dist_sup_pct": -1.1, "boxed": False},
        iv_skew_bias={"status": "ok", "skew_slope": 0.2, "rr_25": -0.01, "atm_iv": 0.12},
        iv_vs_realized={"status": "ok", "state": "FAIR"},
        gex_regime={"status": "ok", "regime_sign": 1, "spot_vs_flip": "ABOVE"},
        expiry_context={"phase": "NORMAL", "dte": 5},
        notes=[], summary="")
    base.update(over)
    return OptionStructureState(**base)


# --------------------------------------------------------------------------- #
#  oc_feat                                                                     #
# --------------------------------------------------------------------------- #
def test_oc_feat_is_deterministic_and_bounded():
    st = _state()
    a = AC.oc_feat(st, "LONG", st.ts)
    b = AC.oc_feat(st.to_dict(), "LONG", st.ts)
    assert a == b                                        # dataclass or dict, same result
    assert a["long"] == 1.0 and AC.oc_feat(st, "SHORT", st.ts)["long"] == 0.0
    for k, v in a.items():
        assert isinstance(v, float) and -1.0001 <= v <= 3.0001, (k, v)
    # a wildly out-of-range input is clipped, not propagated
    st2 = _state(iv_skew_bias={"status": "ok", "skew_slope": 999.0, "rr_25": 9.0, "atm_iv": 9.0})
    f2 = AC.oc_feat(st2, "LONG", st2.ts)
    assert f2["skew_slope"] <= 3.0 and f2["rr25"] <= 3.0 and f2["atm_iv"] <= 3.0


# --------------------------------------------------------------------------- #
#  OcAnnConfirm                                                                #
# --------------------------------------------------------------------------- #
def _recs(n, win_rate, seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        w = rng.random() < win_rate
        out.append({"feat": {"bias": 1.0, "long": 1.0, "x": rng.random()},
                    "win": w, "r_multiple": 1.0 if w else -1.0, "direction": "LONG"})
    return out


def test_ann_insufficient_never_filters():
    ann = AC.OcAnnConfirm({"min_train": 100}).fit(_recs(30, 0.5))
    assert ann.status == "INSUFFICIENT"
    assert ann.p_win({"feat": {"bias": 1.0}}) == 1.0
    kept = ann.filter(_recs(10, 0.5))
    assert len(kept) == 10 and all(r["ann_p_win"] == 1.0 for r in kept)   # keeps everything


def test_ann_fits_and_picks_a_threshold():
    ann = AC.OcAnnConfirm({"min_train": 50, "epochs": 15}).fit(_recs(300, 0.55))
    assert ann.status == "OK" and ann.model is not None
    assert ann.threshold in AC.DEFAULT_ANN_CFG["threshold_grid"] or \
        ann.threshold == AC.DEFAULT_ANN_CFG["threshold_fixed"]
    p = ann.p_win({"feat": {"bias": 1.0, "long": 1.0, "x": 0.5}})
    assert 0.0 <= p <= 1.0


# --------------------------------------------------------------------------- #
#  walk_forward + verdict                                                      #
# --------------------------------------------------------------------------- #
def _sessioned(per_session, sessions, win_rate, seed=2):
    rng = random.Random(seed)
    out = []
    for si, s in enumerate(sessions):
        for i in range(per_session):
            w = rng.random() < win_rate
            out.append({"ts": f"{s}T0{4 + i % 5}:{10 + i % 40:02d}:00Z", "session": s,
                        "direction": "LONG", "feat": {"bias": 1.0, "long": 1.0, "x": rng.random()},
                        "win": w, "r_multiple": 1.0 if w else -1.0})
    return out


def test_walk_forward_folds_are_session_disjoint_and_purged():
    sess = [f"2026-09-{d:02d}" for d in range(1, 8)]
    recs = _sessioned(60, sess, 0.5)
    wf = BT.walk_forward(recs, {**BT.DEFAULT_CFG})
    assert len(wf["folds"]) == BT.DEFAULT_CFG["wf_folds"]
    for f in wf["folds"]:
        assert f["A"]["n"] == 60                          # OOS = exactly one session
        assert f["n_train"] > 0


def test_verdict_insufficient_sample():
    sess = ["2026-09-01", "2026-09-02", "2026-09-03"]
    recs = _sessioned(20, sess, 0.55)                     # ~40 pooled OOS << min_oos
    res = BT.run.__wrapped__ if hasattr(BT.run, "__wrapped__") else None
    wf = BT.walk_forward(recs, BT.DEFAULT_CFG)
    v = BT._verdict(wf, BT.DEFAULT_CFG)
    assert v["verdict"] == "INSUFFICIENT_SAMPLE"


def test_verdict_no_go_when_base_is_non_positive():
    # plenty of sample, but structure-only OOS expectancy <= 0 -> ANN can't rescue
    cfg = {**BT.DEFAULT_CFG, "min_oos": 50, "min_folds": 2, "min_fold_oos": 10,
           "ann": {**AC.DEFAULT_ANN_CFG, "min_train": 30}}
    sess = [f"2026-09-{d:02d}" for d in range(1, 7)]
    recs = _sessioned(120, sess, 0.45)                    # < 50% win -> expectancy < 0
    v = BT._verdict(BT.walk_forward(recs, cfg), cfg)
    assert v["verdict"] == "NO-GO" and "non-positive base" in v["reasons"][0]


def test_verdict_go_requires_real_lift(monkeypatch):
    """B is only GO if it beats A on pooled + folds + keep-fraction + win%."""
    cfg = {**BT.DEFAULT_CFG, "min_oos": 50, "min_folds": 2, "min_fold_oos": 10,
           "min_keep_frac": 0.3, "min_lift_R": 0.05,
           "ann": {**AC.DEFAULT_ANN_CFG, "min_train": 30}}
    sess = [f"2026-09-{d:02d}" for d in range(1, 7)]
    recs = _sessioned(120, sess, 0.56)

    # force the ANN to keep only the winners -> B strictly dominates A
    class _Perfect(AC.OcAnnConfirm):
        def fit(self, train):
            self.status, self.n_train, self.train_base, self.threshold = "OK", len(train), 0.56, 0.5
            return self
        def p_win(self, rec):
            return 0.9 if rec["win"] else 0.1
    monkeypatch.setattr(BT, "OcAnnConfirm", _Perfect)
    v = BT._verdict(BT.walk_forward(recs, cfg), cfg)
    assert v["verdict"] == "GO"
    assert all(c["pass"] for c in v["checks"].values())


# --------------------------------------------------------------------------- #
#  build_records replay plumbing (tiny synthetic capture DB)                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def replay_db(tmp_path):
    p = tmp_path / "mh.db"
    con = sqlite3.connect(p)
    con.executescript("""
      CREATE TABLE quote_snapshots(id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, expiry TEXT,
        strike REAL, option_type TEXT, ltp REAL, oi REAL, oi_change REAL, volume REAL,
        session_date_ist TEXT, received_ts TEXT);
      CREATE TABLE option_greeks(id INTEGER PRIMARY KEY, received_ts TEXT, snap_key TEXT,
        underlying TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT,
        delta REAL, gamma REAL, theta REAL, vega REAL, iv REAL, iv_pct REAL, trade_volume REAL);
    """)
    exp = "15SEP2026"
    for si, day in enumerate(("2026-09-08", "2026-09-09", "2026-09-10")):
        base_spot = 23000 + si * 50
        for m in range(0, 90, 3):                          # a snap every 3 min, 30/session
            hh, mm = 4 + m // 60, m % 60
            rts = f"{day}T{hh:02d}:{mm:02d}:00Z"
            sp = base_spot + m * 1.5                       # drifting up -> LONG bias, wins
            con.execute("INSERT INTO quote_snapshots(symbol,kind,ltp,session_date_ist,received_ts)"
                        " VALUES('NIFTY','INDEX',?,?,?)", (sp, day, rts))
            snap = f"{day}T{hh:02d}:{mm:02d}:00"
            for k in range(22800, 23401, 50):
                for ot, dl in (("CE", 0.5), ("PE", -0.5)):
                    con.execute("INSERT INTO option_greeks(received_ts,snap_key,underlying,expiry,"
                                "strike,option_type,session_date_ist,delta,gamma,theta,vega,iv,"
                                "iv_pct,trade_volume) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (rts, snap, "NIFTY", exp, float(k), ot, day, dl, 0.001, -3.0,
                                 4.0, 0.12, 12.0, 5000.0))
                    con.execute("INSERT INTO quote_snapshots(symbol,kind,expiry,strike,option_type,"
                                "ltp,oi,oi_change,volume,session_date_ist,received_ts) VALUES("
                                "'NIFTY','OPTION',?,?,?,?,?,?,?,?,?)",
                                (exp, float(k), ot, 100.0, 1000.0 + (23000 - k) * (1 if ot == "PE" else -1),
                                 50.0, 9000.0, day, rts))
    con.commit()
    con.close()
    return str(p)


def test_build_records_and_run_smoke(replay_db):
    cfg = {**BT.DEFAULT_CFG, "horizon_min": 20, "move_pct": 0.10, "min_dqs": 0.0}
    recs = BT.build_records("NIFTY", cfg, db_path=replay_db)
    assert isinstance(recs, list)
    if recs:
        r = recs[0]
        assert set(r) >= {"ts", "session", "direction", "feat", "win", "r_multiple"}
        assert r["direction"] in ("LONG", "SHORT")
    res = BT.run("NIFTY", cfg=cfg, db_path=replay_db)
    assert res["verdict"]["verdict"] in ("GO", "NO-GO", "INSUFFICIENT_SAMPLE")
    import json
    json.dumps(res, default=str)
