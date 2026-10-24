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
