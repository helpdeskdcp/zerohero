import numpy as np, pandas as pd
from app.zone_breakout import strategy as ZB, trap_features as TF


def _bars(n=900, seed=1):
    rng = np.random.default_rng(seed); ts = []; day = pd.Timestamp("2024-01-01 09:15", tz="Asia/Kolkata")
    while len(ts) < n:
        if day.weekday() < 5: ts += [day + pd.Timedelta(minutes=5 * k) for k in range(75)]
        day += pd.Timedelta(days=1)
    ts = ts[:n]; c = 20000 + np.cumsum(rng.normal(0, 8, n)); o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + abs(rng.normal(0, 4, n)); l = np.minimum(o, c) - abs(rng.normal(0, 4, n))
    return pd.DataFrame(dict(timestamp=ts, open=o, high=h, low=l, close=c, volume=1.0))


def test_features_do_not_see_the_future():
    b = _bars(); t, _ = ZB.simulate(b, ZB.Config(cons_bars=2, max_cons_atr=3.0), "next_open", True, interval_min=5)
    assert len(t) >= 5
    t["risk_bp"] = t["risk"] / t["entry"] * 1e4; t["net_R"] = t["R"]
    full = TF.build(b, t); i0 = int(t["signal_i"].iloc[len(t) // 2]); b2 = b.copy()
    b2.loc[i0 + 1:, ["open", "high", "low", "close"]] = np.random.default_rng(9).normal(30000, 500, (len(b) - i0 - 1, 4))   # rewrite the future
    cut = t[t["signal_i"] <= i0]; part = TF.build(b2, cut)
    a = full[full["signal_i"] <= i0][TF.FEATURES].reset_index(drop=True); p = part[TF.FEATURES].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, p, check_exact=False, atol=1e-9)


def test_prior_fail_never_counts_the_trade_itself():
    b = _bars(); t, _ = ZB.simulate(b, ZB.Config(cons_bars=2, max_cons_atr=3.0), "next_open", True, interval_min=5)
    t["risk_bp"] = t["risk"] / t["entry"] * 1e4; t["net_R"] = t["R"]
    f = TF.build(b, t); same_bar = f[(f["exit_ts"] == f["entry_ts"]) & (f["reason"] == "sl")]
    assert len(same_bar), "fixture must contain a stop on the entry bar"
    first_of_day = same_bar.groupby(["session", "side"]).head(1)
    assert (first_of_day["prior_fail"] == 0).all()
