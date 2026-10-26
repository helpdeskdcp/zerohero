import time

import pytest

from app.arena import channel_ingest as CI, run as R, settle as S, submit as SU
from tests.test_channel_ingest import post
from tests.test_channel_parse import HERO


def snaps(t0, rows):
    return [{"t": t0 + dt, "ltp": ask, "bid": ask - 1, "ask": ask} for dt, ask in rows]


def sig(t0, **kw):
    return {"id": "X", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "PE", "entry_mode": "LIMIT", "entry": 100.0, "entry_hi": 100.0, "sl": 60.0, "targets": [140.0], "t": t0, "events": [], **kw}


def test_limit_expires_when_not_filled_inside_its_window():
    t0 = 1_760_000_000.0                                     # an arbitrary instant; the session-end logic only needs the snapshots to stay within one session
    rows = [(30, 120), (400, 118), (1000, 99), (1100, 150)]
    late = S.settle(sig(t0, valid_sec=900), snaps(t0, rows), half_spread=0.0); assert late["status"] == "NOT_TRIGGERED"             # the 99 ask comes at +1000 s: too late
    old = S.settle(sig(t0), snaps(t0, rows), half_spread=0.0); assert old["status"] != "NOT_TRIGGERED" and old["fill"] == 99.0        # without a window the old (session-long) behaviour is unchanged
    ok = S.settle(sig(t0, valid_sec=900), snaps(t0, [(30, 120), (400, 99), (500, 150)]), half_spread=0.0); assert ok["fill"] == 99.0 and ok["status"] != "NOT_TRIGGERED"   # filled inside the window


def test_validate_and_ledger_keep_valid_sec(tmp_path):
    base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100, "sl": 80, "targets": [120]}
    assert "valid_sec" not in SU.validate(base) and SU.validate({**base, "valid_sec": 900})["valid_sec"] == 900
    for bad in (30, 99999): 
        with pytest.raises(SU.SubmissionError): SU.validate({**base, "valid_sec": bad})
    rec = SU.submit({**base, "valid_sec": 900}, path=str(tmp_path / "l.jsonl"), practice=True); assert rec["valid_sec"] == 900
    assert R.human_signals(str(tmp_path / "l.jsonl"), include_practice=True)[0]["valid_sec"] == 900


def test_channel_ingested_entries_carry_the_15_minute_window(tmp_path, monkeypatch):
    monkeypatch.setattr(CI, "pick_expiry", lambda u, d, p=None: "2026-10-27"); monkeypatch.setattr(CI.CT, "check", lambda *a, **k: {"problems": [], "warnings": [], "last": None}); monkeypatch.setattr(CI, "_market_open", lambda u: True)
    kw = dict(participant="INSTITUTION_TRADER", mode_fn=lambda: "ARENA_LIVE", ledger=str(tmp_path / "l.jsonl"), log=str(tmp_path / "log.jsonl"))
    r = CI.handle_post(post(1, HERO), {"seen": {}, "by_msg": {}, "last_by_chat": {}}, **kw); assert r["outcome"] == "SUBMITTED"
    assert SU.load(kw["ledger"])[0]["valid_sec"] == 900


def test_validity_correction_only_for_channel_signals_and_is_applied(tmp_path):
    led = str(tmp_path / "l.jsonl"); base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100, "sl": 80, "targets": [120]}
    ch = SU.submit({**base, "note": "via channel post 5"}, path=led, practice=True); manual = SU.submit(base, path=led, practice=True); win = SU.submit({**base, "valid_sec": 600, "note": "via channel post 6"}, path=led, practice=True)
    SU.submit_validity_correction(ch["id"], 900, "rehearsal re-score", path=led)
    for bad in (manual["id"], win["id"]):
        with pytest.raises(SU.SubmissionError): SU.submit_validity_correction(bad, 900, "x", path=led)
    got = {s["id"]: s.get("valid_sec") for s in R.human_signals(led, include_practice=True)}
    assert got == {ch["id"]: 900, manual["id"]: None, win["id"]: 600}


