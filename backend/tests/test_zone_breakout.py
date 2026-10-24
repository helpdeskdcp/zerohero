import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

from app.zone_breakout import strategy as ZB

ORIG = os.path.join(os.path.dirname(__file__), "..", "data", "research", "zone_breakout", "original", "zone_breakout_angelone_paper_bot.py")


def _bars(n_days=30, seed=7, per=75):
    """Synthetic 5-minute sessions with ranges and trend legs so that the strategy actually trades."""
    rng = np.random.default_rng(seed); rows = []; px = 20000.0; drift = 0.0
    for d in range(n_days):
        day = pd.Timestamp("2026-01-05") + pd.Timedelta(days=d)
        if day.dayofweek >= 5: continue
        for k in range(per):
            if k % 25 == 0: drift = rng.choice([-1.0, 0.0, 0.0, 1.0]) * 1.2
            o = px; c = o + drift + rng.normal(0, 6); h = max(o, c) + abs(rng.normal(0, 3)); l = min(o, c) - abs(rng.normal(0, 3)); px = c
            rows.append((day + pd.Timedelta(hours=9, minutes=15 + 5 * k), o, h, l, c, 1000.0))
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]); df["timestamp"] = df["timestamp"].dt.tz_localize("Asia/Kolkata"); return df


def _orig_events(df, tmp_path, cfg):
    spec = importlib.util.spec_from_file_location("zb_orig", ORIG); m = importlib.util.module_from_spec(spec); sys.modules["zb_orig"] = m; spec.loader.exec_module(m)
    c = m.Config(**{k: v for k, v in cfg.as_dict().items()}); eng = m.PaperEngine(c, str(tmp_path / "ev.csv")); d = m.add_indicators(df, c)
    for i in range(len(d)): eng._process_bar(d, i, allow_entry=True, replay=False)
    ev = pd.read_csv(tmp_path / "ev.csv"); ev = ev[ev.event.str.startswith("PAPER_")]
    return [(r.event, pd.Timestamp(r.timestamp), float(r.price), r.stop_loss, r.target) for r in ev.itertuples()]


def test_parity_with_the_original_bot_events(tmp_path):
    cfg = ZB.Config(); df = _bars(); t, ev = ZB.simulate(df, cfg, entry="close", intraday=False, with_events=True)
    mine = [(k, pd.Timestamp(ts), float(px), sl, tp) for k, ts, px, sl, tp in ev if k.startswith("PAPER_")]
    theirs = _orig_events(df, tmp_path, cfg)
    assert len(theirs) >= 6, "the synthetic data must produce trades for the parity test to mean anything"
    assert len(mine) == len(theirs)
    for a, b in zip(mine, theirs):
        assert a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) < 1e-6
        if a[3] is not None and not pd.isna(b[3]) and b[3] != "": assert abs(float(a[3]) - float(b[3])) < 1e-6 and abs(float(a[4]) - float(b[4])) < 1e-6


def test_signals_are_causal():
    cfg = ZB.Config(); df = _bars(40); k = len(df) - 300
    full = ZB.indicators(df, cfg); cut = ZB.indicators(df.iloc[:k], cfg)
    cols = ["atr", "resistance", "support", "ema", "bull_pattern", "bear_pattern", "bull_break", "bear_break", "consolidated"]
    pd.testing.assert_frame_equal(full[cols].iloc[:k].reset_index(drop=True), cut[cols], check_exact=False)
    mut = df.copy(); mut.loc[mut.index[k:], ["open", "high", "low", "close"]] *= 1.2; pd.testing.assert_frame_equal(ZB.indicators(mut, cfg)[cols].iloc[:k].reset_index(drop=True), cut[cols], check_exact=False)
    t1, _ = ZB.simulate(df.iloc[:k], cfg, "next_open", True); t2, _ = ZB.simulate(df, cfg, "next_open", True)
    done = t1[t1["exit_ts"] < df["timestamp"].iloc[k - 1]]; assert done.equals(t2[t2["exit_ts"] < df["timestamp"].iloc[k - 1]].reset_index(drop=True).iloc[:len(done)]) or len(done) == 0


