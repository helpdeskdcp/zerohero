"""Safe U-turn overlay for the Terminal's underlying chart: markers from the stored index prices of the day + the calibration base rate; failure never breaks the chart."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.arena.safe_ai import chart as UC
from app.histcap.store import HistStore

IST = timezone(timedelta(hours=5, minutes=30))
DAY = "2026-11-03"
T0 = datetime.fromisoformat(DAY).replace(tzinfo=IST).replace(hour=9, minute=15).timestamp()


def seed(tmp_path, prices):
    st = HistStore(str(tmp_path / "m.db")); rid = st.start_run("T", {}, True); rows = []
    for k, (t, px) in enumerate(prices):
        rec = datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
        rows.append({"received_ts": rec, "exch_ts": None, "snap_key": f"k{k}", "instrument_key": "BSE:99919000", "symbol": "SENSEX", "kind": "INDEX", "exchange": "BSE", "token": "99919000", "session_date_ist": DAY, "ltp": px, "source": "ANGELONE_QUOTE_FULL", "quote_status": "OK"})
    with st.independent_transaction() as c: st.write_quotes(c, rows, rid, {"issues": [], "rejected": [], "flagged": 0})
    return str(tmp_path / "m.db")


def uturn_prices():
    down = [(T0 + 30 * k, 100000 * (1 - 0.007 * k / 80)) for k in range(0, 81)]                  # 40 minutes down 0.70 %
    up = [(T0 + 30 * (80 + k), 100000 * (1 - 0.007) + 350 * k / 20) for k in range(1, 21)]         # then back half of it in 10 minutes
    return down + up


@pytest.fixture(autouse=True)
def calib(tmp_path, monkeypatch):
    f = tmp_path / "CALIBRATION.json"; f.write_text(json.dumps({"basis": "test", "overall": {"n": 67, "target_hit": 0.6, "target_hit_ci95": [0.48, 0.71], "net_per_trade": 20.0}, "note": "base rate",
                                                                   "by_thrust": [{"label": "0.60-0.80%", "n": 20, "target_hit": 0.65, "target_hit_ci95": [0.43, 0.82], "net_per_trade": 90.0}]}))
    monkeypatch.setattr(UC, "CALIB", str(f)); UC._cache.clear()


def test_a_confirmed_uturn_today_becomes_one_marker_with_its_base_rate(tmp_path):
    path = seed(tmp_path, uturn_prices()); now = T0 + 30 * 100 + 5
    ov = UC.overlay("SENSEX", now=now, path=path)
    assert len(ov["markers"]) == 1 and ov["markers"][0]["side"] == "CE" and 0.65 <= ov["markers"][0]["thrust_pct"] <= 0.72
    br = ov["markers"][0]["base_rate"]; assert br["n"] == 20 and br["target_hit"] == 0.65                               # the 0.60-0.80 % bucket
    assert ov["calibration"]["overall"]["n"] == 67 and ov["ticks"] >= 90


def test_no_markers_on_a_flat_day_and_the_result_is_cached(tmp_path):
    path = seed(tmp_path, [(T0 + 30 * k, 100000 + (k % 3)) for k in range(120)]); now = T0 + 30 * 120
    a = UC.overlay("SENSEX", now=now, path=path); assert a["markers"] == [] and UC.overlay("SENSEX", now=now + 1, path=path) is a


def test_terminal_payload_carries_the_overlay_and_survives_its_failure(tmp_path, monkeypatch):
    from app.arena import terminal as TM
    monkeypatch.setattr(UC, "overlay", lambda *a, **k: {"markers": [{"time": 1000, "side": "PE", "thrust_pct": 0.5, "reason": "x", "base_rate": None}], "calibration": {"basis": "b"}})
    out = TM._with_uturn({"candles": []}, "SENSEX", None, None); assert out["uturn"][0]["time"] == 1000 + TM.IST_S and out["uturn_calibration"] == {"basis": "b"}
    monkeypatch.setattr(UC, "overlay", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    out = TM._with_uturn({"candles": [1]}, "SENSEX", None, None); assert out["uturn"] == [] and out["uturn_calibration"] == {"error": "RuntimeError"} and out["candles"] == [1]
