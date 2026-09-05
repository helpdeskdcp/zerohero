"""
Order-flow smart-money (volume-spike breakout) engine + Telegram dedup.

Spec (operator-confirmed 2026-09-05):
  spike = bar volume >= volume_mult x session-average bar volume
  BUY  : entry = spike HIGH, stop = spike LOW,  target = entry + rr*(H-L)
  SELL : entry = spike LOW,  stop = spike HIGH, target = entry - rr*(H-L)
  outcome walked forward through the same session's remaining bars; a bar
  spanning BOTH target and stop -> STOP_HIT (pessimistic).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.orderflow import smart_money as SM   # noqa: E402


def _bar(bs, o, h, l, c, v):
    return {"bar_start": bs, "o": o, "h": h, "l": l, "c": c, "v": v}


# ---------------------------------------------------------------- detection + math
def test_spike_detected_and_buy_sell_math():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 102, 98, 101, 100),
        _bar("t2", 101, 110, 100, 108, 1000),   # spike: v=1000 vs avg ~ (100+100+1000+100+100)/5 = 280
        _bar("t3", 108, 109, 107, 108, 100),
        _bar("t4", 108, 109, 107, 108, 100),
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0, rr=3.0)
    assert out["status"] == "OK"
    assert out["spike_count"] == 1
    s = out["setups"][0]
    assert s["candle"]["bar_start"] == "t2"
    assert s["volume_x_avg"] == round(1000 / 280.0, 2)
    assert s["range_points"] == 10.0        # 110 - 100

    buy = s["buy"]
    assert buy["side"] == "BUY"
    assert buy["entry"] == 110.0 and buy["stop_loss"] == 100.0
    assert buy["risk_points"] == 10.0
    assert buy["target"] == 110.0 + 3 * 10.0          # 140
    assert buy["rr"] == 3.0

    sell = s["sell"]
    assert sell["entry"] == 100.0 and sell["stop_loss"] == 110.0
    assert sell["target"] == 100.0 - 3 * 10.0          # 70
    assert sell["rr"] == 3.0


def test_buy_outcome_pending_when_no_breakout():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 105, 100, 102, 1000),   # spike, high 105
        _bar("t3", 102, 104, 101, 103, 100),    # never trades > 105 -> BUY pending
        _bar("t4", 103, 104, 101, 103, 100),
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0)
    s = out["setups"][0]
    assert s["buy"]["outcome"]["status"] == "PENDING"
    assert s["buy"]["breakout_bar"] is None


def test_buy_outcome_target_hit():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 110, 100, 105, 1000),   # spike H110 L100 -> BUY entry 110, tgt 110+3*10=140
        _bar("t3", 108, 112, 107, 111, 100),    # breaks 110
        _bar("t4", 111, 141, 110, 140, 100),    # hits 140 (not <=100) -> TARGET_HIT
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0)
    buy = out["setups"][0]["buy"]
    assert buy["breakout_bar"] == "t3"
    assert buy["outcome"]["status"] == "TARGET_HIT"
    assert buy["outcome"]["resolved_bar"] == "t4"


def test_buy_outcome_stop_hit():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 110, 100, 105, 1000),   # spike -> BUY entry 110, stop 100
        _bar("t3", 108, 111, 107, 110, 100),    # breaks out (high 111 > 110)
        _bar("t4", 109, 109, 99, 100, 100),     # low 99 <= 100 -> STOP_HIT
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0)
    buy = out["setups"][0]["buy"]
    assert buy["outcome"]["status"] == "STOP_HIT"
    assert buy["outcome"]["resolved_bar"] == "t4"


def test_outcome_bar_spanning_both_is_stop_hit():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 110, 100, 105, 1000),   # BUY entry 110, stop 100, tgt 140
        _bar("t3", 110, 145, 95, 120, 100),     # high 145 >= 140 AND low 95 <= 100
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0)
    oc = out["setups"][0]["buy"]["outcome"]
    assert oc["status"] == "STOP_HIT"
    assert "pessimistic" in oc["note"]


def test_sell_outcome_target_hit():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 105, 110, 100, 103, 1000),   # spike H110 L100 -> SELL entry 100, tgt 100-3*10=70
        _bar("t3", 101, 102, 98, 99, 100),      # breaks 100 (low 98 < 100)
        _bar("t4", 90, 92, 69, 70, 100),        # low 69 <= 70 -> TARGET_HIT
    ]
    out = SM.smart_money_setups(bars, volume_mult=2.0)
    sell = out["setups"][0]["sell"]
    assert sell["breakout_bar"] == "t3"
    assert sell["outcome"]["status"] == "TARGET_HIT"


def test_no_spike_no_setups():
    bars = [_bar(f"t{i}", 100, 101, 99, 100, 100) for i in range(6)]
    out = SM.smart_money_setups(bars, volume_mult=3.0)
    assert out["status"] == "OK"
    assert out["spike_count"] == 0
    assert out["setups"] == []


def test_no_data_below_three_bars():
    assert SM.smart_money_setups([], volume_mult=2.0)["status"] == "NO_DATA"
    assert SM.smart_money_setups([_bar("t0", 1, 2, 0, 1, 10),
                                 _bar("t1", 1, 2, 0, 1, 10)],
                                volume_mult=2.0)["status"] == "NO_DATA"


def test_higher_volume_mult_filters_out_marginal_spikes():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 105, 100, 102, 600),    # ~2.1x avg -> spike at 2.0, NOT at 5.0
        _bar("t3", 102, 103, 101, 102, 100),
        _bar("t4", 102, 103, 101, 102, 100),
    ]
    assert SM.smart_money_setups(bars, volume_mult=2.0)["spike_count"] == 1
    assert SM.smart_money_setups(bars, volume_mult=5.0)["spike_count"] == 0


# ---------------------------------------------------------------- notify + dedup
def _fresh_db(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "t.db"))
    import importlib, app.db as db
    importlib.reload(db)
    db.init_db()
    return db


def _broken_out_result():
    bars = [
        _bar("t0", 100, 101, 99, 100, 100),
        _bar("t1", 100, 101, 99, 100, 100),
        _bar("t2", 100, 110, 100, 105, 1000),
        _bar("t3", 108, 112, 107, 111, 100),   # BUY breaks out
        _bar("t4", 111, 111, 108, 110, 100),   # TRIGGERED (no resolution)
    ]
    return SM.smart_money_setups(bars, volume_mult=2.0)


def test_notify_only_sends_broken_out_setups_and_dedupes(monkeypatch):
    _fresh_db(monkeypatch)
    import importlib
    import app.orderflow.notify as notify
    importlib.reload(notify)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)   # -> no real send, dry marking

    sm = _broken_out_result()
    r1 = notify.push_new_signals("NIFTY", "2026-09-05", sm, dry_run=True)
    # BUY broke out (TRIGGERED); SELL never broke out (PENDING) -> only 1 considered as signal
    assert r1["considered"] == 1
    assert r1["sent"] == 1

    # real (non-dry) call with no creds: marks handled so the log doesn't spam
    r2 = notify.push_new_signals("NIFTY", "2026-09-05", sm, dry_run=False)
    assert r2["new_marked"] == 1

    # third call: already marked -> nothing new
    r3 = notify.push_new_signals("NIFTY", "2026-09-05", sm, dry_run=False)
    assert r3["new_marked"] == 0 and r3["sent"] == 0


def test_notify_skips_when_status_not_ok(monkeypatch):
    _fresh_db(monkeypatch)
    import importlib, app.orderflow.notify as notify
    importlib.reload(notify)
    r = notify.push_new_signals("NIFTY", "2026-09-05", {"status": "NO_DATA"})
    assert r["sent"] == 0 and r.get("skipped_status") == "NO_DATA"


def test_notify_suppresses_stale_backlog_but_marks_handled(monkeypatch):
    """A breakout from hours ago must NOT be sent (not actionable) but must be
    marked so a later run doesn't fire it either."""
    _fresh_db(monkeypatch)
    import importlib, app.orderflow.notify as notify
    importlib.reload(notify)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    def _mk(breakout_ts):
        return {"status": "OK", "setups": [{
            "candle": {"bar_start": breakout_ts, "o": 100, "h": 110, "l": 100, "c": 105, "v": 1000},
            "volume_x_avg": 3.0, "range_points": 10.0,
            "buy": {"side": "BUY", "entry": 110, "stop_loss": 100, "target": 140,
                    "risk_points": 10, "reward_points": 30, "rr": 3.0,
                    "breakout_bar": breakout_ts, "outcome": {"status": "TRIGGERED"}},
            "sell": {"side": "SELL", "entry": 100, "stop_loss": 110, "target": 70,
                     "risk_points": 10, "reward_points": 30, "rr": 3.0,
                     "breakout_bar": None, "outcome": {"status": "PENDING"}},
        }]}

    r_old = notify.push_new_signals("NIFTY", "2026-09-05", _mk(old))
    assert r_old["sent"] == 0
    assert r_old["suppressed_stale"] == 1
    assert r_old["new_marked"] == 1          # still marked so it never fires later

    r_recent = notify.push_new_signals("NIFTY", "2026-09-05", _mk(recent))
    assert r_recent["suppressed_stale"] == 0
    assert r_recent["new_marked"] == 1       # recent one goes through the normal path
