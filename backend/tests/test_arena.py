import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.arena import run as R, settle as S, spread_model as SM, submit as SU
from app.forward_test import ledger as L

DAY = "2026-10-20"
EXP = "2026-10-27"


def ep(hhmm, day=DAY): return datetime.fromisoformat(f"{day}T{hhmm}:00+05:30").timestamp()


def snaps(rows): return [{"t": ep(t), "ltp": ltp, "bid": b, "ask": a} for t, ltp, b, a in rows]


def sig(**kw):
    d = {"id": "x", "participant": "P", "underlying": "NIFTY", "expiry": EXP, "strike": 23000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.0, "entry_hi": 100.0, "sl": 85.0,
         "targets": [120.0, 140.0], "t": ep("10:00")}
    d.update(kw); return d


H = 0.01


# ---------------- settlement ----------------
def test_buy_at_ask_sell_at_bid_target_and_costs():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 108, 107, 109), ("10:10", 121, 120.5, 121.5), ("10:15", 125, 124, 126)])
    r = S.settle(sig(), sn, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 101 and r["exit"] == 120.5 and r["exit_reason"] == "TARGET" and r["fill_real_quote"] and r["exit_real_quote"]
    assert r["gross_rs"] == pytest.approx((120.5 - 101) * 65) and r["cost_rs"] > 0 and r["net_rs"] == pytest.approx(r["gross_rs"] - r["cost_rs"]) and r["net_rs"] < r["gross_rs"]


def test_latency_skips_early_snapshots_and_missed_when_ask_runs_away():
    sn = snaps([("10:00", 100, 99, 101)])
    assert S.settle(sig(t=ep("10:00")), sn, half_spread=H)["status"] == "NO_DATA:no_snapshot_after_signal"                 # the only snapshot is inside the 20 s delay window
    fast = snaps([("10:00", 100, 99, 100.5), ("10:03", 120, 118, 122), ("10:05", 125, 124, 126)])
    r = S.settle(sig(t=ep("10:00")), fast, half_spread=H); assert r["status"] == "MISSED"                      # 10:00 snapshot is before t+20s; the next ask is 22% above entry
    ok = snaps([("10:00", 100, 99, 100.5), ("10:01", 100, 99, 101), ("10:05", 130, 129, 131)])
    assert S.settle(sig(t=ep("10:00")), ok, half_spread=H)["fill"] == 101                                       # 10:00 snapshot never used (delay 20 s)


def test_stop_first_gap_through_stop_and_time_exit():
    r = S.settle(sig(), snaps([("10:01", 100, 99, 101), ("10:05", 80, 79, 81), ("10:10", 130, 129, 131)]), half_spread=H)
    assert r["exit_reason"] == "SL" and r["exit"] == 79                                                         # gap: sell at the bid, below the stop
    r = S.settle(sig(), snaps([("10:01", 100, 99, 101), ("10:30", 105, 104, 106), ("15:16", 106, 105, 107)]), half_spread=H)
    assert r["exit_reason"] == "TIME" and r["exit"] == 105
    assert S.settle(sig(), snaps([("10:01", 100, 99, 101), ("10:30", 105, 104, 106)]), half_spread=H)["status"] == "NO_DATA:no_exit_snapshot"


def test_missing_bid_ask_uses_modelled_half_spread_both_ways():
    sn = snaps([("10:01", 100, 0, 0), ("10:05", 125, 0, 0)])
    r = S.settle(sig(), sn, half_spread=0.02)
    assert r["fill"] == pytest.approx(102.0) and r["exit"] == pytest.approx(122.5) and not r["fill_real_quote"]


def test_above_trigger_and_not_triggered():
    sn = snaps([("10:01", 95, 94, 96), ("10:05", 101, 100, 102), ("10:10", 125, 124, 126)])
    r = S.settle(sig(entry_mode="ABOVE", entry=100.0), sn, half_spread=H); assert r["status"] == "SETTLED" and r["fill"] == 102 and r["fill_t"] == ep("10:05")
    assert S.settle(sig(entry_mode="ABOVE", entry=200.0), sn, half_spread=H)["status"] == "NOT_TRIGGERED"


def test_mcx_session_end_and_cost_model():
    r = S.settle(sig(underlying="NATURALGAS", strike=270.0, entry=12.0, entry_hi=12.0, sl=9.0, targets=[15.0]), snaps([("10:01", 12, 11.9, 12.1), ("22:00", 12.5, 12.4, 12.6), ("23:16", 12.2, 12.1, 12.3)]), half_spread=0.02)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "TIME" and r["lot"] == 1250 and r["cost_rs"] == pytest.approx(113.5, rel=0.05)


def test_summary_metrics_and_drawdown():
    rs = [{"status": "SETTLED", "net_rs": x, "gross_rs": x + 10, "cost_rs": 10, "fill_t": i, "fill_real_quote": True, "exit_real_quote": True} for i, x in enumerate([500, -200, -400, 300])] + [{"status": "MISSED"}]
    s = S.summarize(rs, 50000.0)
    assert s["net_rs"] == 200 and s["return_pct"] == pytest.approx(0.4) and s["win_rate"] == 0.5 and s["max_drawdown_rs"] == 600 and s["by_status"] == {"SETTLED": 4, "MISSED": 1} and s["real_quote_fill_share"] == 1.0


# ---------------- submission ledger ----------------
GOODSUB = {"participant": "INSTITUTION_TRADER", "underlying": "nifty", "expiry": "27OCT2026", "strike": 23000, "option_type": "ce", "entry_mode": "above", "entry": 100, "sl": 85, "targets": [120, 140]}


def test_submission_validation_and_hash_chain(tmp_path):
    p = str(tmp_path / "h.jsonl")
    a = SU.submit(GOODSUB, p, now=ep("10:00")); b = SU.submit({**GOODSUB, "strike": 23100}, p, now=ep("10:05"))
    assert a["id"] == "H00001" and b["id"] == "H00002" and a["expiry"] == "2026-10-27" and a["underlying"] == "NIFTY" and L.verify_chain(p)[0]
    for bad in ({"underlying": "TCS"}, {"sl": 120}, {"targets": [90]}, {"option_type": "XX"}, {"participant": ""}, {"entry": 0}, {"expiry": "soon"}):
        with pytest.raises(SU.SubmissionError): SU.submit({**GOODSUB, **bad}, p)
    lines = open(p).read().splitlines(); t = json.loads(lines[0]); t["entry"] = 1; open(p, "w").write(json.dumps(t, sort_keys=True) + "\n" + lines[1] + "\n")
    assert not L.verify_chain(p)[0]                                                                              # editing a submitted signal is detected


# ---------------- spread model + end-to-end ----------------
def mkdb(tmp_path, rows):
    p = str(tmp_path / "m.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE quote_snapshots (received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, ltp REAL, bid REAL, ask REAL, exch_ts TEXT)")
    c.executemany("INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,NULL)", rows); c.commit(); c.close(); return p


def row(hhmm, und, ltp, bid, ask, strike=23000.0, ot="CE", exp="27OCT2026"):
    ts = datetime.fromisoformat(f"{DAY}T{hhmm}:00+05:30").astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return (ts, und, "OPTION", exp, strike, ot, DAY, ltp, bid, ask)


def test_spread_model_measured_proxy_and_fallback(tmp_path):
    rows = [row("10:00", "NIFTY", 100.0, 99.0, 101.0, strike=23000 + i * 50) for i in range(300)]
    p = mkdb(tmp_path, rows)
    m = SM.half_spread("NIFTY", [DAY], p); assert m["half_spread"] == pytest.approx(0.01) and m["source"].startswith("MEASURED")
    s = SM.half_spread("SENSEX", [DAY], p); assert s["half_spread"] == pytest.approx(0.01) and "PROXY_NIFTY" in s["source"] and "assumption" in s["source"]
    f = SM.half_spread("CRUDEOIL", [DAY], p); assert f["half_spread"] == SM.FALLBACK_HALF and "FALLBACK" in f["source"]


def test_run_settles_both_sides_identically(tmp_path):
    rows = [row("10:00", "NIFTY", 100, 99, 101), row("10:03", "NIFTY", 125, 124, 126)] + [row("09:30", "NIFTY", 100.0, 99.0, 101.0, strike=24000 + i * 50) for i in range(300)]
    p = mkdb(tmp_path, rows)
    a = sig(id="a", participant="ZEROHERO_AI", t=ep("09:59")); b = sig(id="h", participant="INSTITUTION_TRADER", t=ep("09:59"))
    out = R.run([a, b], market_db=p)
    ra, rb = out["ZEROHERO_AI"]["results"][0], out["INSTITUTION_TRADER"]["results"][0]
    assert ra["status"] == rb["status"] == "SETTLED" and ra["net_rs"] == rb["net_rs"] and ra["spread_source"].startswith("MEASURED")


def test_ai_signal_loader_reads_only_autoscalp_since_date_and_replays_exit_as_event(tmp_path):
    p = str(tmp_path / "c.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE ai_paper_trades (trade_id TEXT, underlying TEXT, expiry TEXT, strike REAL, option_type TEXT, opened_ts TEXT, closed_ts TEXT, entry REAL, stop_loss REAL, target_1 REAL, target_2 REAL, strategy TEXT)")
    ins = "INSERT INTO ai_paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
    c.execute(ins, ("a", "NIFTY", "27OCT2026", 23000, "CE", "2026-10-21T04:30:00+00:00", "2026-10-21T04:55:00+00:00", 100, 85, 120, 140, "AUTOSCALP"))
    c.execute(ins, ("b", "NIFTY", "27OCT2026", 23000, "CE", "2026-10-21T04:30:00+00:00", None, 100, 85, 120, 140, "MANUAL"))
    c.execute(ins, ("c", "NIFTY", "27OCT2026", 23000, "CE", "2026-10-19T04:30:00+00:00", None, 100, 85, 120, 140, "AUTOSCALP"))
    c.execute(ins, ("d", "NIFTY", "27OCT2026", 23000, "CE", "2026-10-21T04:30:00+00:00", None, 100, None, None, None, "AUTOSCALP"))
    c.commit(); c.close()
    got = R.ai_signals(p, "2026-10-21"); assert [g["id"] for g in got] == ["a"] and got[0]["participant"] == "ZEROHERO_AI" and got[0]["expiry"] == "2026-10-27"
    assert got[0]["events"] == [{"t": datetime.fromisoformat("2026-10-21T04:55:00+00:00").timestamp(), "action": "EXIT_NOW"}]


def test_modules_are_read_only_and_have_no_order_or_network_code():
    d = os.path.dirname(S.__file__)
    for f in ("settle.py", "spread_model.py", "run.py", "submit.py"):
        s = open(os.path.join(d, f)).read()
        for bad in ("requests", "urllib", "socket", "app.execution", "place_order", "subprocess"): assert bad not in s, (f, bad)
    assert "mode=ro" in open(os.path.join(d, "settle.py")).read() and "mode=ro" in open(os.path.join(d, "spread_model.py")).read()


# ---------------- order types and management events ----------------
def ev(hhmm, action, **kw): return {"t": ep(hhmm), "action": action, **kw}


def test_limit_buy_waits_for_the_price_to_come_down_and_fills_at_ask():
    sn = snaps([("10:01", 110, 109, 111), ("10:03", 104, 103, 105), ("10:05", 99, 98, 99.5), ("10:10", 125, 124, 126)])
    r = S.settle(sig(entry_mode="LIMIT", entry=100.0, sl=90.0, targets=[120.0]), sn, half_spread=H)
    assert r["status"] == "SETTLED" and r["fill"] == 99.5 and r["fill_t"] == ep("10:05") and r["exit_reason"] == "TARGET"
    assert S.settle(sig(entry_mode="LIMIT", entry=80.0), sn, half_spread=H)["status"] == "NOT_TRIGGERED"


def test_cancel_before_fill_and_exit_now_before_fill_cancel():
    sn = snaps([("10:01", 110, 109, 111), ("10:05", 104, 103, 105), ("10:10", 99, 98, 99.5)])
    for a in ("CANCEL", "EXIT_NOW"):
        r = S.settle(sig(entry_mode="LIMIT", entry=100.0, events=[ev("10:02", a)]), sn, half_spread=H); assert r["status"] == "CANCELLED"
    r = S.settle(sig(entry_mode="LIMIT", entry=100.0, sl=90.0, targets=[200.0], events=[ev("10:09", "CANCEL")]), sn, half_spread=H)
    assert r["status"] == "CANCELLED"                                                                          # cancel effective before the 10:10 fill snapshot


def test_events_have_the_same_20s_delay_and_are_never_retroactive():
    sn = snaps([("10:01", 100, 99, 101), ("10:02", 100, 99, 101), ("10:03", 90, 89, 91), ("10:05", 130, 129, 131)])
    # SL raised to 95 at 10:02:50 would only apply from 10:03:10 -> the 10:03 snapshot (bid 89) is the first affected one, but 10:03 < 10:03:10, so the old SL 85 holds there
    late = {"t": ep("10:02") + 50, "action": "AMEND_SL", "sl": 95.0}
    r = S.settle(sig(events=[late]), sn, half_spread=H); assert r["exit_reason"] == "TARGET"                         # 10:03 bid 89 is above old SL 85: not stopped; target hit later
    early = {"t": ep("10:02") - 10, "action": "AMEND_SL", "sl": 95.0}
    r = S.settle(sig(events=[early]), sn, half_spread=H); assert r["exit_reason"] == "SL" and r["exit"] == 89 and r["events_applied"] == 1


def test_amend_targets_extends_the_ride_and_amend_sl_locks_profit():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 118, 117, 119), ("10:10", 121, 120, 122), ("10:15", 160, 159, 161), ("10:20", 150, 149, 151)])
    base = S.settle(sig(), sn, half_spread=H); assert base["exit"] == 120 and base["exit_reason"] == "TARGET"
    r = S.settle(sig(events=[ev("10:06", "AMEND_TARGETS", targets=[155.0])]), sn, half_spread=H); assert r["exit"] == 159 and r["exit_reason"] == "TARGET" and r["net_rs"] > base["net_rs"]
    sn2 = snaps([("10:01", 100, 99, 101), ("10:05", 115, 114, 116), ("10:10", 108, 107, 109), ("10:15", 130, 129, 131)])
    r = S.settle(sig(targets=[150.0], events=[ev("10:06", "AMEND_SL", sl=110.0)]), sn2, half_spread=H); assert r["exit_reason"] == "SL" and r["exit"] == 107       # stop moved above entry, gap fills at bid