def test_run_cache_reuses_final_results_and_resettles_open_ones(monkeypatch):
    calls = []
    def fake_settle(sig, snaps, *, half_spread, policy="T1", latency=20):
        calls.append(sig["id"]); done = sig["id"] != "OPEN"
        return {"id": sig["id"], "participant": sig["participant"], "status": "SETTLED" if done else "NO_DATA:no_exit_snapshot", "t": sig["t"], **({"net_rs": 1.0, "gross_rs": 2.0, "cost_rs": 1.0, "fill_t": sig["t"], "fill_real_quote": True, "exit_real_quote": True} if done else {})}
    monkeypatch.setattr(R.S, "settle", fake_settle); monkeypatch.setattr(R.S, "load_snaps", lambda *a, **k: []); monkeypatch.setattr(R.SM, "half_spread", lambda *a, **k: {"half_spread": 0.0, "source": "TEST"})
    sg = lambda i: {"id": i, "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 1.0, "option_type": "CE", "t": 1_760_000_000.0}
    cache = {}; R.run([sg("A"), sg("OPEN")], cache=cache); assert calls == ["A", "OPEN"]
    R.run([sg("A"), sg("OPEN")], cache=cache); assert calls == ["A", "OPEN", "OPEN"]                                     # the settled one came from the cache, the open one was settled again
    R.run([{**sg("A"), "sl": 1.0}], cache=cache); assert calls[-1] == "A"                                                  # any change to the signal's content invalidates its entry
    R.run([sg("A")]); assert calls.count("A") == 3                                                                          # no cache argument: old behaviour


def test_captured_is_stale_while_revalidate(monkeypatch, tmp_path):
    from app.arena import contracts as CT
    import threading
    monkeypatch.setattr(CT, "_CACHE_FILE", str(tmp_path / "c.json")); monkeypatch.setattr(CT, "_file_loaded", [True]); CT._cache.clear(); CT._refreshing.clear()
    n = []; gate = threading.Event()
    def comp(u, day=None, path=None):
        n.append(1); gate.wait(2); out = {"day": "d", "expiries": {"2026-10-27": {"strikes": [float(len(n))], "raw": "27OCT2026"}}}; CT._cache[(u, day, path)] = (time.time(), out); return out
    monkeypatch.setattr(CT, "_compute_captured", comp)
    gate.set(); first = CT.captured("NIFTY"); assert first["expiries"]["2026-10-27"]["strikes"] == [1.0]                                     # very first call computes inline
    CT._cache[("NIFTY", None, None)] = (time.time() - 500, first); gate.clear(); t = time.time()
    stale = CT.captured("NIFTY"); assert stale is first and time.time() - t < 0.5                                                          # stale answer returned at once
    stale2 = CT.captured("NIFTY"); assert stale2 is first and len(n) == 2                                                                  # one background refresher only
    gate.set(); time.sleep(0.3); assert CT.captured("NIFTY")["expiries"]["2026-10-27"]["strikes"] == [2.0]                                  # refreshed value arrives


def test_cutover_moves_the_window_only_on_or_after_the_official_start(tmp_path):
    import json
    from app.arena import cutover
    p = str(tmp_path / "contest.json"); json.dump({"start": "2026-10-20", "end": "2026-11-25", "official_start": "2026-11-02"}, open(p, "w"))
    assert cutover.apply(p, "2026-11-01")["done"] is False and json.load(open(p))["start"] == "2026-10-20"                    # the day before: nothing changes
    r = cutover.apply(p, "2026-11-02"); assert r["done"] and r["old_start"] == "2026-10-20" and json.load(open(p))["start"] == "2026-11-02"
    assert cutover.apply(p, "2026-11-03")["done"] is False                                                                      # idempotent
    assert cutover.apply(str(tmp_path / "none.json"), "2026-11-02")["done"] is False


def test_goldm_settles_with_the_per_10g_multiplier_and_provisional_costs():
    from app.instrument_profiles import get_instrument_profile
    assert int(get_instrument_profile("GOLDM").lot_size.value) == 10 and "GOLDM" in SU.ALLOWED
    c = S.cost_points("GOLDM", 2450.0, 3000.0, 10)
    assert 5 < c < 15                                                                    # a few rupees per unit on a Rs 2,450 premium (about Rs 90 per lot round trip), not Rs 0 and not Rs 100
    t0 = 1_760_000_000.0; sig = {"id": "G", "participant": "P", "underlying": "GOLDM", "expiry": "2026-10-29", "strike": 146000.0, "option_type": "PE", "entry_mode": "NEAR", "entry": 2450.0, "entry_hi": 2450.0, "sl": 1950.0, "targets": [3050.0], "t": t0, "events": []}
    sn = [{"t": t0 + 30, "ltp": 2450, "bid": 2440, "ask": 2450}, {"t": t0 + 300, "ltp": 3060, "bid": 3060, "ask": 3070}]
    r = S.settle(sig, sn, half_spread=0.0)
    assert r["status"] == "SETTLED" and r["lot"] == 10 and abs(r["gross_rs"] - (3060 - 2450) * 10) < 1e-6 and "PROVISIONAL" in r["cost_note"]


def test_stop_loss_is_optional_and_a_trade_without_one_runs_to_its_target_or_the_session_end():
    base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "NEAR", "entry": 100, "targets": [130]}
    assert SU.validate(base)["sl"] is None and SU.validate({**base, "sl": 80})["sl"] == 80.0
    for bad in (0, 100, 120):
        with pytest.raises(SU.SubmissionError): SU.validate({**base, "sl": bad})                                              # a stop that is given must still be below the entry
    t0 = 1_760_000_000.0; sig = {"id": "N", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.0, "entry_hi": 100.0, "sl": None, "targets": [130.0], "t": t0, "events": []}
    fall = [{"t": t0 + 30, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 120, "ltp": 40, "bid": 39, "ask": 41}, {"t": t0 + 240, "ltp": 125, "bid": 124, "ask": 126}, {"t": t0 + 300, "ltp": 131, "bid": 130.5, "ask": 131.5}]
    r = S.settle(sig, fall, half_spread=0.0); assert r["status"] == "SETTLED" and r["exit_reason"] == "TARGET" and r["exit"] == 130.5                           # the dip to 39 did not stop it out: there is no stop
    held = S.settle(sig, fall[:2], half_spread=0.0); assert held["status"].startswith("NO_DATA")                                                                  # still open, no stop triggered
    late = S.settle({**sig, "events": [{"t": t0 + 150, "action": "AMEND_SL", "sl": 50.0}]}, fall, half_spread=0.0)                                                # a stop added later works from then on, never retroactively
    assert late["exit_reason"] == "TARGET"                                                                                                                       # price was above 50 after the amendment


