"""Phase 3 data audit (read-only): completeness, duplicates, OHLC consistency, session/weekday sanity, roll-like jumps, per instrument."""
import datetime as dt
import hashlib
import os
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.smart_bos.data import _CACHE, ist_minutes, load_1m  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
for sym, kind, sess in (("NIFTY", "INDEX", (555, 930)), ("BANKNIFTY", "INDEX", (555, 930)), ("SENSEX", "INDEX", (555, 930)),
                        ("CRUDEOIL", "FUTURE", (540, 1410)), ("NATURALGAS", "FUTURE", (540, 1410))):
    path = os.path.join(_CACHE, f"{sym}_{kind}_2017-01-01_2027-01-01.npz")
    if not os.path.exists(path):
        path = os.path.join(_CACHE, f"{sym}_{kind}_2026-01-01_2027-01-01.npz") if kind == "FUTURE" else path
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if os.path.exists(path) else "n/a"
    b = load_1m(sym, kind) if kind == "INDEX" else load_1m(sym, kind, start="2026-01-01")
    t, o, h, l, c, v = (b[k] for k in ("t", "o", "h", "l", "c", "v"))
    days, mod = ist_minutes(t)
    wd = ((t + 19800) // 86400 + 3) % 7
    exp = sess[1] - sess[0]
    ud, cnt = np.unique(days, return_counts=True)
    dup = int((np.diff(t) == 0).sum())
    ins = (mod >= sess[0]) & (mod < sess[1])
    bad_hl = int(((h < np.maximum(o, c) - 1e-9) | (l > np.minimum(o, c) + 1e-9) | (h < l)).sum())
    zero_rng = float(np.mean(h == l))
    first = [dt.datetime.fromtimestamp(int(t[days == d][0]), IST) for d in ud[:: max(1, len(ud) // 200)]]
    first_ok = np.mean([(f.hour * 60 + f.minute) == sess[0] for f in first])
    ov = np.array([o[days == d][0] / c[days == ud[k - 1]][-1] - 1 for k, d in enumerate(ud) if k > 0 and (days == ud[k - 1]).any()]) if len(ud) < 400 else None
    if ov is None:
        lastc = np.array([c[days == d][-1] for d in ud]); firsto = np.array([o[days == d][0] for d in ud])
        ov = firsto[1:] / lastc[:-1] - 1
    big = np.argsort(-np.abs(ov))[:3]
    print(f"\n{sym} {kind}  file={os.path.basename(path)} sha256[:16]={sha}")
    print(f"  bars={len(t)} sessions={len(ud)} first={dt.datetime.fromtimestamp(int(t[0]), IST):%Y-%m-%d} last={dt.datetime.fromtimestamp(int(t[-1]), IST):%Y-%m-%d %H:%M}")
    print(f"  bars/session expected {exp}: median {int(np.median(cnt))}, sessions with >=99% bars {np.mean(cnt >= 0.99 * exp):.1%}, <90% bars {int((cnt < 0.9 * exp).sum())} sessions, missing bars total {int(np.maximum(exp - cnt, 0).sum())}")
    print(f"  duplicate timestamps={dup} | bars outside session={int((~ins).sum())} | weekend bars (fixed weekday)={int((wd >= 5).sum())} | first bar at session open in {first_ok:.1%} of sampled days")
    print(f"  OHLC inconsistent bars={bad_hl} | zero-range bars={zero_rng:.1%} | zero-volume bars={float(np.mean(v == 0)):.1%}")
    print("  largest overnight jumps (open vs prev close): " + ", ".join(f"{dt.datetime.fromtimestamp(int(t[days == ud[k + 1]][0]), IST):%Y-%m-%d} {ov[k]*100:+.2f}%" for k in big))
