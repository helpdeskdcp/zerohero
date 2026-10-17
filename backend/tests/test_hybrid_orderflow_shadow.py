import sqlite3

import pytest

from app.hybrid_orderflow import shadow as SH
from app.hybrid_orderflow.bar_eval import BAR, IST
from app.hybrid_orderflow.policy import load_policy
from tests.test_hybrid_bar_eval import T0, ticks

P = load_policy()
META = {"570750": {"symbol": "NATURALGAS", "kind": "FUTURE", "tick_size": 0.1}, "1": {"symbol": "NIFTY", "kind": "OPTION_CE", "tick_size": 0.05},
        "2": {"symbol": "RELIANCE", "kind": "FUTURE", "tick_size": 0.05}}


def make_tick_db(path, tk, sid=570750, crossed_every=0):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE classified_ticks(id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, security_id INTEGER, symbol TEXT, kind TEXT, ltp REAL, ltq INTEGER,"
              " ltt INTEGER, side TEXT, classification_method TEXT, best_bid REAL, best_ask REAL, cvd INTEGER)")
    for i, (ltt, ltp, ltq, side, meth) in enumerate(tk):
        bb, ba = (ltp + 1, ltp) if crossed_every and i % crossed_every == 0 else (ltp - 0.1, ltp + 0.1)
        c.execute("INSERT INTO classified_ticks(received_ts,security_id,ltp,ltq,ltt,side,classification_method,best_bid,best_ask) VALUES('x',?,?,?,?,?,?,?,?)",
                  (sid, ltp, ltq, ltt, side, meth, bb, ba))
    c.commit()
    c.close()


def runner(tmp_path, tk, **kw):
    tdb = str(tmp_path / "ticks.db")
    make_tick_db(tdb, tk, **kw)
    return SH.ShadowRunner(lambda: META, tick_db=tdb, store=SH.ShadowStore(str(tmp_path / "shadow.db")), policy=P)


def test_tracks_only_policy_futures(tmp_path):
    r = runner(tmp_path, ticks(1, 40))
    assert r.instruments() == {570750: ("NATURALGAS", 0.1)}                # options and unsupported symbols are ignored


def test_run_once_scores_and_persists_bars(tmp_path):
    tk = ticks(1, 60)
    r = runner(tmp_path, tk)
    out = r.run_once(now=tk[-1][0] + 10_000)
    assert out["fed"] == len(tk) and out["closed"] == 60
    c = r.store.counts()
    assert sum(c["scored_by_status"].values()) > 0 and c["scored_days"] == 1
    with sqlite3.connect(r.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 60


def test_restart_rebuilds_the_session_idempotently(tmp_path):
    tk = ticks(1, 60)
    r1 = runner(tmp_path, tk)
    r1.run_once(now=tk[-1][0] + 10_000)
    before = r1.store.counts()
    r2 = SH.ShadowRunner(lambda: META, tick_db=r1.tick_db, store=r1.store, policy=P)             # "restart": evaluators are empty again
    r2.run_once(now=tk[-1][0] + 10_000)
    assert r2.store.counts() == before


def test_incremental_ticks_do_not_duplicate_or_skip(tmp_path):
    tk = ticks(1, 60)
    half = len(tk) // 2
    tdb = str(tmp_path / "ticks.db")
    make_tick_db(tdb, tk[:half])
    r = SH.ShadowRunner(lambda: META, tick_db=tdb, store=SH.ShadowStore(str(tmp_path / "s.db")), policy=P)
    r.run_once(now=tk[half][0])
    c = sqlite3.connect(tdb)
    for ltt, ltp, ltq, side, meth in tk[half:]:
        c.execute("INSERT INTO classified_ticks(received_ts,security_id,ltp,ltq,ltt,side,classification_method,best_bid,best_ask) VALUES('x',570750,?,?,?,?,?,?,?)",
                  (ltp, ltq, ltt, side, meth, ltp - 0.1, ltp + 0.1))
    c.commit(); c.close()
    r.run_once(now=tk[-1][0] + 10_000)
    ref = runner(tmp_path / "ref" if (tmp_path / "ref").mkdir() is None else tmp_path, tk)
    ref.run_once(now=tk[-1][0] + 10_000)
    assert r.store.counts()["scored_by_status"] == ref.store.counts()["scored_by_status"]


def test_crossed_book_ticks_are_never_fed(tmp_path):
    tk = ticks(1, 40)
    r = runner(tmp_path, tk, crossed_every=5)
    out = r.run_once(now=tk[-1][0] + 10_000)
    assert out["fed"] == len(tk) - len(range(0, len(tk), 5))


def test_missing_tick_db_is_contained(tmp_path):
    r = SH.ShadowRunner(lambda: META, tick_db=str(tmp_path / "nope.db"), store=SH.ShadowStore(str(tmp_path / "s.db")), policy=P)
    out = r.run_once()
    assert out == {"fed": 0, "closed": 0} and r.last_error                   # error recorded, never raised
    assert r.status()["live_orders"] is False


def test_no_meta_yet_is_contained(tmp_path):
    r = SH.ShadowRunner(lambda: {}, tick_db=str(tmp_path / "x.db"), store=SH.ShadowStore(str(tmp_path / "s.db")), policy=P)
    assert r.run_once() == {"fed": 0, "closed": 0} and "no tracked" in r.last_error


def test_forward_outcomes_use_exact_bars_and_gate_says_insufficient(tmp_path):
    st = SH.ShadowStore(str(tmp_path / "s.db"))
    k0 = (T0 + IST) // BAR
    with sqlite3.connect(st.path) as db:
        for i in range(0, 12):
            db.execute("INSERT INTO bars VALUES('NATURALGAS',?,?,?,?,?,1)", (k0 + i, 100 + i, 101 + i, 99 + i, 100.5 + i))
        db.execute("INSERT INTO scores VALUES('NATURALGAS',?, 'SIGNAL','BULLISH',8,'[]','{}','1.2.0')", (k0,))
        db.execute("INSERT INTO scores VALUES('NATURALGAS',?, 'SIGNAL','BEARISH',8,'[]','{}','1.2.0')", (k0 + 10,))      # +1/+6 bars do not exist -> skipped
    tr = st.forward_trades(3)
    assert len(tr) == 1 and tr[0][2] == "BULLISH" and abs(tr[0][3] - ((100.5 + 3) / 101 - 1) * 1e4) < 1e-6
    rep = SH.forward_report(st, P)
    assert rep["gate"]["verdict"] == "INSUFFICIENT_SAMPLE" and rep["paper_stage_unlocked"] is False


def test_shadow_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HYBRID_ORDERFLOW_SHADOW", "0")
    assert SH.enabled() is False
    monkeypatch.delenv("HYBRID_ORDERFLOW_SHADOW")
    assert SH.enabled() is True


def test_runner_refuses_to_exist_with_an_invalid_policy(tmp_path):
    from app.hybrid_orderflow.policy import PolicyError
    bad = dict(P)
    bad["execution"] = {**P["execution"], "live_orders_enabled": True}
    with pytest.raises(PolicyError):
        from app.hybrid_orderflow.policy import validate
        validate(bad)
