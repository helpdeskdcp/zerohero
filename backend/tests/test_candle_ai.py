import numpy as np
import pandas as pd

from app.candle_ai import features as F, levels as L, patterns as P


def _bars(days=30, per=75, seed=3, vol=True):
    rng = np.random.default_rng(seed); rows = []; px = 20000.0
    for d in range(days):
        day = pd.Timestamp("2026-01-01") + pd.Timedelta(days=d)
        if day.dayofweek >= 5: continue
        for k in range(per):
            t = (day + pd.Timedelta(hours=9, minutes=15 + 5 * k)).tz_localize("Asia/Kolkata").tz_convert("UTC")
            o = px; c = o + rng.normal(0, 8); h = max(o, c) + abs(rng.normal(0, 4)); l = min(o, c) - abs(rng.normal(0, 4)); px = c
            rows.append((t, o, h, l, c, float(rng.integers(100, 1000)) if vol else 0.0))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"])


def test_features_are_causal_future_bars_never_change_earlier_rows():
    df = _bars(); k = len(df) - 40
    full = F.build(df); cut = F.build(df.iloc[:k].reset_index(drop=True))
    a = full.iloc[:k].reset_index(drop=True); b = cut
    pd.testing.assert_frame_equal(a, b, check_exact=False, atol=1e-9)
    mut = df.copy(); mut.loc[k:, ["o", "h", "l", "c"]] *= 1.5; mut.loc[k:, "v"] *= 9                          # rewrite the future
    pd.testing.assert_frame_equal(F.build(mut).iloc[:k].reset_index(drop=True), b, check_exact=False, atol=1e-9)


def test_previous_period_levels_use_only_completed_periods():
    df = _bars(days=40); lev = L.previous_period_levels(df); date = L.session_date(df["t"])
    d = pd.DataFrame({"date": date, "h": df.h, "l": df.l, "c": df.c}).groupby("date").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    day2 = d.index[3]; prev = d.iloc[2]; rows = lev[date == day2]
    assert (rows["PDH"] == prev.h).all() and (rows["PDL"] == prev.l).all() and (rows["PDC"] == prev.c).all()
    p = (prev.h + prev.l + prev.c) / 3; assert np.allclose(rows["FD_P"], p) and np.allclose(rows["FD_R1"], p + 0.382 * (prev.h - prev.l)) and np.allclose(rows["FD_S3"], p - (prev.h - prev.l))
    assert lev[date == d.index[0]]["PDH"].isna().all()                                                         # first day has no previous day
    first_week = date.dt.to_period("W") == d.index[0].to_period("W"); assert lev[first_week]["PWH"].isna().all()   # no previous week yet
    mid = d.index[d.index.to_period("M") == d.index[-1].to_period("M")][0]
    pm = d[d.index.to_period("M") == (d.index[-1].to_period("M") - 1)]
    assert (lev[date == mid]["PMH"] == pm.h.max()).all() and (lev[date == mid]["PML"] == pm.l.min()).all()


def test_patterns_engulfing_hammer_and_inside():
    o = np.array([100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 104, 101.0]); c = o.copy()
    h = o + 5; l = o - 5
    c[:] = o + 0.5 * np.array([1, -1] * 8)                                                                       # noise
    # bar 14: bearish ; bar 15: bullish engulfing it
    o[14], c[14], h[14], l[14] = 104, 101, 104.5, 100.5
    o[15], c[15], h[15], l[15] = 100.5, 105, 105.5, 100
    f = P.detect(o, h, l, c); assert f["engulf_bull"].iloc[15] == 1 and f["engulf_bear"].iloc[15] == 0
    o2 = np.array([100.0] * 20); h2 = o2 + 4; l2 = o2 - 4; c2 = o2 + np.where(np.arange(20) % 2, 1, -1) * 1.0
    o2[19], c2[19], h2[19], l2[19] = 100, 100.4, 100.5, 91                                                      # long lower wick, small body at top
    assert P.detect(o2, h2, l2, c2)["hammer_shape"].iloc[19] == 1
    o3, c3, h3, l3 = o2.copy(), c2.copy(), h2.copy(), l2.copy(); h3[19], l3[19] = h3[18] - 1, l3[18] + 1; o3[19], c3[19] = 100, 100.2
    assert P.detect(o3, h3, l3, c3)["inside_bar"].iloc[19] == 1


def test_target_never_crosses_the_overnight_gap():
    df = _bars(days=6); t = F.target(df); date = L.session_date(df["t"]).values
    last = np.r_[date[1:] != date[:-1], True]
    assert t["ret_bps"].values[last].tolist() == [np.nan] * last.sum() or np.isnan(t["ret_bps"].values[last]).all()
    assert t["y"].dropna().isin([0.0, 1.0]).all()


def test_volume_features_nan_without_volume_and_causal_with():
    d0 = _bars(vol=False); assert F.build(d0)["relvol"].isna().all()
    d1 = _bars(); f = F.build(d1); assert f["relvol"].notna().sum() > 100


def test_regime_layer_is_causal():
    from app.candle_ai.regime import GarchRegime, HMMRegime, regime_frame
    df = _bars(days=60, seed=5); k = len(df) - 60
    g = GarchRegime().fit(df.iloc[:1500]); h = HMMRegime().fit(df.iloc[:1500])
    full = regime_frame(df, g, h); cut = regime_frame(df.iloc[:k].reset_index(drop=True), g, h)
    pd.testing.assert_frame_equal(full.iloc[:k].reset_index(drop=True), cut, check_exact=False, atol=1e-9)
    mut = df.copy(); mut.loc[k:, ["o", "h", "l", "c"]] *= 1.2
    pd.testing.assert_frame_equal(regime_frame(mut, g, h).iloc[:k].reset_index(drop=True), cut, check_exact=False, atol=1e-9)
    assert np.allclose(full[[f"hmm_p{i}" for i in range(3)]].sum(axis=1), 1.0) and (full["sigma_next"] > 0).all()


def test_pivot_reversal_signals_are_causal_and_fire_on_constructed_setup():
    from app.candle_ai import pivot_reversal as PR
    df = _bars(days=12, seed=9); k = len(df) - 50
    full = PR.signals(df); cut = PR.signals(df.iloc[:k].reset_index(drop=True))
    pd.testing.assert_frame_equal(full.iloc[:k].reset_index(drop=True), cut, check_exact=False)
    mut = df.copy(); mut.loc[k:, ["o", "h", "l", "c"]] *= 1.2; pd.testing.assert_frame_equal(PR.signals(mut).iloc[:k].reset_index(drop=True), cut, check_exact=False)
    lines, names = PR.pivot_lines(df); assert len(names) == 13 and lines.iloc[:75].isna().all().all()                 # first session has no previous session
