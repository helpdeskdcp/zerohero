import json
from datetime import datetime

from app.arena import mohit_view as MV


def _sig(i, day, t="10:00"):
    return {"id": i, "participant": "INSTITUTION_TRADER", "underlying": "SENSEX", "expiry": "2026-11-05", "strike": 73800.0, "option_type": "PE", "entry_mode": "LIMIT", "entry": 450.0, "entry_hi": 450.0, "sl": 300.0,
            "targets": [580.0, 670.0, 800.0], "t": datetime.fromisoformat(f"{day}T{t}:00+05:30").timestamp(), "lots": 2}


def test_the_row_has_date_time_entry_sl_and_all_three_targets():
    r = MV._row(_sig("H1", "2026-10-29", "09:18"), {"status": "SETTLED", "fill": 449, "exit": 300, "exit_reason": "SL", "net_rs": -6042, "hold_sec": 100, "fill_t": 1})
    assert (r["date"], r["time"], r["entry"], r["sl"], r["t1"], r["t2"], r["t3"]) == ("2026-10-29", "09:18:00", 450.0, 300.0, 580.0, 670.0, 800.0) and r["result"]["net_rs"] == -6042


def test_a_day_the_owner_excluded_from_learning_is_not_listed(monkeypatch, tmp_path):
    f = tmp_path / "ex.json"; f.write_text(json.dumps({"INSTITUTION_TRADER": {"days": ["2026-10-30"]}}))
    monkeypatch.setattr(MV.R, "human_signals", lambda **k: [_sig("A", "2026-10-29"), _sig("B", "2026-10-30"), {**_sig("C", "2026-10-29"), "participant": "OTHER"}])
    assert [s["id"] for s in MV._signals(str(f))] == ["A"]
    assert MV.chart_for("B", 1, str(tmp_path / "c"), str(f)) is None                       # an excluded signal cannot be charted by id either


def test_a_cached_past_day_chart_is_served_without_touching_the_capture_db(monkeypatch, tmp_path):
    f = tmp_path / "ex.json"; f.write_text("{}")
    monkeypatch.setattr(MV.R, "human_signals", lambda **k: [_sig("A", "2026-10-20")])
    calls = []
    monkeypatch.setattr(MV.TM, "premium", lambda *a, **k: (calls.append(1), {"candles": [{"time": 9 * 3600 + 15 * 60 + 19800 - 19800 + 60, "open": 1, "high": 2, "low": 1, "close": 2}]})[1])
    out = MV.chart_for("A", 1, str(tmp_path / "c"), str(f)); assert out["candles"] and calls == [1]
    monkeypatch.setattr(MV.TM, "premium", lambda *a, **k: (_ for _ in ()).throw(AssertionError("capture DB must not be read again")))
    assert MV.chart_for("A", 1, str(tmp_path / "c"), str(f))["signal"]["id"] == "A"