def test_trailing_stop_points_and_pct():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 120, 119, 121), ("10:10", 140, 139, 141), ("10:15", 128, 127, 129), ("10:20", 150, 149, 151)])
    r = S.settle(sig(targets=[200.0], events=[ev("10:02", "TRAIL_SL", trail_points=10.0)]), sn, half_spread=H)
    assert r["exit_reason"] == "TRAIL" and r["exit"] == 127                                                       # hwm 139 - 10 = 129 >= bid 127
    r = S.settle(sig(targets=[200.0], events=[ev("10:02", "TRAIL_SL", trail_pct=5.0)]), sn, half_spread=H)
    assert r["exit_reason"] == "TRAIL" and r["exit"] == 127                                                       # 139*0.95 = 132.05
    r = S.settle(sig(targets=[200.0], events=[ev("10:02", "TRAIL_SL", trail_pct=30.0)]), sn, half_spread=H)
    assert r["status"] == "NO_DATA:no_exit_snapshot"                                                              # wide trail never triggered and no session-end snapshot: unsettled, not guessed


def test_emergency_exit_sells_at_bid_after_delay_and_later_events_are_ignored():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 108, 107, 109), ("10:10", 90, 89, 91), ("10:15", 130, 129, 131)])
    r = S.settle(sig(targets=[200.0], events=[ev("10:04", "EXIT_NOW"), ev("10:12", "AMEND_SL", sl=1.0)]), sn, half_spread=H)
    assert r["exit_reason"] == "EMERGENCY" and r["exit"] == 107 and r["fill_t"] == ep("10:01") and r["events_applied"] >= 1


