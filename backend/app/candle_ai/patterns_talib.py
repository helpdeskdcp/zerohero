"""Canonical TA-Lib candlestick patterns (61 CDL*) as {-1,0,+1} features, column prefix 'cdl_'. Causal (TA-Lib looks back only). Optional: needs the TA-Lib wheel."""
from __future__ import annotations

import numpy as np
import pandas as pd


def talib_features(df: pd.DataFrame) -> pd.DataFrame:
    import talib
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c")); out = {}
    for name in talib.get_function_groups()["Pattern Recognition"]:
        out["cdl_" + name[3:].lower()] = np.sign(getattr(talib, name)(o, h, l, c)).astype(np.int8)
    return pd.DataFrame(out, index=df.index)
