import numpy as np
import pandas as pd

from app.scanner import intraday as I


def _frame(n=80, seed=1):
    rng = np.random.default_rng(seed); c = 100 + np.cumsum(rng.normal(0, 0.3, n)); o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + abs(rng.normal(0, 0.1, n)); l = np.minimum(o, c) - abs(rng.normal(0, 0.1, n))
    return pd.DataFrame({"t": pd.date_range("2026-10-22 03:45", periods=n, freq="5min", tz="UTC"), "o": o, "h": h, "l": l, "c": c, "v": rng.integers(900, 1100, n).astype(float)})


def test_bull_bear_logic_on_constructed_bars():
    df = _frame(); df.loc[60:, "v"] = 1000.0
    i = 70
    # construct a bullish sweep: previous bar low 99.0; this bar dips to 98.8, closes 100.9 above open 99.5, volume spike, RSI forced high by prior up-moves
    for k in range(40, 70): df.loc[k, ["o", "c"]] = [100 + 0.1 * (k - 40), 100 + 0.1 * (k - 40) + 0.12]; df.loc[k, "h"] = df.loc[k, "c"] + 0.05; df.loc[k, "l"] = df.loc[k, "o"] - 0.05
    df.loc[i - 1, ["l"]] = 102.5; df.loc[i, ["o", "l", "h", "c", "v"]] = [102.9, 102.3, 104.0, 103.9, 5000.0]
    f = I.scan_flags(df)
    assert bool(f["A_BULL"].iloc[i]) and bool(f["FULL_BULL"].iloc[i]) and not f["FULL_BEAR"].iloc[i]
    assert f["rsi"].iloc[i] > 55 and f["vol_ratio"].iloc[i] > 1.5
    low_vol = df.copy(); low_vol.loc[i, "v"] = 1000.0; assert not I.scan_flags(low_vol)["FULL_BULL"].iloc[i]                    # volume condition is part of CORE
    mirror = df.copy(); mirror[["o", "h", "l", "c"]] = 200 - df[["o", "l", "h", "c"]].values                                      # price mirror (high<->low)
    fm = I.scan_flags(mirror); assert bool(fm["A_BEAR"].iloc[i]) and bool(fm["FULL_BEAR"].iloc[i]) and not fm["FULL_BULL"].iloc[i]


def test_flags_are_causal():
    df = _frame(120, seed=5); k = 90; full = I.scan_flags(df); cut = I.scan_flags(df.iloc[:k].reset_index(drop=True))
    pd.testing.assert_frame_equal(full.iloc[:k].reset_index(drop=True), cut, check_exact=False)
    mut = df.copy(); mut.loc[k:, ["o", "h", "l", "c"]] *= 1.3; mut.loc[k:, "v"] *= 9; pd.testing.assert_frame_equal(I.scan_flags(mut).iloc[:k].reset_index(drop=True), cut, check_exact=False)


def test_swing_levels_are_confirmed_late_and_causal():
    n = 140; idx = np.arange(n); l = pd.Series(100 + 0.01 * idx); h = pd.Series(101 + 0.01 * (n - idx)); l.iloc[20] = 95.0                      # rising lows (no other swing low), falling highs (no swing high); one swing low at bar 20
    sh, sl = I.swing_levels(h, l, k=3)
    assert sl.iloc[:24].isna().all() and sh.isna().all()                  # swing at 20 is confirmed at bar 23 and usable from bar 24
    assert sl.iloc[24] == 95.0 and sl.iloc[100] == 95.0 and np.isnan(sl.iloc[20 + 3 + 1 + 80 + 3 + 1])                    # expires after maxage
    l2 = l.copy(); l2.iloc[60:] = 50.0; sh2, sl2 = I.swing_levels(h, l2, k=3)
    assert (sl2.iloc[:60].fillna(-1) == sl.iloc[:60].fillna(-1)).all()   # later bars never change earlier levels


def test_sw_variants_fire_on_a_swing_sweep():
    n = 100; rng = np.random.default_rng(2); idx = np.arange(n); c = 100 + 0.1 * idx + rng.normal(0, 0.0, n); o = c - 0.12
    df = pd.DataFrame({"t": pd.date_range("2026-10-22 03:45", periods=n, freq="5min", tz="UTC"), "o": o, "h": c + 0.05, "l": o - 0.05, "c": c, "v": 1000.0})
    df.loc[70, "l"] = 99.0                                                # one clean swing low (all neighbours are higher, lows rise with the trend)
    i = 95; df.loc[i, ["o", "l", "h", "c", "v"]] = [df.loc[i, "o"], 98.0, df.loc[i, "c"] + 0.6, df.loc[i, "c"] + 0.5, 6000.0]                       # sweeps below 99 and closes far above it
    f = I.scan_flags(df)
    assert abs(f["swing_low"].iloc[i] - 99.0) < 1e-9 and bool(f["SW_A_BULL"].iloc[i]) and bool(f["SW_FULL_BULL"].iloc[i]) and not f["SW_FULL_BEAR"].iloc[i]


def test_rsi_is_100_without_losses_and_matches_wilder_otherwise():
    up = pd.Series(np.arange(1.0, 41.0)); r = I.rsi(up, 14); assert r.iloc[:13].isna().all() and (r.iloc[14:] == 100.0).all()
    flat = pd.Series(np.full(40, 5.0)); assert (I.rsi(flat, 14).iloc[14:] == 50.0).all()
    rng = np.random.default_rng(1); x = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    d = x.diff(); g = d.clip(lower=0); ls = -d.clip(upper=0); ag, al = g.iloc[1:15].mean(), ls.iloc[1:15].mean()                      # Wilder seed = simple mean of the first 14 changes, then recursive smoothing
    for i in range(15, 200): ag = (ag * 13 + g.iloc[i]) / 14; al = (al * 13 + ls.iloc[i]) / 14
    assert abs(I.rsi(x, 14).iloc[-1] - (100 - 100 / (1 + ag / al))) < 1.0                 # ewm(alpha=1/14) with adjust=False differs from the textbook seed only by a decayed start-up term
