"""Safe AI: the U-turn rule and the safety stack around it, with every dependency faked."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.safe_ai import live as L, signals as SG

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-11-03"
T0 = datetime.fromisoformat(DAY).replace(tzinfo=IST).replace(hour=9, minute=15).timestamp()


@pytest.fixture(autouse=True)
def _isolated_control(tmp_path, monkeypatch):
    """tests never read the real data/safe_ai/control.json (production dry-run switch) and never depend on SAFE_AI_* in the environment"""
    monkeypatch.setattr(L, "CONTROL", str(tmp_path / "no_control.json")); monkeypatch.delenv("SAFE_AI_SUBMIT", raising=False); monkeypatch.delenv("SAFE_AI_LIVE", raising=False)


def uturn_down(n_extra=0):
    """open 100.00, falls 0.70 % in 40 min, bounces to ~99.65 (half of the thrust): a confirmed down-thrust U-turn"""
    t = [(T0 + m * 60, 100 - 0.7 * m / 40) for m in range(0, 41)] + [(T0 + m * 60, 99.3 + 0.35 * (m - 40) / 10) for m in range(41, 51 + n_extra)]
    return t


def at(ticks): return ticks[-1][0]


def test_rule_fires_on_a_confirmed_uturn_and_not_on_noise():
    t = uturn_down(); r = SG.detect(t, at(t)); assert r and r["side"] == "CE" and r["features"]["thrust_pct"] == pytest.approx(0.7, abs=0.01)
    flat = [(T0 + m * 60, 100.0 + (0.01 if m % 2 else 0)) for m in range(60)]; assert SG.detect(flat, at(flat)) is None
    still_falling = [(T0 + m * 60, 100 - 0.7 * m / 40) for m in range(41)]; assert SG.detect(still_falling, at(still_falling)) is None               # no bounce yet: no turn
    up = [(T0 + m * 60, 100 + 0.7 * m / 40) for m in range(41)] + [(T0 + m * 60, 100.7 - 0.35 * (m - 40) / 10) for m in range(41, 51)]
    r = SG.detect(up, at(up)); assert r and r["side"] == "PE"                                                                                   # the mirror image
    new_low = uturn_down() + [(T0 + 52 * 60, 99.2), (T0 + 53 * 60, 99.25)]; assert SG.detect(new_low, at(new_low)) is None                       # a fresh extreme inside the hold window
    assert SG.detect(t[:12], at(t[:12])) is None                                                                                                 # too little history


def test_time_window_and_expiry_day_cut_off():
    p = SG.Params(); at_h = lambda h, m: T0 + ((h - 9) * 60 + (m - 15)) * 60
    assert SG.in_window(at_h(9, 40), p) and not SG.in_window(at_h(9, 30), p) and SG.in_window(at_h(14, 30), p) and not SG.in_window(at_h(14, 31), p)
    assert SG.in_window(at_h(13, 30), p, True) and not SG.in_window(at_h(13, 31), p, True)


class World:
    def __init__(self, tmp_path, ticks=None, ask=200.0, bid=198.0, qage=5.0, results=None, mode="ARENA_LIVE", open_=True):
        self.ticks = ticks if ticks is not None else uturn_down(); self.now_ts = self.ticks[-1][0] + 1; self.submitted = []; self.watched = []; self.results = results if results is not None else []
        self.ask, self.bid, self.qage, self.mode_, self.open_ = ask, bid, qage, mode, open_
        self.e = L.Engine(spot=lambda s: (self.ticks[-1][1], self.now_ts - 1) if s == "NIFTY" else None, seed=lambda s, d: list(self.ticks) if s == "NIFTY" else [],
                          chain=lambda s: {"day": DAY, "expiries": {"2026-11-03": {"strikes": [99.0, 100.0, 101.0]}, "2026-11-10": {"strikes": [100.0]}}} if s == "NIFTY" else {},
                          quote=lambda s, e, k, o: {"ltp": self.ask, "bid": self.bid, "ask": self.ask, "t": self.now_ts - self.qage}, submit=self._submit, watch=lambda *a: self.watched.append(a),
                          outcomes=lambda d: {"results": self.results}, mode=lambda: self.mode_, is_open=lambda ex: self.open_, now=lambda: self.now_ts,
                          state_path=str(tmp_path / "state.json"), log_path=str(tmp_path / "log.jsonl"))

    def _submit(self, body, practice): rec = {**body, "id": f"H{len(self.submitted) + 1:05d}", "practice": practice}; self.submitted.append(rec); return rec

    def log(self): return [json.loads(x) for x in open(self.e.log_path)] if __import__("os").path.exists(self.e.log_path) else []


def test_a_confirmed_uturn_becomes_one_conservative_paper_signal(tmp_path):
    w = World(tmp_path); out = w.e.tick(); assert len(out) == 1
    b = w.submitted[0]; assert (b["participant"], b["underlying"], b["option_type"], b["strike"], b["expiry"], b["lots"], b["entry_mode"]) == ("ZEROHERO_SAFE_AI", "NIFTY", "CE", 100.0, "2026-11-03", 1, "NEAR")
    assert b["entry"] == 200.0 and b["sl"] == 150.0 and b["targets"] == [240.0] and "no edge claimed" in b["note"] and w.watched == [("NIFTY", "2026-11-03", 100.0, "CE")]
    assert w.e.tick() == []                                                                                                                     # one position in flight: nothing else while it is open


def test_every_refusal_is_a_refusal_with_a_logged_reason(tmp_path):
    for kw, why in (({"ask": 20.0, "bid": 19.8}, "premium_too_low"), ({"ask": 200.0, "bid": 190.0}, "spread_too_wide"), ({"qage": 120.0}, "option_quote_too_old")):
        w = World(tmp_path, **kw); assert w.e.tick() == [] and not w.submitted and any(x["kind"] == "skip:" + why for x in w.log()), why
        for f in (tmp_path / "state.json", tmp_path / "log.jsonl"):
            if f.exists(): f.unlink()
    w = World(tmp_path, open_=False); assert w.e.tick() == []                                                                                      # exchange closed
    w = World(tmp_path, mode="ENDED"); assert w.e.tick() == []                                                                                     # contest over


def test_one_loss_stops_that_index_and_the_day_loss_lock_stops_everything(tmp_path):
    w = World(tmp_path); w.e.tick(); sid = w.submitted[0]["id"]
    w.results = [{"id": sid, "underlying": "NIFTY", "status": "SETTLED", "net_rs": -1500.0, "fill_t": w.now_ts, "hold_sec": 300}]; w.now_ts += 3000; w.e._last_outcome_check = 0
    assert w.e.tick() == [] and w.e.state["stopped"].get("NIFTY") and w.e.state["pending_fill"] is None                                           # a loss ends the day for NIFTY ...
    assert any(x["kind"] == "skip:stopped_after_a_loss_today" for x in w.log())
    w2 = World(tmp_path / "b" if False else tmp_path); w2.e.state = {"day": DAY, "n": 1, "per": {}, "busy_until": 0.0, "stopped": {}, "day_net": 0.0, "locked": False}
    w2.results = [{"id": "H9", "underlying": "SENSEX", "status": "SETTLED", "net_rs": -6500.0, "fill_t": 1.0, "hold_sec": 1}]; w2.e._last_outcome_check = 0; w2.e.tick()
    assert w2.e.state["locked"] is True and w2.e.tick() == []                                                                                      # ... and a day net of -6000 ends everything


def test_caps_and_the_in_flight_guard(tmp_path):
    w = World(tmp_path); w.e.tick(); sid = w.submitted[0]["id"]
    w.results = [{"id": sid, "underlying": "NIFTY", "status": "SETTLED", "net_rs": 900.0, "fill_t": w.now_ts, "hold_sec": 60}]; w.e._last_outcome_check = 0; w.now_ts += 60; w.e.tick()
    assert w.e.state["pending_fill"] is None and w.e.state["busy_until"] > w.now_ts                                                                 # released, but the cool-down (30 min) runs
    w.e.state["n"] = L.MAX_TRADES_DAY; w.now_ts += 4000; w.ticks = uturn_down(); assert w.e.tick() == [] and any(x["kind"] == "skip:daily_trade_cap" for x in w.log())


def test_dry_run_switch_decides_and_logs_but_never_submits(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFE_AI_SUBMIT", "0"); w = World(tmp_path); assert w.e.tick() == [] and not w.submitted and any(x["kind"] == "would_submit" for x in w.log())
    monkeypatch.setenv("SAFE_AI_LIVE", "0"); w = World(tmp_path / "x" if False else tmp_path); w.e.state = {}; assert w.e.tick() == []                          # runner switched off


def test_a_stale_index_price_blocks_everything(tmp_path):
    w = World(tmp_path); t = w.ticks; w.now_ts = t[-1][0] + 120; w.e.spot = lambda s: None; assert w.e.tick() == [] and any(x["kind"] == "skip:stale_index_price" for x in w.log())


def test_control_file_switches_ledger_writes_without_a_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("SAFE_AI_SUBMIT", raising=False); cf = tmp_path / "control.json"; monkeypatch.setattr(L, "CONTROL", str(cf))
    assert L.submit_enabled() is True                                                    # no file: on
    cf.write_text('{"submit": false}'); import time; time.sleep(2.1); assert L.submit_enabled() is False
    cf.write_text('{"submit": true}'); time.sleep(2.1); assert L.submit_enabled() is True
    monkeypatch.setenv("SAFE_AI_SUBMIT", "0"); assert L.submit_enabled() is False       # the environment switch always wins


def test_sweep_and_rejection_of_the_previous_day_high_and_low():
    pdh, pdl = 100.5, 99.0
    up = [(T0 + m * 60, 100.0 + 0.9 * m / 30) for m in range(0, 31)] + [(T0 + m * 60, 100.9 - 0.7 * (m - 30) / 10) for m in range(31, 41)]        # runs to 100.9 (0.4 % above the high) then falls back to 100.2
    r = SG.detect_sweep(up, up[-1][0], pdh, pdl); assert r and r["side"] == "PE" and r["features"]["overshoot_pct"] > 0.3
    dn = [(T0 + m * 60, 99.5 - 0.9 * m / 30) for m in range(0, 31)] + [(T0 + m * 60, 98.6 + 0.7 * (m - 30) / 10) for m in range(31, 41)]            # sweeps the low then rejects
    r = SG.detect_sweep(dn, dn[-1][0], pdh, pdl); assert r and r["side"] == "CE"
    assert SG.detect_sweep(up[:25], up[24][0], pdh, pdl) is None                                                                                    # not back inside yet
    inside = [(T0 + m * 60, 99.7 + 0.001 * (m % 3)) for m in range(60)]; assert SG.detect_sweep(inside, inside[-1][0], pdh, pdl) is None            # never touched a level


def test_thrust_follows_the_control_file_and_ignores_nonsense(tmp_path, monkeypatch):
    cf = tmp_path / "control.json"; monkeypatch.setattr(L, "CONTROL", str(cf)); w = World(tmp_path)
    cf.write_text('{"submit": true, "thrust": 0.007}'); import time; time.sleep(2.1); w.e.tick(); assert w.e.p.thrust == 0.007                                # the 0.70 % threshold is in force
    cf.write_text('{"thrust": "big"}'); time.sleep(2.1); w.e.tick(); assert w.e.p.thrust == 0.007                                                          # nonsense keeps the last good value
    cf.write_text('{"thrust": 0.5}'); time.sleep(2.1); w.e.tick(); assert w.e.p.thrust == 0.007                                                           # out of the allowed range


def test_only_the_expiry_day_and_the_two_days_before_are_traded(tmp_path):
    w = World(tmp_path); w.e.chain = lambda s: {"day": DAY, "expiries": {"2026-11-06": {"strikes": [99.0, 100.0, 101.0]}}} if s == "NIFTY" else {}                  # nearest expiry is 3 days away
    assert w.e.tick() == [] and not w.submitted and any(x["kind"] == "skip:dte_above_limit" for x in w.log())
    w2 = World(tmp_path / "w2" if False else tmp_path); w2.e.state = {}; w2.e.chain = lambda s: {"day": DAY, "expiries": {"2026-11-05": {"strikes": [99.0, 100.0, 101.0]}}} if s == "NIFTY" else {}     # 2 days away: allowed
    assert len(w2.e.tick()) == 1
