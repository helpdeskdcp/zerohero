import numpy as np
import pandas as pd

from app.autoscalp import scan5m_shadow as S5
from app.scanner import intraday as I


def _df(n=120, seed=3, last_bull=True):
    rng = np.random.default_rng(seed); t = pd.date_range("2026-10-22 03:45", periods=n, freq="5min", tz="UTC"); c = 100 + np.cumsum(rng.normal(0.02, 0.2, n)); o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.05; l = np.minimum(o, c) - 0.05; v = np.full(n, 1000.0)
    df = pd.DataFrame({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v})
    if last_bull:
        for k in range(n - 40, n - 2): df.loc[k, ["o", "c"]] = [100 + 0.05 * (k - n + 40), 100 + 0.05 * (k - n + 40) + 0.08]; df.loc[k, "h"] = df.loc[k, "c"] + 0.02; df.loc[k, "l"] = df.loc[k, "o"] - 0.02
        pl = df["l"].iloc[n - 2]; df.loc[n - 1, ["o", "l", "h", "c", "v"]] = [pl + 0.05, pl - 0.1, pl + 1.6, pl + 1.5, 6000.0]
    return df


def test_logs_dedupes_fills_forward_and_cooldown(tmp_path):
    df = _df(); sent = []; db = str(tmp_path / "s.db"); S5._last_bin.clear(); S5._last_tg.clear()
    now = float(df["t"].iloc[-1].timestamp() + 300)
    fp = lambda s, until: df
    new = S5.observe("NIFTY", now, fp, db, send=sent.append)
    assert [n["direction"] for n in new] == ["BULL"] and len(sent) == 1 and "not a trade" in sent[0] and "CE" in sent[0] and "gross edge about 0" in sent[0]
    assert S5.observe("NIFTY", now + 5, fp, db, send=sent.append) == []                                            # same 5-minute bin: not evaluated twice
    assert S5.observe("GOLDMX", now + 400, fp, db) == []
    # a later bar: extend the frame so the first hit gets forward returns; a new hit within the cooldown is logged but not announced
    ext = pd.concat([df, pd.DataFrame({"t": pd.date_range(df["t"].iloc[-1] + pd.Timedelta(minutes=5), periods=14, freq="5min", tz="UTC"), "o": df["c"].iloc[-1], "h": df["c"].iloc[-1] + 0.1, "l": df["c"].iloc[-1] - 0.1, "c": df["c"].iloc[-1] + 0.2, "v": 1000.0})], ignore_index=True)
    S5.observe("NIFTY", now + 14 * 300 + 400, lambda s, u: ext, db, send=sent.append)
    rep = S5.report(db); assert rep["forward"]["BULL"]["hits"] == 1 and rep["forward"]["BULL"]["n_fwd3"] == 1 and rep["latest"][0]["fwd3"] is not None
    assert len(sent) == 1
