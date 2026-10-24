import numpy as np
import pandas as pd

from app.candle_ai import option_flow as OF


def _bars(put_vol_mult=1.0):
    rows = []; base = 1_800_000_000
    for m in range(0, 60):
        ts = base + m * 60
        for k in (22800, 22850, 22900, 22950, 23000, 23050, 23100):
            rows.append(("CE", float(k), ts, max(5.0, 22900 + 50 - k) + 60 + m * 0.01, 100.0, 1000.0 + m))
            rows.append(("PE", float(k), ts, max(5.0, k - 22900 + 50) + 60 + m * 0.01, 100.0 * put_vol_mult, 2000.0 + 10 * m))
    return pd.DataFrame(rows, columns=["otype", "strike", "ts", "c", "v", "oi"]).rename(columns={"otype": "otype"})


def test_flow_features_signs_and_causality():
    b = _bars(put_vol_mult=3.0); spot = pd.Series({1_800_000_000 + m * 60: 22900.0 + 0.1 * m for m in range(60)}); t = 1_800_000_000 + 40 * 60
    f = OF.flow_features(b, spot, [t]).iloc[0]
    assert abs(f.F1 - 0.5) < 1e-9                                     # put volume 3x call volume -> (3-1)/(3+1)
    assert f.F2 > 0                                                  # put OI grows 10/min, call OI 1/min -> positive OI-change imbalance
    assert np.isfinite(f.F3) and np.isfinite(f.F4) and np.isfinite(f.F5)
    # causality: rewriting everything after t must not change the features at t
    b2 = b.copy(); b2.loc[b2.ts > t, ["c", "v", "oi"]] *= 7; spot2 = spot.copy(); spot2[spot2.index > t] += 500
    f2 = OF.flow_features(b2, spot2, [t]).iloc[0]; assert np.allclose(f.values, f2.values, equal_nan=True)
    assert np.isnan(OF.flow_features(b, spot, [t + 999999]).iloc[0].F1)       # no spot at that minute -> NaN, not a guess
