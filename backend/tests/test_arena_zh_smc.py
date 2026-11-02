from app.arena import zh_smc as Z

TIMES = [1_790_000_000_000 + i * 300_000 for i in range(50)]


def _res():
    n = len(TIMES); vals = lambda *idx: [True if i in idx else None for i in range(n)]
    return {"ok": True, "plots": [{"title": "BUY CE Confirmed", "values": vals(10)}, {"title": "BUY PE Confirmed", "values": vals(20)}, {"title": "BUY PE (option chart)", "values": vals(30)},
                                  {"title": "Exit SL", "values": vals(25)}, {"title": "Exit EOD", "values": vals(40)}, {"title": "Bull BOS", "values": vals(3)}],
            "drawings": {"labels": [{"text": "ENTRY 100.5", "x": n + 7, "y": 100.5, "xloc": "bar_index"}, {"text": "SL 95", "x": n + 7, "y": 95.0, "xloc": "bar_index"}, {"text": "T1 HIT 105", "x": n + 7, "y": 105.0, "xloc": "bar_index"},
                                       {"text": "BUY CE\n2026\nScore", "x": 10, "y": 99.0, "xloc": "bar_index"}, {"text": "SSL", "x": n + 7, "y": 1.0, "xloc": "bar_time"}],
                         "tables": [{"rows": 5, "columns": 3, "cells": [[{"text": "ZH"}, {"text": "CE"}, {"text": "PE"}]] + [[{"text": "r"}, {"text": "a"}, {"text": "b"}]] * 4}, {"rows": 12, "columns": 2, "cells": [[{"text": "HIST"}, {"text": "RES"}]] + [[{"text": "Closed"}, {"text": "3"}]] * 11}]}}


def test_shape_returns_only_entry_and_exit_markers_on_the_chart_axis_and_the_live_levels():
    s = Z.shape(_res(), TIMES)
    assert [(m["side"], m["kind"]) for m in s["markers"]] == [("BUY CE", "buy"), ("BUY PE", "buy"), ("SL", "exit"), ("BUY PE", "buy"), ("EOD", "exit")]      # BOS (structure) is not a signal marker
    assert s["markers"][0]["time"] == TIMES[10] // 1000 + 19800 and s["markers"] == sorted(s["markers"], key=lambda m: m["time"])
    assert [(l["head"], l["price"]) for l in s["levels"]] == [("ENTRY", 100.5), ("SL", 95.0), ("T1", 105.0)]              # only the right-side tags of the live trade, not the historical detail labels
    assert s["dash"][0][0] == "ZH" and len(s["perf"]) == 3


def test_view_never_raises_and_reports_a_clear_error_when_there_are_too_few_candles(monkeypatch):
    monkeypatch.setattr(Z, "_candles_underlying", lambda *a, **k: [])
    out = Z.view("NIFTY", 5, None, now=1.0, execute=lambda *a, **k: {})
    assert out["markers"] == [] and "not enough candles" in out["error"]


def test_view_passes_the_chart_kind_and_drops_the_5m_gate_for_15m(monkeypatch):
    cs = [{"time": 1_790_000_000_000 + i * 300_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0.0} for i in range(60)]
    seen = {}
    monkeypatch.setattr(Z, "_candles_underlying", lambda *a, **k: cs); monkeypatch.setattr(Z, "_candles_option", lambda *a, **k: cs)
    def ex(req, **k): seen.update(req); return {"ok": True, "plots": [], "drawings": {}}
    Z.view("NIFTY", 15, None, now=1.0, execute=ex); assert seen["inputs"] == {"chartKind": "Underlying / Index", "require5m": False} and seen["tf"] == "15"
    Z.view("NIFTY", 5, ("2026-11-05", 23250.0, "PE"), now=1.0, execute=ex); assert seen["inputs"] == {"chartKind": "Option PE chart"} and seen["symbol"].endswith("23250PE")


def test_a_failed_pine_run_is_reported_not_raised(monkeypatch):
    cs = [{"time": 1_790_000_000_000 + i * 300_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0.0} for i in range(60)]
    monkeypatch.setattr(Z, "_candles_underlying", lambda *a, **k: cs)
    out = Z.view("NIFTY", 5, None, now=1.0, execute=lambda *a, **k: {"ok": False, "diagnostics": [{"message": "boom"}]}); assert out["error"] == "boom" and out["markers"] == []