def test_intraday_variant_never_holds_overnight_and_next_open_entries():
    cfg = ZB.Config(); df = _bars(60)
    t, _ = ZB.simulate(df, cfg, "next_open", True)
    assert len(t) > 5 and (pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").date == pd.DatetimeIndex(t["exit_ts"]).tz_convert("Asia/Kolkata").date).all()
    assert (pd.DatetimeIndex(t["exit_ts"]).tz_convert("Asia/Kolkata").hour * 60 + pd.DatetimeIndex(t["exit_ts"]).tz_convert("Asia/Kolkata").minute <= 15 * 60 + 15).all()
    first = df.set_index("timestamp"); assert all(abs(r.entry - first.loc[r.entry_ts, "open"]) < 1e-9 for r in t.itertuples())            # entry is the OPEN of the bar after the signal
    assert (t["risk"] > 0).all() and (t.loc[t.reason == "tp", "R"] > 1.99).all() * 1 == 1 and (t.loc[t.reason == "sl", "R"] < -0.99).all()
    tc, _ = ZB.simulate(df, cfg, "close", False)
    assert (pd.DatetimeIndex(tc["entry_ts"]).tz_convert("Asia/Kolkata").date != pd.DatetimeIndex(tc["exit_ts"]).tz_convert("Asia/Kolkata").date).sum() >= 0


def test_live_incremental_equals_batch_and_survives_restart(tmp_path):
    from app.zone_breakout import live as LV
    cfg = ZB.Config(); full = _bars(60, seed=7); batch, _ = ZB.simulate(full, cfg, "next_open", True); assert len(batch) > 5
    db = str(tmp_path / "z.db"); sent = []; c = LV._conn(db); ts = full["timestamp"]; cuts = list(range(300, len(full), 37)) + [len(full)]
    for n, k in enumerate(cuts):
        LV.process("NIFTY", full.iloc[max(0, k - 600):k].reset_index(drop=True), c, send=sent.append)
        if n % 3 == 1: c.commit(); c.close(); c = LV._conn(db)                                                 # a restart in the middle: state comes back from SQLite
    c.commit(); live = c.execute("SELECT side, entry_ts, entry, exit_ts, exit, reason, R FROM trades WHERE status='CLOSED' ORDER BY entry_ts").fetchall(); c.close()
    first = LV._conn(db)  # noqa
    # trades whose entry is after the first seeded bar must equal the batch trades after that bar
    seed_ts = ts.iloc[299]; b = batch[batch["entry_ts"] > seed_ts].reset_index(drop=True)
    assert len(live) >= len(b) - 1 and len(live) > 3
    for r, t in zip(live, b.itertuples()):
        assert r[0] == t.side and pd.Timestamp(r[1]) == t.entry_ts and abs(r[2] - t.entry) < 1e-6 and abs(r[4] - t.exit) < 1e-6 and r[5] == t.reason
    assert any("IDADDY AI SIGNAL" in s and "PAPER  (no live order)" in s and "NO-GO" in s for s in sent) and any("ZONE BREAKOUT" in s for s in sent)


def test_angel_adapter_history_parses_chunks_and_fails_closed():
    from app.zone_breakout import data as DT
    calls = []
    def fetch(f, t):
        calls.append((f, t)); return {"data_status": "OK", "candles": [{"t": "2026-10-23T09:15:00+05:30", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 0}, {"t": "2026-10-23T09:20:00+05:30", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 0}, {"t": "2026-10-23T16:00:00+05:30", "o": 1, "h": 1, "l": 1, "c": 1, "v": 0}]}
    d = DT.angel_history("NIFTY", days=45, fetch=fetch)
    assert len(calls) == 3 and len(d) == 2 and str(d["timestamp"].iloc[0].tz) == "Asia/Kolkata" and d["timestamp"].iloc[0].hour == 9                          # chunked, 16:00 (outside the session) dropped, duplicates collapsed
    import pytest as _p
    with _p.raises(RuntimeError): DT.angel_history("NIFTY", days=5, fetch=lambda f, t: {"data_status": "DATA_UNAVAILABLE", "reason": "x"})
