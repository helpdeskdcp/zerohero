"""PREFIX-INVARIANCE (no-future-data) test, executed: results computed on data truncated at time T must equal, record for record, the results computed on the full data
for every trade/signal that is complete before T. Engine (corrected_params) and the candlestick pipeline (patterns, zones, bracket) are both tested at several cut points,
including mid-session cuts. Any difference would prove that a result used information from after T. READ-ONLY."""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import corrected_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.research_engines.smart_bos.engine import _atr  # noqa: E402
from app.strategy_lab import candle_patterns as CP  # noqa: E402

b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
IST = 19800
cuts = [dt.datetime(2020, 6, 15, 12, 30), dt.datetime(2023, 3, 9, 14, 5), dt.datetime(2025, 6, 18, 10, 45)]       # IST wall clock, deliberately mid-session
full_ch = resample(b1, 5, 555, 930)
full_tr, _ = run(full_ch, corrected_params(chart_tf=5), b1=b1)
key = lambda ch, t: (int(ch["t"][t.sig_i]), int(ch["t"][t.entry_i]), int(ch["t"][t.exit_i]), t.side, round(t.entry, 6), round(t.exit, 6), round(t.bps, 6), t.reason)
fullset = {key(full_ch, t) for t in full_tr}
print(f"ENGINE (corrected_params, NIFTY 5m): full run {len(full_tr)} trades")
ok_all = True
for c in cuts:
    T = int((c - dt.datetime(1970, 1, 1)).total_seconds()) - IST
    m = b1["t"] < T
    b1t = {k: v[m] for k, v in b1.items()}
    cht = resample(b1t, 5, 555, 930)
    trt, _ = run(cht, corrected_params(chart_tf=5), b1=b1t)
    # complete before the cut: a position still open when the data ends is force-closed by the engine with reason EOD_DATA (a boundary artefact of truncation,
    # NOT a real exit) -> it is excluded; its ENTRY is nevertheless checked below to exist unchanged in the full run
    closed = [t for t in trt if int(cht["t"][t.exit_i]) + 300 <= T and t.reason != "EOD_DATA"]
    forced = [t for t in trt if t.reason == "EOD_DATA"]
    entry_keys_full = {(k[0], k[1], k[3], k[4]) for k in fullset}
    forced_ok = all((int(cht["t"][t.sig_i]), int(cht["t"][t.entry_i]), t.side, round(t.entry, 6)) in entry_keys_full for t in forced)
    keys = [key(cht, t) for t in closed]
    missing = [k for k in keys if k not in fullset]
    full_closed_before = {k for k in fullset if k[2] + 300 <= T}
    extra = full_closed_before - set(keys)
    ok = not missing and not extra and forced_ok
    ok_all &= ok
    print(f"  cut {c:%Y-%m-%d %H:%M} IST: truncated-run trades complete before cut {len(keys)}; full-run trades complete before cut {len(full_closed_before)}; in truncated but not full {len(missing)}; in full but not truncated {len(extra)}; positions force-closed at the data end {len(forced)} (their entries exist unchanged in the full run: {forced_ok}) -> {'IDENTICAL' if ok else 'DIFFERENT'}")

# ---- candlestick pipeline: patterns / zones / bracket outcomes
o, h, l, c = full_ch["o"], full_ch["h"], full_ch["l"], full_ch["c"]
day, mod = ist_minutes(full_ch["t"])
atr = _atr(h, l, c, 14)
P = CP.patterns(o, h, l, c, atr); Z = CP.zones(o, h, l, c, atr, day, None)
print("\nCANDLE PIPELINE (patterns, zones, bracket outcome)")
for cdt in cuts:
    T = int((cdt - dt.datetime(1970, 1, 1)).total_seconds()) - IST
    n = int((full_ch["t"] < T).sum())
    o_, h_, l_, c_, d_ = o[:n], h[:n], l[:n], c[:n], day[:n]
    a_ = _atr(h_, l_, c_, 14)
    P2 = CP.patterns(o_, h_, l_, c_, a_); Z2 = CP.zones(o_, h_, l_, c_, a_, d_, None)
    last_complete_day_start = int(np.argmax(d_ == d_[-1]))                                                       # bars of earlier, fully observed days
    lim = last_complete_day_start
    same_p = all((P[k][:lim] == P2[k][:lim]).all() for k in P)
    same_z = all((Z[k][0][:lim] == Z2[k][0][:lim]).all() and (Z[k][1][:lim] == Z2[k][1][:lim]).all() for k in Z)
    idx = np.flatnonzero(P["bull_engulf"][:lim - 30] & Z["none"][0][:lim - 30])[-400:]
    sd = np.maximum(o[idx + 1] - l[idx], 1.0 * atr[idx])
    b_full = CP.bracket(o, h, l, c, day, idx, np.ones(len(idx), int), sd, 1.0)
    b_tr = CP.bracket(o_, h_, l_, c_, d_, idx, np.ones(len(idx), int), sd, 1.0)
    same_b = np.allclose(np.nan_to_num(b_full), np.nan_to_num(b_tr))
    ok_all &= same_p and same_z and same_b
    print(f"  cut {cdt:%Y-%m-%d %H:%M}: bars compared (earlier complete days) {lim}; patterns identical {same_p}; zones identical {same_z}; bracket outcomes identical for {len(idx)} events {same_b}")
print("\nRESULT:", "no difference found at any cut -> no use of future data detected" if ok_all else "DIFFERENCE FOUND -> leakage")
