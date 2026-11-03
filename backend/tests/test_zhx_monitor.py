import json
from types import SimpleNamespace as NS

from app.arena.safe_ai import zhx_monitor as Z


def chain(spot=23400.0, dte=1, bid=100.0, ask=101.0, iv=0.14):
    leg = lambda: NS(bid=bid, ask=ask, ltp=(bid + ask) / 2, iv=iv, delta=0.5, oi=1e6, oi_change=1e4, volume=5e4)
    return NS(underlying="NIFTY", expiry="04NOV2026", ts="2026-11-04T10:00:00+05:30", source="angelone_captured", spot=spot, rows=[NS(strike=23400.0, ce=leg(), pe=leg()), NS(strike=23450.0, ce=leg(), pe=leg())], expiry_ctx={"dte": dte})


def Qv(verdict, blocking=(), watch=()):
    return lambda ch, **k: NS(to_dict=lambda: {"verdict": verdict, "direction": "LONG", "blocking": list(blocking), "watch_reasons": list(watch), "gates": [{"name": "g1", "status": "PASS"}], "summary": "s"})


def Aw(res=None, sup=None):
    walls = NS(to_dict=lambda: {"nearest_resistance": res, "nearest_support": sup, "ce_wall": res, "pe_wall": sup})
    return lambda ch: {"oi_walls": walls, "pcr": NS(pcr_oi=0.9), "max_pain": NS(max_pain=23300), "weighted_iv": 0.14}


SIG = {"side": 1, "bar_time": 1_790_000_000.0, "entry": 23400.0, "sl": 23360.0, "t1": 23470.0, "t2": 23520.0, "t3": 23600.0, "score": 7.0}


def test_take_only_when_the_chain_is_qualified_and_every_hard_gate_passes():
    d = Z.decide("NIFTY", SIG, chain(), Qv("QUALIFIED"), Aw())
    assert d["decision"] == "TAKE" and d["option"]["type"] == "CE" and d["option"]["strike"] == 23400.0 and d["option"]["spread_pct"] == 1.0 and d["reasons"] == []


def test_wide_spread_low_premium_or_far_expiry_skip_the_signal_whatever_the_chart_says():
    assert Z.decide("NIFTY", SIG, chain(bid=100, ask=108), Qv("QUALIFIED"), Aw())["decision"] == "SKIP"           # spread 7.7 %
    assert Z.decide("NIFTY", SIG, chain(bid=10, ask=10.2), Qv("QUALIFIED"), Aw())["decision"] == "SKIP"           # premium below 30
    d = Z.decide("NIFTY", SIG, chain(dte=56), Qv("QUALIFIED"), Aw()); assert d["decision"] == "SKIP" and any("dte 56" in r for r in d["reasons"])      # the monthly-option case (BANKNIFTY)
    assert Z.decide("NIFTY", SIG, None)["decision"] == "SKIP"


def test_an_oi_wall_between_entry_and_t1_or_a_non_qualified_chain_downgrades_to_watch():
    d = Z.decide("NIFTY", SIG, chain(), Qv("QUALIFIED"), Aw(res=23440.0)); assert d["decision"] == "WATCH" and any("OI wall" in r for r in d["reasons"])
    d2 = Z.decide("NIFTY", SIG, chain(), Qv("WATCH", watch=["pcr neutral"]), Aw()); assert d2["decision"] == "WATCH"
    d3 = Z.decide("NIFTY", {**SIG, "side": -1, "t1": 23330.0}, chain(), Qv("QUALIFIED"), Aw(sup=23350.0)); assert d3["decision"] == "WATCH" and d3["option"]["type"] == "PE"


def test_tick_logs_one_row_per_signal_bar_submits_nothing_and_survives_errors(tmp_path, monkeypatch):
    log = str(tmp_path / "zhx.jsonl"); monkeypatch.setattr(Z, "decide", lambda sym, sig, ch, **k: {"symbol": sym, "signal": sig, "decision": "WATCH", "reasons": []})
    rows = Z.tick(("NIFTY", "SENSEX"), now=1_790_000_100.0, log_path=log, get_chain=lambda s: None, signal_fn=lambda s, now=None: SIG if s == "NIFTY" else (_ for _ in ()).throw(RuntimeError("boom")))
    assert [r.get("symbol") for r in rows] == ["NIFTY", "SENSEX"] and "error" in rows[1] and rows[0]["mode"].startswith("SHADOW")
    assert len(open(log).read().strip().split("\n")) == 1


def test_latest_signal_reads_the_last_two_confirmed_bars_and_ignores_stale_ones():
    import pandas as pd
    ts = pd.date_range("2026-11-04 09:15", periods=200, freq="5min", tz="Asia/Kolkata"); d = pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0.0})
    n = len(d); vals = lambda k: [1 if (i == k) else 0 for i in range(n)]; col = lambda v: [v if i == n - 1 else None for i in range(n)]
    res = {"ok": True, "plots": [{"title": "ZHX Signal", "values": vals(n - 1)}, {"title": "ZHX Entry", "values": col(23400.0)}, {"title": "ZHX SL", "values": col(23360.0)}, {"title": "ZHX T1", "values": col(23470.0)}, {"title": "ZHX T2", "values": col(23520.0)}, {"title": "ZHX T3", "values": col(23600.0)}, {"title": "ZHX Score", "values": col(7.0)}]}
    last_close = ts[-1].timestamp() + 300
    s = Z.latest_signal("NIFTY", now=last_close + 60, bars=d, execute=lambda *a, **k: res); assert s and s["side"] == 1 and s["entry"] == 23400.0 and s["t3"] == 23600.0 and s["score"] == 7.0
    assert Z.latest_signal("NIFTY", now=last_close + 3600, bars=d, execute=lambda *a, **k: res) is None            # an hour old: not actionable
