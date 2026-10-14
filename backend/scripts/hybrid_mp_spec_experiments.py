"""(A) opening gap: Pine formula vs session-aware formula vs the independent daily-bar table.  (B) state recomputation from a short window
(the dashboard approach) vs full history: how many sessions of history are needed for the final-bar state to agree?"""
import datetime as dt
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.hybrid_mp.gap import opening_gap_pine, opening_gap_session_aware  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
which = sys.argv[1] if len(sys.argv) > 1 else "all"

if which in ("all", "gap"):
    raw = load_1m("NIFTY", "INDEX")                       # includes bars OUTSIDE 09:15-15:30 (as a chart with extended bars would)
    sess_only = resample(raw, 1, 555, 930)
    days, mod = ist_minutes(raw["t"])
    first_idx = {}
    for i in range(len(raw["t"])):
        d = int(days[i])
        if d not in first_idx:
            first_idx[d] = i
    pine_raw = opening_gap_pine(raw)["gap_pct"]
    sa = opening_gap_session_aware(raw, (555, 930))
    pine_clean = opening_gap_pine(sess_only)["gap_pct"]
    sdays, _ = ist_minutes(sess_only["t"])
    c = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True)
    # 1d bars are stamped at the IST session start expressed in UTC (e.g. 2026-09-24T18:30:00Z == 2026-09-25 IST): convert before taking the date
    daily = {}
    for bs, o_, c_ in c.execute("select bar_start, o, c from market_candles where symbol='NIFTY' and kind='INDEX' and tf='1d' order by bar_start"):
        key = (dt.datetime.fromisoformat(bs.replace("Z", "+00:00")).astimezone(IST)).date().isoformat()
        daily[key] = (o_, c_)
    rows = []
    prev_daily_close = None
    for dkey in sorted(daily):
        o_, c_ = daily[dkey]
        if prev_daily_close:
            dd = dt.date.fromisoformat(dkey).toordinal() - 719163
            gi = np.flatnonzero(days == dd)
            si = np.flatnonzero(sdays == dd)
            if len(gi) and len(si):
                rows.append((dkey, pine_raw[gi[0]], sa["gap_pct"][np.flatnonzero((days == dd) & (mod >= 555))[0]], pine_clean[si[0]], (o_ / prev_daily_close - 1) * 100))
        prev_daily_close = c_
    r = np.array([x[1:] for x in rows], float)
    print(f"A. OPENING GAP, NIFTY, {len(rows)} trading days with all four sources (Pine on raw bars incl. outside-session | session-aware | Pine on session-only bars | daily table open/prev close)")
    for name, col in (("Pine on raw (extended) bars", 0), ("session-aware (corrected)", 1), ("Pine on session-only bars", 2)):
        diff = np.abs(r[:, col] - r[:, 3])
        print(f"  {name:30s} mean|gap - daily-table gap| = {np.nanmean(diff):.4f} pct-pts; days differing by >0.05 pct-pt: {int(np.nansum(diff > 0.05))} ({np.nanmean(diff > 0.05):.1%}); by >0.10: {int(np.nansum(diff > 0.10))}")
    bad = np.argsort(-np.abs(r[:, 0] - r[:, 3]))[:3]
    print("  worst Pine-on-raw days:", [(rows[k][0], round(r[k, 0], 2), round(r[k, 3], 2)) for k in bad])
    print(f"  session-aware flagged incomplete prior session on {int(sa['incomplete_prior'].sum())} bars (first-of-day only counted once per day: {len(set(days[sa['incomplete_prior']]))} days)")

if which in ("all", "window"):
    b1 = load_1m("NIFTY", "INDEX")
    chart = resample(b1, 5, 555, 930)
    cd, _ = ist_minutes(chart["t"])
    ud = np.unique(cd)
    rng = np.random.default_rng(123)
    cand = np.flatnonzero(np.isin(cd, ud[-150:]))
    ends = np.sort(rng.choice(cand, 50, replace=False))
    p = fixed_params(chart_tf=5)
    keys = ("direction", "trade", "signal_check")
    res = {W: [0, 0, 0] for W in (3, 8, 20)}
    detail = {W: [] for W in (3, 8, 20)}
    for e in ends:
        t_end = chart["t"][e]
        full_ch = {k: v[:e + 1] for k, v in chart.items()}
        full_b1 = {k: v[b1["t"] < t_end + 300] for k, v in b1.items()}
        _, sf = run(full_ch, p, b1=full_b1, want_state=True)
        F = sf["state"]
        for W in (3, 8, 20):
            keep_days = np.unique(cd[:e + 1])[-W:]
            m = np.isin(cd[:e + 1], keep_days)
            ch = {k: v[:e + 1][m] for k, v in chart.items()}
            bd, _ = ist_minutes(b1["t"])
            mb = (b1["t"] < t_end + 300) & np.isin(bd, keep_days)
            bb = {k: v[mb] for k, v in b1.items()}
            _, sw = run(ch, p, b1=bb, want_state=True)
            S = sw["state"]
            res[W][0] += 1
            ok = all(F[k] == S[k] for k in keys) and F["pending"]["direction"] == S["pending"]["direction"] \
                and ((F["entry"] is None) == (S["entry"] is None)) and (F["entry"] is None or abs(F["entry"] - S["entry"]) < 1e-6)
            res[W][1] += ok
            res[W][2] += all(F[k] == S[k] for k in ("direction",))
            if not ok:
                detail[W].append((int(e), [k for k in keys if F[k] != S[k]]))
    print(f"\nB. STATE FROM A SHORT WINDOW vs FULL HISTORY (50 random end bars in the last 150 sessions; fixed_params, 5m, MTF on)")
    for W, (n, ok, okd) in res.items():
        print(f"  window = last {W:2d} sessions: all-fields agreement {ok}/{n} ({ok/n:.0%}); structure-direction agreement {okd}/{n} ({okd/n:.0%}); disagreements e.g. {detail[W][:3]}")
