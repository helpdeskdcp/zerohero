#!/usr/bin/env python3
"""
orderflow_composite_ablation.py -- RESEARCH / BACKTEST ONLY. No production path.

Operator ask (2026-09-06): does a COMPOSITE setup --
  sideways compression + abnormal range/volume spike + rejection candle +
  breakout confirmation (+ index-profile context)
-- have a real edge, vs the isolated triggers?  Ablation A..G, several
threshold sets, premium basis, per session / regime, strict causality.

Operator constraints honoured here:
  * signal FREQUENCY is not a goal -- compression stays STRICT, nothing is
    loosened to manufacture trades. Fewer, cleaner, asymmetric setups win.
  * completed candles only; every look-back is causal (bars[:idx]); the
    index profile used by gate G is the DEVELOPING profile from bars[:idx].
  * thresholds are swept (spike 2 / 2.5 / 3x, comp_lb 5 / 8, vol on/off) but
    NOT optimised against the evaluation window.
  * NOTHING here touches the live engine, API, dashboard or config.

Edge bar: >= 10 independent sessions AND preferably >= 50 trades, across
regimes. We currently have 4 index-bar sessions/symbol -> a real
walk-forward split is impossible; that is reported, not hidden.

------------------------------------------------------------------ definitions
baseline_range(idx) = median high-low of every completed bar BEFORE idx.

LONG candidate on trigger bar b at index idx:
  1 compression : the comp_lb bars ending at idx-1 span
                  <= comp_x * baseline_range(idx)                [C D E F G]
  2 expansion   : (b.h-b.l) >= spike_x * baseline_range(idx)     [A C E F G]
    volume opt. : b.v >= vol_mult * mean(vol before idx)         [toggle]
  3 rejection   : lower wick >= 1.0*body AND (b.c-b.l)/range>=.60 [B D E F G]
  4 interaction : b.h > compressed-range high                    [C D E F G]
  5 profile     : b closes beyond the DEVELOPING value-area high [G only]
  6 confirmation: a LATER completed bar's high > b.h             [F G]
                  -> entry = b.h ; else entry = b.c at bar idx+1
SHORT mirrors (upper wick, close low, break the floor / VAL).

stop = stop_frac*(b.h-b.l) from entry; target = rr*that. Outcome walked on
later COMPLETED index bars (bar spanning both -> STOP_HIT). P&L re-priced on
the captured ATM option premium (premium_walk); no option series or a
thin-quote window -> the trade is dropped (premium-only study).

Ablation:
  A spike only          B hammer only            C sideways+spike
  D sideways+hammer     E sideways+spike+hammer   F = E + confirmation
  G = F + index-profile value-area break
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import market_hub
from app.orderflow.smart_money import _clean
from app.orderflow import premium_walk as _pw
from app.orderflow import profile as _prof

RR = 3.0
STOP_FRAC = 1.0
SPIKE_XS = (2.0, 2.5, 3.0)
COMP_LBS = (5, 8)
REJ_WICK_X = 1.0
REJ_CLOSE_FRAC = 0.60
PROFILE_MIN_BARS = 15
VARIANTS = ("A", "B", "C", "D", "E", "F", "G")
#            needs: compression, expansion, rejection, confirm, profile
_NEEDS = {
    "A": (False, True, False, False, False),
    "B": (False, False, True, False, False),
    "C": (True, True, False, False, False),
    "D": (True, False, True, False, False),
    "E": (True, True, True, False, False),
    "F": (True, True, True, True, False),
    "G": (True, True, True, True, True),
}


# ------------------------------------------------------------------ primitives
def _baseline_range(clean, idx):
    r = [b["h"] - b["l"] for b in clean[:idx] if b["h"] > b["l"]]
    return st.median(r) if r else 0.0


def _avg_vol(clean, idx):
    v = [b["v"] for b in clean[:idx] if b["v"] and b["v"] > 0]
    return (sum(v) / len(v)) if v else 0.0


def _compressed(clean, idx, lookback, comp_x, base):
    if idx < lookback or base <= 0:
        return None
    w = clean[idx - lookback:idx]
    hi = max(b["h"] for b in w)
    lo = min(b["l"] for b in w)
    return (hi, lo) if (hi - lo) <= comp_x * base else None


def _rejection(b, side):
    rng = b["h"] - b["l"]
    o, c = b.get("o"), b.get("c")
    if rng <= 0 or o is None or c is None:
        return False
    body = abs(c - o) or 1e-9
    upper = b["h"] - max(o, c)
    lower = min(o, c) - b["l"]
    if side == "BUY":
        return lower >= REJ_WICK_X * body and (c - b["l"]) / rng >= REJ_CLOSE_FRAC
    return upper >= REJ_WICK_X * body and (b["h"] - c) / rng >= REJ_CLOSE_FRAC


_PROF_CACHE: dict = {}


def _dev_value_area(sym, sess, clean, idx):
    """Causal developing (VAL, VAH) from clean[:idx]; cached per (sess, idx)."""
    key = (sym, sess, idx)
    if key in _PROF_CACHE:
        return _PROF_CACHE[key]
    va = None
    if idx >= PROFILE_MIN_BARS:
        vp = _prof.volume_profile([{"h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
                                   for b in clean[:idx]], symbol=sym)
        if vp.get("status") == "OK" and vp.get("vah") is not None and vp.get("val") is not None:
            va = (vp["val"], vp["vah"])
    _PROF_CACHE[key] = va
    return va


def _walk(bars_after, entry, stop, target, side):
    mfe = mae = 0.0
    for i, b in enumerate(bars_after):
        if side == "BUY":
            fav, adv = b["h"] - entry, b["l"] - entry
            hit_stop, hit_tgt = b["l"] <= stop, b["h"] >= target
        else:
            fav, adv = entry - b["l"], entry - b["h"]
            hit_stop, hit_tgt = b["h"] >= stop, b["l"] <= target
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        if hit_stop:
            return "STOP_HIT", i, mfe, mae
        if hit_tgt:
            return "TARGET_HIT", i, mfe, mae
    return "TRIGGERED", None, mfe, mae


def _first_break(clean, start, level, side):
    for j in range(start, len(clean)):
        if side == "BUY" and clean[j]["h"] > level:
            return j
        if side == "SELL" and clean[j]["l"] < level:
            return j
    return None


def _regime(clean):
    if len(clean) < 5:
        return "NA"
    o0 = clean[0].get("o")
    net = clean[-1]["c"] - (o0 if o0 is not None else clean[0]["c"])
    rng = max(b["h"] for b in clean) - min(b["l"] for b in clean)
    if rng <= 0:
        return "NA"
    if abs(net) >= 0.5 * rng:
        return "TREND_UP" if net > 0 else "TREND_DOWN"
    return "CHOP"


# ------------------------------------------------------------------ one session
def _session_trades(sym, sess, clean, opt_map, *, variant, spike_x, comp_lb,
                    comp_x, use_vol, vol_mult):
    need_c, need_x, need_r, need_conf, need_p = _NEEDS[variant]
    reg = _regime(clean)
    out = []
    for idx, b in enumerate(clean):
        base = _baseline_range(clean, idx)
        if base <= 0:
            continue
        rng = b["h"] - b["l"]
        exp_ok = rng >= spike_x * base
        vol_ok = (not use_vol) or (b["v"] and b["v"] >= vol_mult * _avg_vol(clean, idx))
        comp = _compressed(clean, idx, comp_lb, comp_x, base)

        if need_x and not exp_ok:
            continue
        if use_vol and not vol_ok:
            continue
        if need_c and comp is None:
            continue
        va = _dev_value_area(sym, sess, clean, idx) if need_p else None
        if need_p and va is None:
            continue

        for side in ("BUY", "SELL"):
            if need_r and not _rejection(b, side):
                continue
            if need_c:                       # item 4: trigger breaks the compressed range
                if side == "BUY" and not (b["h"] > comp[0]):
                    continue
                if side == "SELL" and not (b["l"] < comp[1]):
                    continue
            if need_p:                       # item 5: close beyond the developing value area
                if side == "BUY" and not (b["c"] > va[1] and b["l"] <= va[1]):
                    continue
                if side == "SELL" and not (b["c"] < va[0] and b["h"] >= va[0]):
                    continue
            sd = STOP_FRAC * rng
            if sd <= 0:
                continue
            if need_conf:
                entry = b["h"] if side == "BUY" else b["l"]
                bj = _first_break(clean, idx + 1, entry, side)
                if bj is None:
                    continue
                after = clean[bj + 1:]
                entry_ts = clean[bj]["bar_start"]
            else:
                if idx + 1 >= len(clean):
                    continue
                entry = b["c"]
                after = clean[idx + 1:]
                entry_ts = clean[idx + 1]["bar_start"]
            if not after:
                continue
            stop = entry - sd if side == "BUY" else entry + sd
            target = entry + RR * sd if side == "BUY" else entry - RR * sd
            status, ridx, mfe_i, mae_i = _walk(after, entry, stop, target, side)
            if status == "TRIGGERED":
                continue
            exit_ts = after[ridx]["bar_start"]
            rw = _pw.rewalk_leg(opt_map, entry_price=entry, side=side,
                                entry_ts=entry_ts, exit_ts=exit_ts)
            if not rw or rw.get("premium_thin"):
                continue
            out.append({
                "session": sess, "regime": reg, "side": side, "index_status": status,
                "points": rw["premium_points"], "mfe": rw["premium_mfe"],
                "mae": rw["premium_mae"], "index_mfe": round(mfe_i, 2),
                "index_mae": round(mae_i, 2),
            })
    return out


# ------------------------------------------------------------------ aggregation
def _agg(trades):
    n = len(trades)
    if not n:
        return {"n": 0}
    pts = [t["points"] for t in trades]
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p < 0]
    gw, gl = sum(wins), -sum(losses)
    seq = [t["points"] for t in sorted(trades, key=lambda x: x["session"])]
    peak = cum = dd = 0.0
    for p in seq:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    avg_mae = st.fmean(t["mae"] for t in trades)
    return {
        "n": n, "sessions": len({t["session"] for t in trades}),
        "win_rate": round(len(wins) / n, 3),
        "avg_mfe": round(st.fmean(t["mfe"] for t in trades), 2),
        "avg_mae": round(avg_mae, 2),
        "med_mfe": round(st.median(t["mfe"] for t in trades), 2),
        "med_mae": round(st.median(t["mae"] for t in trades), 2),
        "asym": round(st.fmean(t["mfe"] for t in trades) / abs(avg_mae), 2) if avg_mae < 0 else None,
        "avg_pts": round(st.fmean(pts), 2),
        "net_pts": round(sum(pts), 1),
        "expectancy": round(sum(pts) / n, 3),
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_dd": round(dd, 1),
    }


def _collect(sym, dates, **kw):
    all_tr = []
    for d in dates:
        bars = market_hub.session_bars(sym, d)
        if not bars:
            continue
        clean = _clean(bars)
        if len(clean) < max(COMP_LBS) + 3:
            continue
        opt_map = market_hub.session_option_quotes(sym, d)
        if not opt_map:
            continue
        all_tr += _session_trades(sym, d, clean, opt_map, **kw)
    return all_tr


# ------------------------------------------------------------------ report
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--vol-mult", type=float, default=2.0)
    ap.add_argument("--comp-x", default="1.5,2.0,2.5",
                    help="compression thresholds to sweep: prior-range span <= comp_x * baseline range. "
                         "STRICT values only -- this is sensitivity analysis, not loosening for signal count.")
    args = ap.parse_args()
    COMP_XS = tuple(float(x) for x in str(args.comp_x).split(","))
    MIN_SESS, MIN_TR = 10, 50

    for sym in args.symbols.split(","):
        sym = sym.strip().upper()
        dates = sorted(market_hub.session_dates(sym, limit=400))
        print("\n" + "=" * 100)
        print(f"{sym}  --  premium basis  --  {len(dates)} captured index-bar sessions: {dates}  "
              f"(comp_x sweep {COMP_XS}, strict)")
        print("=" * 100)
        hdr = (f"{'var':<4}{'spk':>4}{'lb':>3}{'cx':>5}{'vol':>4} {'n':>4}{'ses':>4}{'win%':>7}"
               f"{'aMFE':>7}{'aMAE':>7}{'asym':>6}{'aPts':>7}{'net':>8}{'expc':>8}{'PF':>7}{'mDD':>8}")
        print(hdr)
        print("-" * len(hdr))
        best = None
        for variant in VARIANTS:
            nc, nx, nr, ncf, npf = _NEEDS[variant]
            spk_opts = SPIKE_XS if nx else (SPIKE_XS[0],)
            lb_opts = COMP_LBS if nc else (COMP_LBS[0],)
            cx_opts = COMP_XS if nc else (COMP_XS[0],)
            zero = 0
            for spike_x in spk_opts:
                for comp_lb in lb_opts:
                    for comp_x in cx_opts:
                        for use_vol in (False, True):
                            tr = _collect(sym, dates, variant=variant, spike_x=spike_x,
                                          comp_lb=comp_lb, comp_x=comp_x,
                                          use_vol=use_vol, vol_mult=args.vol_mult)
                            a = _agg(tr)
                            if not a["n"]:
                                zero += 1
                                continue
                            v = "Y" if use_vol else "N"
                            cxs = f"{comp_x:g}" if nc else "-"
                            print(f"{variant:<4}{spike_x:>4}{comp_lb:>3}{cxs:>5}{v:>4} {a['n']:>4}{a['sessions']:>4}"
                                  f"{a['win_rate']*100:>6.1f}%{a['avg_mfe']:>7}{a['avg_mae']:>7}"
                                  f"{str(a['asym']):>6}{a['avg_pts']:>7}{a['net_pts']:>8}{a['expectancy']:>8}"
                                  f"{str(a['profit_factor']):>7}{a['max_dd']:>8}")
                            score = ((a["asym"] or 0), a["expectancy"])
                            if a["n"] >= 6 and a["expectancy"] > 0 and (best is None or score > best[0]):
                                best = (score, variant, spike_x, comp_lb, use_vol, a, tr, comp_x)
            if zero:
                print(f"{variant:<4} .. {zero} threshold combo(s) produced 0 trades")

        if best:
            _, variant, spike_x, comp_lb, use_vol, a, tr, bcx = best
            print(f"\n  most-asymmetric positive-expectancy config (n>=6): {variant} "
                  f"spike_x={spike_x} comp_lb={comp_lb} comp_x={bcx} vol={'Y' if use_vol else 'N'} "
                  f"-> n={a['n']} sess={a['sessions']} win%={a['win_rate']*100:.0f} "
                  f"asym={a['asym']} exp={a['expectancy']} PF={a['profit_factor']} maxDD={a['max_dd']}")
            by_s, by_r = {}, {}
            for t in tr:
                by_s.setdefault(t["session"], []).append(t)
                by_r.setdefault(t["regime"], []).append(t)
            print("  by session:")
            for k in sorted(by_s):
                s = _agg(by_s[k])
                print(f"    {k}  n={s['n']:>3} win%={s['win_rate']*100:>5.1f} net={s['net_pts']:>8} "
                      f"exp={s['expectancy']:>8} PF={s['profit_factor']} asym={s['asym']}")
            print("  by regime:")
            for k in sorted(by_r):
                s = _agg(by_r[k])
                print(f"    {k:<11} n={s['n']:>3} win%={s['win_rate']*100:>5.1f} net={s['net_pts']:>8} "
                      f"exp={s['expectancy']:>8} PF={s['profit_factor']} asym={s['asym']}")
            half = len(dates) // 2
            at = _agg([t for t in tr if t["session"] in dates[:half]])
            av = _agg([t for t in tr if t["session"] in dates[half:]])
            print(f"  nominal walk-forward  train {dates[:half]} exp={at.get('expectancy')} n={at.get('n')} "
                  f"| test {dates[half:]} exp={av.get('expectancy')} n={av.get('n')}  "
                  f"[NOT a valid split at {half}+{len(dates)-half} sessions]")
        else:
            print("\n  no config reached n>=6 with positive expectancy.")

        n_sess = len(dates)
        n_best = best[5]["n"] if best else 0
        n_reg = len({_regime(_clean(market_hub.session_bars(sym, d))) for d in dates})
        print(f"\n  VERDICT ({sym}): independent sessions={n_sess} (need >={MIN_SESS}); "
              f"best-config trades={n_best} (need >={MIN_TR}); distinct regimes seen={n_reg}.")
        print("    -> Sample is far below the edge bar. Even where a config shows a "
              "positive expectancy / asymmetric MFE:MAE, it CANNOT be called a proven "
              "edge on 4 sessions. Classification: (2) PROMISING-BUT-INSUFFICIENT at "
              "best, otherwise (3) NO EDGE / (4) ARTIFACT. Production behaviour "
              "unchanged; re-run when >=10 independent sessions across regimes exist.")


if __name__ == "__main__":
    main()
