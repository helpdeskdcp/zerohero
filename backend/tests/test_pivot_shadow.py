import numpy as np
import pandas as pd

from app.autoscalp import pivot_reversal_shadow as PS


def _bars_1m(days=4, seed=2):
    rng = np.random.default_rng(seed); rows = []; px = 22000.0
    for d in range(days):
        day = pd.Timestamp("2026-10-19") + pd.Timedelta(days=d)
        if day.dayofweek >= 5: continue
        for k in range(375):
            t = (day + pd.Timedelta(hours=9, minutes=15 + k)).tz_localize("Asia/Kolkata").tz_convert("UTC"); c = px + rng.normal(0, 2); rows.append((int(t.timestamp()), px, max(px, c) + 1, min(px, c) - 1, c)); px = c
    a = np.array(rows); return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "v": np.zeros(len(a))}


def test_five_minute_only_complete_bins():
    b = _bars_1m(); now = float(b["t"][-1] + 60)
    df = PS.five_minute(b, now - 120)                                  # two minutes before the last bar ended: the final 5m bin is incomplete or missing
    assert (df["t"].iloc[-1] + pd.Timedelta(minutes=5)).timestamp() <= now - 120 and len(df) > 150
    assert PS.five_minute({"t": np.array([]), "o": [], "h": [], "l": [], "c": []}, now).empty


def test_observe_logs_once_fills_forward_and_respects_universe(tmp_path, monkeypatch):
    from app.candle_ai import pivot_reversal as PR
    b = _bars_1m(); now = float(b["t"][-1] + 61); db = str(tmp_path / "p.db"); sent = []
    prov = lambda sym, kind, until, days=6: {k: v[b["t"] + 60 <= until] for k, v in b.items()}
    def fake_signals(df):
        out = pd.DataFrame({v: False for v in PR.VARIANTS}, index=df.index); out.loc[df.index[-5], "ENGULF_BUY"] = False; out.loc[df.index[-1], "HARAMI_SELL"] = True; return out
    monkeypatch.setattr(PR, "signals", fake_signals); PS._last_bar.clear()
    new = PS.observe("NIFTY", now, prov, db, send=sent.append)
    assert len(new) == 1 and new[0]["variant"] == "HARAMI_SELL" and new[0]["side"] == "SELL" and len(sent) == 1 and "no demonstrated edge" in sent[0]
    assert PS.observe("NIFTY", now, prov, db, send=sent.append) == [] and len(sent) == 1                  # same bar: not logged / announced twice
    assert PS.observe("NATURALGAS", now, prov, db) == []                                               # outside the index universe
    # forward fill: shift time forward by 15 five-minute bars -> same-session? use a later 'now' inside the same day only if the bar is early enough; otherwise fills stay NULL
    import sqlite3
    c = sqlite3.connect(db); r = c.execute("SELECT fwd3, fwd6, fwd12 FROM pivot_signals").fetchone(); assert r == (None, None, None)       # last bar of the session has no later bar
    c.execute("UPDATE pivot_signals SET bar_start=?", (pd.Timestamp(PS.five_minute(b, now)["t"].iloc[-30]).strftime("%Y-%m-%dT%H:%M:%SZ"),)); c.commit(); c.close()
    PS._last_bar.clear(); PS.observe("NIFTY", now, prov, db, send=sent.append)
    r = sqlite3.connect(db).execute("SELECT fwd3, fwd6, fwd12 FROM pivot_signals WHERE bar_start=(SELECT MIN(bar_start) FROM pivot_signals)").fetchone(); assert all(x is not None for x in r)


def test_range_zone_signals_logged_with_range_and_state_view(tmp_path, monkeypatch):
    from app.candle_ai import pivot_reversal as PR, range_zone as RZ
    b = _bars_1m(); now = float(b["t"][-1] + 61); db = str(tmp_path / "r.db"); sent = []
    prov = lambda sym, kind, until, days=6: {k: v[b["t"] + 60 <= until] for k, v in b.items()}
    def fake_rz(df, require_touches=True):
        out = pd.DataFrame({"rh": 22100.0, "rl": 22050.0, "mid": 22075.0, "atr": 4.0, "valid": True}, index=df.index)
        for v in RZ.VARIANTS: out[v] = False
        out.loc[df.index[-1], "BRK_UP"] = True; return out
    monkeypatch.setattr(RZ, "signals", fake_rz); monkeypatch.setattr(PR, "signals", lambda df: pd.DataFrame({v: False for v in PR.VARIANTS}, index=df.index)); PS._last_bar.clear()
    new = PS.observe("BANKNIFTY", now, prov, db, send=sent.append)
    assert [n["variant"] for n in new] == ["RZ_BRK_UP"] and new[0]["rh"] == 22100.0 and "range 22050.00-22100.00" in sent[0]
    monkeypatch.setattr(RZ, "box", lambda df, require_touches=True: fake_rz(df)); st = PS.current_state("NIFTY", now, prov)
    assert st["status"] == "RANGE" and st["range_high"] == 22100.0 and st["position"] in ("ABOVE_RANGE", "BELOW_RANGE", "INSIDE", "UPPER_ZONE", "LOWER_ZONE") and PS.current_state("NIFTY", now, lambda *a, **k: {"t": np.array([]), "o": [], "h": [], "l": [], "c": []})["status"] == "NO_DATA"