def test_targets_are_optional_a_no_target_no_stop_trade_runs_to_the_session_end():
    base = {"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "NEAR", "entry": 100}
    assert SU.validate(base)["targets"] == [] and SU.validate({**base, "targets": [130]})["targets"] == [130.0]
    with pytest.raises(SU.SubmissionError): SU.validate({**base, "targets": [90]})                                              # a target that is given must be above the entry
    t0 = 1_760_000_000.0; sig = {"id": "N2", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.0, "entry_hi": 100.0, "sl": None, "targets": [], "t": t0, "events": []}
    r = S.settle(sig, [{"t": t0 + 30, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 120, "ltp": 300, "bid": 299, "ask": 301}], half_spread=0.0); assert r["status"].startswith("NO_DATA")        # never exits by itself, however far it runs
    r = S.settle({**sig, "events": [{"t": t0 + 100, "action": "EXIT_NOW"}]}, [{"t": t0 + 30, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 200, "ltp": 300, "bid": 299, "ask": 301}], half_spread=0.0)
    assert r["status"] == "SETTLED" and r["exit_reason"] == "EMERGENCY" and r["exit"] == 299.0                                  # a manual exit-now closes it


def test_lots_scale_the_rupees_not_the_per_lot_figure_and_flat_brokerage_is_paid_once():
    t0 = 1_760_000_000.0; sg = lambda **k: {"id": "L", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.0, "entry_hi": 100.0, "sl": None, "targets": [120.0], "t": t0, "events": [], **k}
    sn = [{"t": t0 + 30, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 200, "ltp": 121, "bid": 120.5, "ask": 121.5}]
    r1 = S.settle(sg(), sn, half_spread=0.0); r2 = S.settle(sg(lots=2), sn, half_spread=0.0)
    assert r1["lots"] == 1 and r2["lots"] == 2 and r2["gross_rs"] == pytest.approx(2 * r1["gross_rs"]) and r2["cost_rs"] < 2 * r1["cost_rs"]                    # brokerage is flat per order, not per lot
    assert r2["net_rs_1lot"] == pytest.approx(r1["net_rs"]) and r2["net_rs"] > 2 * r1["net_rs"]                                                                   # the per-lot figure is the same whatever the quantity
    sm = S.summarize([r2]); assert sm["net_rs"] == pytest.approx(r2["net_rs"]) and sm["net_rs_1lot"] == pytest.approx(r1["net_rs"]) and sm["lots_traded"] == 2


def test_quantity_can_change_only_before_the_trade_is_active():
    t0 = 1_760_000_000.0
    sig = {"id": "Q", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": "LIMIT", "entry": 100.0, "entry_hi": 100.0, "sl": None, "targets": [130.0], "t": t0, "lots": 2,
           "events": [{"t": t0 + 40, "action": "AMEND_LOTS", "lots": 4}, {"t": t0 + 400, "action": "AMEND_LOTS", "lots": 1}]}
    sn = [{"t": t0 + 30, "ltp": 110, "bid": 109, "ask": 110}, {"t": t0 + 100, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 500, "ltp": 131, "bid": 130.5, "ask": 131.5}]
    r = S.settle(sig, sn, half_spread=0.0)
    assert r["status"] == "SETTLED" and r["lots"] == 4                                              # changed while waiting (effective 20 s after t0+40); the change sent after the fill is ignored
    assert S.settle({**sig, "events": []}, sn, half_spread=0.0)["lots"] == 2
    for bad in (0, 11, 1.5): 
        with pytest.raises(SU.SubmissionError): SU.validate({"participant": "P", "underlying": "NIFTY", "expiry": "27OCT2026", "strike": 23000, "option_type": "CE", "entry_mode": "NEAR", "entry": 100, "lots": bad})
    with pytest.raises(SU.SubmissionError): SU.validate_event({"signal_id": "X", "action": "AMEND_LOTS", "lots": 0})
    assert SU.validate_event({"signal_id": "X", "action": "AMEND_LOTS", "lots": 3})["lots"] == 3


def test_above_entry_zone_does_not_chase_above_its_upper_end():
    t0 = 1_760_000_000.0
    sig = {"id": "Z", "participant": "P", "underlying": "SENSEX", "expiry": "2026-10-22", "strike": 72800.0, "option_type": "CE", "entry_mode": "ABOVE", "entry": 570.0, "entry_hi": 575.0, "sl": 540.0, "targets": [600.0], "t": t0, "valid_sec": 900, "events": []}
    gap = [{"t": t0 + 30, "ltp": 580, "bid": 579, "ask": 581}, {"t": t0 + 100, "ltp": 590, "bid": 589, "ask": 591}]
    assert S.settle(sig, gap, half_spread=0.0)["status"] == "NOT_TRIGGERED"                                                # ran past the zone: not bought
    back = gap + [{"t": t0 + 200, "ltp": 574, "bid": 573, "ask": 574.5}, {"t": t0 + 300, "ltp": 601, "bid": 600.5, "ask": 601.5}]
    r = S.settle(sig, back, half_spread=0.0); assert r["status"] == "SETTLED" and r["fill"] == 574.5                        # back inside the zone: bought there
    assert S.settle({k: v for k, v in sig.items() if k != "entry_hi"}, gap, half_spread=0.0)["status"] != "NOT_TRIGGERED"   # without a zone (all older signals) the old behaviour is unchanged
    assert SU.validate({"participant": "P", "underlying": "SENSEX", "expiry": "22OCT2026", "strike": 72800, "option_type": "CE", "entry_mode": "ABOVE", "entry": 570, "entry_hi": 575})["entry_hi"] == 575.0
    with pytest.raises(SU.SubmissionError): SU.validate({"participant": "P", "underlying": "SENSEX", "expiry": "22OCT2026", "strike": 72800, "option_type": "CE", "entry_mode": "ABOVE", "entry": 570, "entry_hi": 560})


def test_exit_now_has_a_short_execution_delay_only_after_the_rule_is_effective_and_only_for_exit_now(monkeypatch):
    t0 = 1_760_000_000.0; base = {"id": "X", "participant": "P", "underlying": "NIFTY", "expiry": "2026-10-27", "strike": 23000.0, "option_type": "CE", "entry_mode": "NEAR", "entry": 100.0, "entry_hi": 100.0, "sl": None, "targets": [], "t": t0}
    snaps = [{"t": t0 + 30, "ltp": 100, "bid": 99, "ask": 100}, {"t": t0 + 105, "ltp": 80, "bid": 80, "ask": 81}, {"t": t0 + 130, "ltp": 70, "bid": 70, "ask": 71}]
    ev = [{"t": t0 + 100, "action": "EXIT_NOW"}]
    monkeypatch.setattr(S, "rule_from", lambda name: None)                                                                      # rule not effective: 20 s -> first snapshot at/after t0+120 = bid 70
    assert S.settle({**base, "events": ev}, snaps, half_spread=0.0)["exit"] == 70
    monkeypatch.setattr(S, "rule_from", lambda name: (t0 + 50) if name == "exit_now_latency_from_ts" else None)                # effective: 3 s -> first snapshot at/after t0+103 = bid 80
    assert S.settle({**base, "events": ev}, snaps, half_spread=0.0)["exit"] == 80
    monkeypatch.setattr(S, "rule_from", lambda name: (t0 + 101) if name == "exit_now_latency_from_ts" else None)               # event posted BEFORE the effective instant: never retroactive, still 20 s
    assert S.settle({**base, "events": ev}, snaps, half_spread=0.0)["exit"] == 70
    monkeypatch.setattr(S, "rule_from", lambda name: (t0 + 50) if name == "exit_now_latency_from_ts" else None)
    sl = S.settle({**base, "sl": 50.0, "events": [{"t": t0 + 100, "action": "AMEND_SL", "sl": 85.0}]}, snaps, half_spread=0.0)   # other actions keep 20 s: the stop 85 is live only from t0+120 (bid 70 -> stopped there)
    assert sl["exit_reason"] == "SL" and sl["exit"] == 70