def test_ai_exit_event_backstop_does_not_override_an_earlier_stop():
    sn = snaps([("10:01", 100, 99, 101), ("10:05", 80, 79, 81), ("10:30", 95, 94, 96)])
    r = S.settle(sig(events=[ev("10:25", "EXIT_NOW")]), sn, half_spread=H); assert r["exit_reason"] == "SL" and r["exit"] == 79


def test_event_validation_and_submission(tmp_path):
    p = str(tmp_path / "h.jsonl"); s1 = SU.submit({**GOODSUB, "entry_mode": "limit"}, p, now=ep("10:00"))
    e1 = SU.submit_event({"signal_id": s1["id"], "action": "amend_sl", "sl": 95}, "INSTITUTION_TRADER", p, now=ep("10:05"))
    e2 = SU.submit_event({"signal_id": s1["id"], "action": "TRAIL_SL", "trail_pct": 5}, "INSTITUTION_TRADER", p, now=ep("10:06"))
    assert e1["id"] == "E00001" and e2["id"] == "E00002" and e1["action"] == "AMEND_SL" and L.verify_chain(p)[0] and s1["entry_mode"] == "LIMIT"
    for bad in ({"action": "BUY_MORE"}, {"action": "AMEND_SL", "sl": 0}, {"action": "AMEND_TARGETS", "targets": []}, {"action": "TRAIL_SL"}, {"action": "TRAIL_SL", "trail_points": 1, "trail_pct": 5}, {"action": "TRAIL_SL", "trail_pct": 100}, {"signal_id": "H99999", "action": "CANCEL"}):
        with pytest.raises(SU.SubmissionError): SU.submit_event({"signal_id": s1["id"], "action": "CANCEL", **bad}, "INSTITUTION_TRADER", p)
    with pytest.raises(SU.SubmissionError): SU.submit_event({"signal_id": s1["id"], "action": "CANCEL"}, "SOMEONE_ELSE", p)                    # not their signal
    for i in range(SU.MAX_EVENTS_PER_SIGNAL - 2): SU.submit_event({"signal_id": s1["id"], "action": "EXIT_NOW"}, "INSTITUTION_TRADER", p)
    with pytest.raises(SU.SubmissionError, match="at most"): SU.submit_event({"signal_id": s1["id"], "action": "EXIT_NOW"}, "INSTITUTION_TRADER", p)


def test_human_loader_attaches_events_and_signal_ids_ignore_events(tmp_path):
    p = str(tmp_path / "h.jsonl"); day = lambda d: datetime.fromisoformat(f"{d}T05:00:00+00:00").timestamp()
    a = SU.submit(GOODSUB, p, now=day("2026-10-27")); SU.submit_event({"signal_id": a["id"], "action": "AMEND_SL", "sl": 90}, "INSTITUTION_TRADER", p, now=day("2026-10-27") + 60)
    b = SU.submit(GOODSUB, p, now=day("2026-10-27") + 120); assert b["id"] == "H00002"
    got = R.human_signals(p, start="2026-10-26", end="2026-11-25"); assert [g["id"] for g in got] == ["H00001", "H00002"] and got[0]["events"] == [{"action": "AMEND_SL", "sl": 90.0, "t": day("2026-10-27") + 60}] and got[1]["events"] == []
    assert SU.load(p)[1]["type"] == "HUMAN_EVENT"


def test_information_barrier_no_trading_code_imports_the_arena():
    """The AI engine must be unable to read the human side's signals: nothing outside app/arena imports it (except the router mount and the auth-exempt hook)."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"); bad = []
    for dp, _, fs in os.walk(root):
        if "__pycache__" in dp or os.sep + "arena" in dp: continue
        for f in fs:
            if not f.endswith(".py"): continue
            src = open(os.path.join(dp, f), encoding="utf-8").read()
            rel = os.path.relpath(os.path.join(dp, f), root)
            if rel == os.path.join("active_arm", "run.py"): continue          # reporting/settlement side of the ACTIVE arm (uses the arena settlement ENGINE only; never imported by the runner)
            if ("arena" in src.lower()) and f not in ("main.py", "dhan_routes.py", "rules.yaml") and "rules_registry" not in dp: bad.append(rel)
    assert bad == [], f"modules referencing the arena (information barrier): {bad}"
    for f in ("app/autoscalp/runner.py", "app/engines/scalp_strategy.py", "app/rules_engine/shadow.py", "app/active_arm/shadow.py", "app/active_arm/__init__.py"): assert "arena" not in open(os.path.join(os.path.dirname(root), f)).read().lower()
    runner = open(os.path.join(os.path.dirname(root), "app/autoscalp/runner.py")).read()
    assert "active_arm.run" not in runner and "from ..active_arm import run" not in runner                                   # the runner only ever imports the log-only shadow module


# ---------------- SL-at-fill rule (forward-only) ----------------
def _rule(tmp_path, monkeypatch, from_ts):
    import json
    p = tmp_path / "rule_effective.json"; p.write_text(json.dumps({"sl_at_fill_from_ts": from_ts})); monkeypatch.setattr(S, "RULES_FILE", str(p))


H2 = [("10:03", 9.55, 9.55, 9.6), ("10:06", 9.85, 9.75, 9.85), ("10:09", 9.75, 9.7, 9.75), ("10:12", 10.15, 10.05, 10.15)]


def test_sl_at_fill_exits_immediately_when_stop_is_above_the_fill_market(tmp_path, monkeypatch):
    s0 = sig(underlying="NATURALGAS", strike=290.0, option_type="PE", entry_mode="LIMIT", entry=9.8, entry_hi=9.8, sl=9.7, targets=[10.0], t=ep("10:00"))
    sn = snaps(H2)
    base = S.settle(s0, sn, half_spread=0.0); assert base["exit_reason"] == "SL" and base["fill"] == 9.6 and base["exit"] == 9.7                       # old behaviour: the stop-out came later at a better price (artefact)
    _rule(tmp_path, monkeypatch, ep("09:59"))                                                                                                            # rule effective before this signal
    r = S.settle(s0, sn, half_spread=0.0)
    assert r["exit_reason"] == "SL_AT_FILL" and r["fill"] == 9.6 and r["exit"] == 9.55 and r["hold_sec"] == 0 and r["gross_rs"] == pytest.approx((9.55 - 9.6) * 1250) and r["net_rs"] < 0
    _rule(tmp_path, monkeypatch, ep("10:30"))                                                                                                            # rule effective AFTER the signal: not retroactive
    assert S.settle(s0, sn, half_spread=0.0)["exit_reason"] == "SL"


def test_sl_at_fill_does_not_fire_for_a_normal_stop_and_inactive_without_file(tmp_path, monkeypatch):
    sn = snaps([("10:01", 100, 99, 100.5), ("10:05", 125, 124, 126)])
    _rule(tmp_path, monkeypatch, ep("09:00")); r = S.settle(sig(), sn, half_spread=0.0); assert r["exit_reason"] == "TARGET"                          # SL 85 far below the market
    monkeypatch.setattr(S, "RULES_FILE", str(tmp_path / "missing.json")); assert S.sl_at_fill_from() is None
    assert S.settle(sig(entry_mode="LIMIT", entry=100.0, sl=99.8, t=ep("10:00")), snaps([("10:01", 99.5, 99.5, 99.9), ("10:05", 99.0, 98.9, 99.1)]), half_spread=0.0)["exit_reason"] == "SL"        # rule off: normal path


# ---------------- ATM / ITM / OTM chain ----------------
def _chain_db(tmp_path, fut_rows=None, index=None, name="c.db"):
    import sqlite3
    p = str(tmp_path / name); c = sqlite3.connect(p)
    c.execute("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY, received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, ltp REAL, bid REAL, ask REAL, exch_ts TEXT)")
    rows = []
    for k in range(9200, 9700, 50):
        for ot in ("CE", "PE"): rows.append(("2026-10-20T13:00:00Z", "CRUDEOIL", "OPTION", "17NOV2026", float(k), ot, "2026-10-20", 500.0 - (k - 9400) * 0.5 if ot == "CE" else 500.0 + (k - 9400) * 0.5, None, None))
    for e, v in (fut_rows or [("19OCT2026", 9300.0), ("19NOV2026", 9356.0), ("19DEC2026", 9400.0)]): rows.append(("2026-10-20T13:00:00Z", "CRUDEOIL", "FUTURE", e, None, None, "2026-10-20", v, None, None))
    if index: rows.append(("2026-10-20T13:00:00Z", "CRUDEOIL", "INDEX", None, None, None, "2026-10-20", index, None, None))
    c.executemany("INSERT INTO quote_snapshots (received_ts,symbol,kind,expiry,strike,option_type,session_date_ist,ltp,bid,ask) VALUES (?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close(); return p


def test_spot_uses_the_future_on_or_after_the_option_expiry_or_the_index(tmp_path):
    from app.arena import contracts as CT
    p = _chain_db(tmp_path); CT._cache.clear(); CT._chain_cache.clear()
    spot, src = CT.spot_for("CRUDEOIL", "2026-11-17", p); assert spot == 9356.0 and "2026-11-19" in src                                  # Nov future for the Nov option, not the (expired) Oct one
    p2 = _chain_db(tmp_path, index=9999.0, name="c2.db")
    spot, src = CT.spot_for("CRUDEOIL", "2026-11-17", p2); assert spot == 9999.0 and src.startswith("INDEX")


def test_chain_labels_itm_otm_correctly_for_calls_and_puts(tmp_path):
    from app.arena import contracts as CT
    p = _chain_db(tmp_path); CT._cache.clear(); CT._chain_cache.clear()
    ce = CT.chain("CRUDEOIL", "2026-11-17", "CE", 2, p); lab = {r["strike"]: r["label"] for r in ce["rows"]}
    assert ce["atm"] == 9350.0 and ce["step"] == 50.0 and lab[9350.0] == "ATM" and lab[9300.0] == "ITM1" and lab[9250.0] == "ITM2" and lab[9400.0] == "OTM1" and lab[9450.0] == "OTM2" and len(ce["rows"]) == 5
    pe = CT.chain("CRUDEOIL", "2026-11-17", "PE", 2, p); lab = {r["strike"]: r["label"] for r in pe["rows"]}
    assert lab[9400.0] == "ITM1" and lab[9300.0] == "OTM1" and lab[9350.0] == "ATM"                                                         # puts: ITM is ABOVE spot
    assert ce["rows"][0]["quote"] is not None and ce["problems"] == []
    miss = CT.chain("CRUDEOIL", "2026-12-01", "CE", 2, p); assert miss["problems"] and miss["rows"] == []


def test_chain_endpoint_defaults_to_current_expiry_and_labels_roles(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.arena import api as A, contracts as CT, submit as SU
    p = _chain_db(tmp_path); CT._cache.clear(); CT._chain_cache.clear(); monkeypatch.setattr(CT, "DB", p)
    monkeypatch.setattr(SU, "PATH", str(tmp_path / "h.jsonl")); monkeypatch.setenv("ARENA_TOKEN_HUMAN", "tok"); A._hits.clear()
    cl = TestClient(FastAPI()); app = cl.app; app.include_router(A.router)
    j = cl.get("/api/arena/public/chain?underlying=crudeoil&option_type=ce&n=3", headers={"X-Arena-Token": "tok"}).json()
    assert j["expiry"] == "2026-11-17" and j["expiries"][0]["role"] == "current" and j["atm"] == 9350.0 and len(j["rows"]) == 7
    assert cl.get("/api/arena/public/chain?underlying=TCS", headers={"X-Arena-Token": "tok"}).status_code == 422 and cl.get("/api/arena/public/chain?underlying=NIFTY").status_code == 401
    CT.warm(path=p)                                                                                                              # never raises, fills the cache


def test_load_snaps_retimes_future_stamped_frame(tmp_path):
    """23-Oct-2026 NG 265 PE: a frame received 11:48:08 but stamped (and volume-matching) 11:57:42 caused a false SL 9 min early."""
    import sqlite3
    from app.arena import settle as S
    db = str(tmp_path / "m.db"); c = sqlite3.connect(db)
    c.execute("CREATE TABLE quote_snapshots (received_ts TEXT, exch_ts TEXT, ltp REAL, bid REAL, ask REAL, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT)")
    for rt, et, px in [("11:48:03", "11:48:02", 9.25), ("11:48:08", "11:57:42", 7.85), ("11:48:11", "11:48:09", 9.25)]:
        c.execute("INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"2026-10-23T{rt}Z", f"2026-10-23T{et}Z", px, px - 0.05, px, "NATURALGAS", "OPTION", "23OCT2026", 265.0, "PE", "2026-10-23"))
    c.commit(); c.close()
    sn = S.load_snaps("NATURALGAS", "2026-10-23", 265.0, "PE", "2026-10-23", db)
    assert [x["ltp"] for x in sn] == [9.25, 9.25, 7.85] and sn[-1]["t"] == S._ep("2026-10-23T11:57:42Z")


def test_last_quote_and_latest_quote_ignore_future_stamped_frame(tmp_path):
    from app.arena import contracts as C
    db = str(tmp_path / "m.db"); c = sqlite3.connect(db)
    c.execute("CREATE TABLE quote_snapshots (id INTEGER PRIMARY KEY, received_ts TEXT, exch_ts TEXT, ltp REAL, bid REAL, ask REAL, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, source TEXT)")
    for rt, et, px in [("11:48:03", "11:48:02", 9.25), ("11:48:08", "11:57:42", 7.85)]:
        c.execute("INSERT INTO quote_snapshots (received_ts,exch_ts,ltp,bid,ask,symbol,kind,expiry,strike,option_type,session_date_ist,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"2026-10-23T{rt}Z", f"2026-10-23T{et}Z", px, px - 0.05, px, "NATURALGAS", "OPTION", "23OCT2026", 265.0, "PE", "2026-10-23", "ANGELONE_QUOTE_FULL"))
    c.commit(); c.close()
    assert C.last_quote("NATURALGAS", "2026-10-23", 265.0, "PE", "2026-10-23", db)["ltp"] == 9.25
    assert C.latest_quote("NATURALGAS", "2026-10-23", 265.0, "PE", db)["ltp"] == 9.25


# ---------------- DATA_GAP / ABOVE-chase / stale levels (rules from gap_chase_rules_from_ts, not retroactive) ----------------
def _rules(tmp_path, monkeypatch, from_ts):
    p = tmp_path / "rule_effective.json"; p.write_text(json.dumps({"gap_chase_rules_from_ts": from_ts})); monkeypatch.setattr(S, "RULES_FILE", str(p))


def test_data_gap_voids_near_fill_and_missed_only_for_new_signals(tmp_path, monkeypatch):
    sn = snaps([("10:00", 100, 99, 100.5), ("10:02", 100, 99, 101), ("10:05", 130, 129, 131)])        # 100 s of silence around the execution instant
    _rules(tmp_path, monkeypatch, ep("09:00"))
    r = S.settle(sig(t=ep("10:00")), sn, half_spread=H); assert r["status"] == "DATA_GAP" and r["gap_sec"] > 60
    _rules(tmp_path, monkeypatch, ep("11:00"))                                                                                 # rule deployed after the signal: old behaviour
    assert S.settle(sig(t=ep("10:00")), sn, half_spread=H)["status"] == "SETTLED"
    _rules(tmp_path, monkeypatch, ep("09:00"))
    dense = snaps([("10:00", 100, 99, 100.5), ("10:01", 100, 99, 101), ("10:05", 130, 129, 131)])
    assert S.settle(sig(t=ep("10:00")), dense, half_spread=H)["status"] == "SETTLED"


def test_above_chase_is_missed_and_stale_target_is_invalid(tmp_path, monkeypatch):
    _rules(tmp_path, monkeypatch, ep("09:00"))
    chase = snaps([("10:00", 100, 99, 100.5), ("10:01", 168, 167, 168.5), ("10:05", 190, 189, 191)])
    assert S.settle(sig(entry_mode="ABOVE", entry=150.0, entry_hi=150.0, sl=114.0, targets=[185.0]), chase, half_spread=H)["status"] == "MISSED"        # fills 12% above its trigger
    stale = snaps([("10:00", 330, 329, 331), ("10:01", 343, 342, 344), ("10:02", 344, 343, 345)])
    r = S.settle(sig(entry_mode="ABOVE", entry=200.0, entry_hi=200.0, sl=168.0, targets=[231.0]), stale, half_spread=H)
    assert r["status"] == "MISSED"                                                                                              # the chase limit catches it first; with a zone-less near-market ABOVE the stale target never produces a fake TARGET
    near = snaps([("10:00", 100, 99, 100.5), ("10:01", 100, 99, 101), ("10:02", 101, 100, 102)])
    r = S.settle(sig(entry_mode="NEAR", entry=100.0, entry_hi=100.0, sl=85.0, targets=[100.5]), near, half_spread=H); assert r["status"] == "INVALID_LEVELS"


def test_void_status_from_ledger_correction():
    r = S.settle(sig(void_status="DATA_GAP", void_reason="capture blackout"), snaps([("10:01", 100, 99, 101)]), half_spread=H)
    assert r["status"] == "DATA_GAP" and r["void_reason"] == "capture blackout"


def test_status_correction_rejects_unknown_signal_and_bad_status(tmp_path):
    p = str(tmp_path / "h.jsonl")
    with pytest.raises(SU.SubmissionError): SU.submit_status_correction("nope", "DATA_GAP", "x", path=p)
    with pytest.raises(SU.SubmissionError): SU.submit_status_correction("nope", "WIN", "x", path=p)
