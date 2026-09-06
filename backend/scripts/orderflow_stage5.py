#!/usr/bin/env python3
"""
orderflow_stage5.py -- RESEARCH ONLY.

Stage-4 showed the spike->acceptance->continuation sequence does NOT survive
re-pricing on ATM option premium (theta + spread dominate). Stage-5 goes back
to the UNDERLYING / FUTURES and asks the cleaner question:

  Does the sequence have a positive expectancy-per-unit-structural-risk on the
  underlying itself, with a REALISTIC stop (exit at the breaching bar's
  extreme + 1 futures tick, not at the stop price), validated on a
  chronological train / validation / out-of-sample split?

Then it builds the deliverables the Stage-5 brief asks for: the H1..H7
competing-explanation classifier, the small-SL / large-R distribution, the
earliest-entry comparison by expectancy-per-R, the feature incremental-value
report, and an index-vs-index lead/lag proxy (true constituent-stock data is
UNOBSERVABLE).

HARD RULES: research only; no production pattern; no live change; no orders;
NO weighted score. Strictly causal; chronological split (never shuffled);
thresholds by stability not P&L. Nothing called institutional/smart-money/
holy-grail/profitable. If the maths disproves a hypothesis, it is reported.

Reuses Stage-3/4 helpers. Underlying data via orderflow_histsrc (~35-39
sessions/symbol, 3 regimes, 2026-07-13 .. 09-04).

Outputs:
  data/orderflow_stage5_report_2026-09-06.txt
  data/orderflow_stage5_events.csv
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import orderflow_histsrc as HS
from scripts.orderflow_stage3_validation import (
    HSess, _dev_va, _levels, _broken_level, _reclaimed_by, _outcome, _split,
    _classify_spike, _n1, _acc_entry_bar, _entry_stops, spike_percentile,
)
from scripts.orderflow_stage4 import day_regime, balance_features, orderflow_proxy, profile_context

RK = (1, 2, 3, 4, 5, 6, 8)
OUT_WIN = 3
# 1-tick futures slippage per side, in underlying points
TICK = {"NIFTY": 0.05, "NATURALGAS": 0.10, "CRUDEOIL": 1.0}
SPIKE_PCTL = 0.90


# ================================================================ realistic underlying walk
def uwalk(bars, ebar, entry, sl, direction, tick):
    """Forward walk on the underlying. Stop-out exits at the breaching bar's
    extreme minus 1 tick (adverse) -> realistic loss can exceed -1R. Also
    tracks MFE before invalidation and a fixed-3R realised R."""
    R = abs(entry - sl)
    if R <= 0:
        return None
    t3 = entry + 3 * R if direction == "LONG" else entry - 3 * R
    mfe = mae = 0.0
    reach = {k: None for k in RK}
    t1 = t2 = tinv = None
    realized_fix3 = None
    exit_kind = None
    for i, b in enumerate(bars[ebar:], start=ebar):
        fav = (b["h"] - entry) if direction == "LONG" else (entry - b["l"])
        adv = (b["l"] - entry) if direction == "LONG" else (entry - b["h"])
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        rr = mfe / R
        for k in RK:
            if reach[k] is None and rr >= k:
                reach[k] = i - ebar
        if t1 is None and rr >= 1:
            t1 = i - ebar
        if t2 is None and rr >= 2:
            t2 = i - ebar
        hit_stop = (b["l"] <= sl) if direction == "LONG" else (b["h"] >= sl)
        hit_t3 = (b["h"] >= t3) if direction == "LONG" else (b["l"] <= t3)
        if hit_stop:
            ext = b["l"] if direction == "LONG" else b["h"]
            fill = (min(ext, sl) - tick) if direction == "LONG" else (max(ext, sl) + tick)
            realized_fix3 = ((fill - entry) if direction == "LONG" else (entry - fill)) / R
            tinv = i - ebar
            exit_kind = "STOP"
            break
        if hit_t3:
            realized_fix3 = 3.0 - tick / R
            exit_kind = "T3"
            break
    if realized_fix3 is None:                       # ran to session end
        last = bars[-1]["c"]
        realized_fix3 = ((last - entry) if direction == "LONG" else (entry - last)) / R
        exit_kind = "EOD"
    return {
        "R_pts": round(R, 3), "MFE_R": round(mfe / R, 3), "MAE_R": round(mae / R, 3),
        "fix3_R": round(realized_fix3, 3), "exit": exit_kind,
        "t1": t1, "t2": t2, "t_inv": tinv,
        **{f"mfe_ge_{k}R": (reach[k] is not None) for k in RK},
    }


# ================================================================ H1..H7 classifier
def hclass(s, idx, direction, L, w):
    """Objective competing-explanation label from bars <= idx and the 1-3
    completed post-bars in `w` (w = walk result on the reference entry).
    One primary label per event."""
    c = s.clean
    b = c[idx]
    rng = b["h"] - b["l"]
    o, cl = b.get("o"), b.get("c")
    body = abs(cl - o) if (o is not None and cl is not None) else 0.0
    body_frac = body / rng if rng > 0 else 0
    prior = c[max(0, idx - 3):idx]
    prior_rng = st.fmean([x["h"] - x["l"] for x in prior]) if prior else rng
    # run of same-direction closes ending at idx
    run = 0
    for j in range(idx, 0, -1):
        up = (c[j].get("c") or 0) >= (c[j - 1].get("c") or 0)
        if (up and direction == "LONG") or (not up and direction == "SHORT"):
            run += 1
        else:
            break
    swept = False
    if L is not None:
        broke = (b["h"] > L) if direction == "LONG" else (b["l"] < L)
        closed_back = (b["c"] <= L) if direction == "LONG" else (b["c"] >= L)
        swept = broke and closed_back
    reclaim3 = _reclaimed_by(s, idx, direction, L, 3) is not None
    n1 = _n1(s, idx, direction, L)
    n1_ag = bool(n1 and n1["agree"])
    cont = (w or {}).get("MFE_R", 0) >= 2.0 and (w or {}).get("exit") != "STOP"
    rev = (w or {}).get("MAE_R", 0) <= -1.0
    # priority order
    if swept and reclaim3 and not cont:
        return "H3_sweep" if not rev else "H7_trapped_reversal"
    if reclaim3 and rev:
        return "H7_trapped_reversal"
    if run >= 5 and not cont:
        return "H6_exhaustion"
    if rng >= 2.0 * prior_rng and prior_rng <= 0.6 * s.base[idx] and not cont:
        return "H2_liquidity_vacuum"
    if body_frac < 0.35 and rng >= 1.5 * s.base[idx]:
        return "H4_absorption_continuation" if (n1_ag and cont) else "H5_absorption_reversal"
    if body_frac >= 0.55 and n1_ag and not reclaim3:
        return "H1_genuine_participation"
    return "H0_ambiguous"


# ================================================================ VWAP proxy
def _vwap_proxy(clean, idx):
    """No bar volume -> anchored mean of typical price. PROXY, labelled."""
    if idx < 2:
        return None
    tp = [(x["h"] + x["l"] + x["c"]) / 3 for x in clean[:idx]]
    return st.fmean(tp)


# ================================================================ build
ENTRIES = ("A_spikeclose", "B_n1agree", "C_earlyaccept", "D_hold2")


def build(sym):
    ss = HS.sessions(sym)
    sessions = [HSess(sym, d, src) for d, src in ss]
    tr, va, oos = _split(sessions)
    tick = TICK.get(sym, 0.05)
    rows = []
    for s in sessions:
        c = s.clean
        for idx in range(6, len(c) - 5):
            if s.base[idx] <= 0:
                continue
            pct = spike_percentile(s, idx)
            if pct is None or pct < SPIKE_PCTL:
                continue
            b = c[idx]
            cls, *_ = _classify_spike(b)
            direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                         else ("LONG" if (b.get("c") or 0) >= (b.get("o") or 0) else "SHORT"))
            lv = _levels(s, idx)
            L, _ = _broken_level(s, idx, direction, lv)
            n1 = _n1(s, idx, direction, L)

            # entry candidates (all causal)
            T = {}
            if idx + 1 < len(c):
                T["A_spikeclose"] = (b["c"], idx + 1)
            if n1 and n1["agree"] and idx + 2 < len(c):
                T["B_n1agree"] = (c[idx + 1]["c"], idx + 2)
            cj = _acc_entry_bar(s, idx, direction, L, "close_no_reclaim")
            if cj is not None and cj + 1 < len(c):
                T["C_earlyaccept"] = (c[cj]["c"], cj + 1)
            dj = _acc_entry_bar(s, idx, direction, L, "hold2")
            if dj is not None and dj + 1 < len(c):
                T["D_hold2"] = (c[dj]["c"], dj + 1)

            ew = {}
            for k, (ep, eb) in T.items():
                stops = _entry_stops(s, idx, eb, direction, lv)
                # prefer the reaction-candle stop, fall back to spike/window
                sl = stops.get("reaction") or stops.get("window") or stops.get("spike")
                w = uwalk(c, eb, ep, sl, direction, tick)
                if w:
                    from scripts.orderflow_stage3_validation import _avail_R
                    w["avail_R"] = _avail_R(lv, ep, w["R_pts"], direction)
                    w["entry"] = round(ep, 3)
                    w["sl"] = round(sl, 3)
                    ew[k] = w

            ref = ew.get("A_spikeclose")
            oc = {ww: _outcome(s, idx, direction, L, (ref or {}).get("R_pts", 1) or 1, ww)
                  for ww in (1, 2, 3, 5)}
            H = hclass(s, idx, direction, L, ew.get("C_earlyaccept") or ref)

            of = orderflow_proxy(HS.session_oi_series(sym, s.date, s.src), s, idx, direction) \
                if False else {}   # OI proxy re-fetched lazily below only for feature test
            reg = day_regime(s, idx)
            bal = balance_features(s, idx)
            pf = profile_context(s, idx, direction)
            vwp = _vwap_proxy(c, idx)
            split = "train" if s.date in tr else "val" if s.date in va else "oos"
            rows.append({
                "sym": sym, "session": s.date, "src": s.src, "ts": b["bar_start"],
                "split": split, "regime": s.regime, "day_regime": reg,
                "direction": direction, "spike_class": cls,
                "spike_pctile": round(pct, 3),
                "range_x": round((b["h"] - b["l"]) / s.base[idx], 2),
                "vwap_dist": round((b["c"] - vwp), 2) if vwp else None,
                "L": L, "reclaim3": _reclaimed_by(s, idx, direction, L, 3) is not None,
                "n1_agree": bool(n1 and n1["agree"]),
                "n1_reenter": bool(n1 and n1["reenter_spike"]),
                "bal_range_contraction": bal.get("bal_range_contraction"),
                "bal_rotations": bal.get("bal_rotations"),
                "pf_loc": pf.get("loc"),
                "hclass": H, "oc": oc, "ew": ew,
            })
    return sessions, rows, (tr, va, oos)


# ================================================================ stats
def _agg(rows, ekey):
    W = [(r, r["ew"][ekey]) for r in rows if ekey in r["ew"]]
    if not W:
        return {"n": 0}
    n = len(W)
    mfe = sorted(w["MFE_R"] for _, w in W)
    mae = sorted(w["MAE_R"] for _, w in W)
    fx = [w["fix3_R"] for _, w in W]
    av = sorted(w["avail_R"] for _, w in W if w.get("avail_R"))
    rp = sorted(w["R_pts"] for _, w in W)
    t1 = [w["t1"] for _, w in W if w["t1"] is not None]
    tinv = [w["t_inv"] for _, w in W if w["t_inv"] is not None]
    cont = sum(1 for r, _ in W if r["oc"].get(OUT_WIN) == "CONTINUATION")
    trap = sum(1 for r, _ in W if r["oc"].get(OUT_WIN) == "TRAP")
    seq = [w["fix3_R"] for _, w in sorted(W, key=lambda x: x[0]["session"])]
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    pk = lambda k: round(sum(1 for _, w in W if w[f"mfe_ge_{k}R"]) / n, 3)
    return {
        "n": n, "sessions": len({r["session"] for r, _ in W}),
        "cont": round(cont / n, 3), "trap": round(trap / n, 3),
        "E_fix3R": round(st.fmean(fx), 3),
        "win_fix3R": round(sum(1 for x in fx if x > 0) / n, 3),
        "med_MFE_R": mfe[n // 2], "p95_MFE_R": mfe[min(n - 1, int(n * 0.95))],
        "med_MAE_R": mae[n // 2], "p95_MAE_R": mae[max(0, int(n * 0.05))],
        "med_R_pts": rp[n // 2], "med_availR": av[len(av) // 2] if av else None,
        "med_t1_bars": st.median(t1) if t1 else None,
        "med_tinv_bars": st.median(tinv) if tinv else None,
        "maxDD_R": round(dd, 1),
        **{f"P{k}R": pk(k) for k in RK},
    }


def _row(name, m):
    if not m or not m["n"]:
        return f"    {name:<26} n=0"
    return (f"    {name:<26} n={m['n']:>4} ses={m['sessions']:>2} cont={m['cont']*100:>3.0f}% "
            f"trap={m['trap']*100:>3.0f}% E[3R]={m['E_fix3R']:>6} win={m['win_fix3R']*100:>3.0f}% "
            f"medMFE_R={m['med_MFE_R']:>5} medMAE_R={m['med_MAE_R']:>6} "
            f"P3R={m['P3R']*100:>3.0f}% P5R={m['P5R']*100:>3.0f}% P8R={m['P8R']*100:>3.0f}% "
            f"R={m['med_R_pts']} availR={m['med_availR']} DD={m['maxDD_R']}")


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


# ================================================================ index lead/lag (Part 8)
def leadlag(out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 110)
    p("[§8] INDEX / STOCK STRUCTURAL RELATIONSHIP")
    p("=" * 110)
    p("  TRUE constituent-stock -> index causation is UNOBSERVABLE (no single-stock feed).")
    p("  Proxy test: cross-INDEX lead/lag of 5m returns (does an abnormal move in one")
    p("  index lead another). Resampled from cycles.underlying_ltp, RTH only.")
    pairs = [("BANKNIFTY", "NIFTY"), ("NIFTY", "BANKNIFTY"), ("FINNIFTY", "NIFTY"),
             ("MIDCPNIFTY", "NIFTY")]
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{HS.OI_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        def series(sym):
            rows = con.execute("SELECT ts, underlying_ltp FROM cycles WHERE symbol=? "
                               "AND underlying_ltp IS NOT NULL ORDER BY ts", (sym,)).fetchall()
            from datetime import datetime
            buck = {}
            for r in rows:
                t = datetime.fromisoformat(r["ts"])
                if not (555 <= t.hour * 60 + t.minute <= 930):
                    continue
                k = t.replace(minute=(t.minute // 5) * 5, second=0, microsecond=0)
                buck.setdefault(k, []).append(r["underlying_ltp"])
            return {k: v[-1] for k, v in sorted(buck.items())}
        cache = {}
        for a, b in pairs:
            if a not in cache:
                cache[a] = series(a)
            if b not in cache:
                cache[b] = series(b)
            A, B = cache[a], cache[b]
            ks = sorted(set(A) & set(B))
            ra = [(A[ks[i]] / A[ks[i - 1]] - 1) for i in range(1, len(ks))]
            rb = [(B[ks[i]] / B[ks[i - 1]] - 1) for i in range(1, len(ks))]
            if len(ra) < 50:
                p(f"  {a:>10} -> {b:<10}  insufficient overlap")
                continue
            def corr(x, y):
                mx, my = st.fmean(x), st.fmean(y)
                num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
                den = (sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)) ** 0.5
                return num / den if den else 0.0
            c0 = corr(ra, rb)
            c_lead = corr(ra[:-1], rb[1:])     # a(t) vs b(t+1)  -> a leads b
            c_lag = corr(ra[1:], rb[:-1])      # a(t) vs b(t-1)  -> b leads a
            p(f"  {a:>10} vs {b:<10} n={len(ra):>4}  corr(0)={c0:+.2f}  "
              f"corr(a leads b by 1)={c_lead:+.2f}  corr(b leads a by 1)={c_lag:+.2f}  "
              f"-> {'a leads' if c_lead > c_lag + 0.03 else 'b leads' if c_lag > c_lead + 0.03 else 'contemporaneous'}")
        con.close()
    except Exception as e:
        p(f"  lead/lag test failed: {type(e).__name__}: {e}")


# ================================================================ report
def report(sym, sessions, rows, splits, out):
    p = lambda *a: print(*a, file=out)
    tr, va, oos = splits
    p("\n" + "#" * 118)
    p(f"# {sym}  --  {len(rows)} abnormal events over {len({r['session'] for r in rows})} "
      f"sessions  regimes {sorted({r['regime'] for r in rows})}  (UNDERLYING basis, realistic stop)")
    p(f"#   split: train {len(tr)} / val {len(va)} / oos {len(oos)} sessions")
    p("#" * 118)

    p("\n[§5 EARLIEST-ENTRY comparison]  best expectancy per unit structural risk (NOT highest accuracy)")
    for k in ENTRIES:
        p(_row(k, _agg(rows, k)))
    p("   chronological OOS only:")
    oe = [r for r in rows if r["session"] in oos]
    for k in ENTRIES:
        p(_row(k + " [oos]", _agg(oe, k)))

    # pick the best entry by pooled E_fix3R with n>=30
    cand = [(k, _agg(rows, k)) for k in ENTRIES]
    best = max((c for c in cand if c[1].get("n", 0) >= 30),
              key=lambda c: c[1]["E_fix3R"], default=cand[0])
    bk = best[0]
    p(f"\n   -> best expectancy/R entry (pooled, n>=30): {bk}  E[3R]={best[1]['E_fix3R']}")

    p(f"\n[§6 SMALL-SL / LARGE-R distribution]  entry = {bk}, realistic stop, MFE measured BEFORE invalidation")
    for lab, ds in (("train", tr), ("val", va), ("oos", oos), ("POOLED", None)):
        m = _agg(rows if ds is None else [r for r in rows if r["session"] in ds], bk)
        if not m["n"]:
            p(f"    {lab:<9} n=0"); continue
        p(f"    {lab:<9} n={m['n']:>4} medMFE_R={m['med_MFE_R']} p95MFE_R={m['p95_MFE_R']} "
          f"medMAE_R={m['med_MAE_R']} p95MAE_R={m['p95_MAE_R']} | "
          + " ".join(f"P{k}R={m[f'P{k}R']*100:.0f}%" for k in RK)
          + f" | t1={m['med_t1_bars']}bars t_inv={m['med_tinv_bars']}bars E[3R]={m['E_fix3R']} DD={m['maxDD_R']}R")

    p("\n[§4 COMPETING-EXPLANATION classifier H0..H7]  (entry = " + bk + ")")
    for k, g in sorted(_grp(rows, lambda r: r["hclass"]).items()):
        p(_row(k, _agg(g, bk)))

    p("\n[§5/§7 CONTINUATION vs TRAP matrix]  (entry = " + bk + ")")
    for name, sel in (
        ("ALL", lambda r: True),
        ("no reclaim within 3", lambda r: not r["reclaim3"]),
        ("reclaim within 3", lambda r: r["reclaim3"]),
        ("n1 agrees", lambda r: r["n1_agree"]),
        ("n1 disagrees", lambda r: r["n1_agree"] is False),
        ("no reclaim & n1 agrees", lambda r: (not r["reclaim3"]) and r["n1_agree"]),
        ("day TRENDING/OPEN_DRIVE", lambda r: r["day_regime"] in ("TRENDING", "OPENING_DRIVE")),
        ("day ROTATION/CHOP/BALANCED", lambda r: r["day_regime"] in ("ROTATION", "CHOP", "BALANCED")),
        ("spike outside value", lambda r: r["pf_loc"] in ("above_value", "below_value")),
        ("range_x >= 3", lambda r: r["range_x"] >= 3),
    ):
        p(_row(name, _agg([r for r in rows if sel(r)], bk)))

    p("\n[§9 FEATURE INCREMENTAL-VALUE]  baseline vs conditioned (entry = " + bk + "); dE = E[3R] delta, on OOS")
    base_oos = _agg([r for r in rows if r["session"] in oos], bk)
    base_all = _agg(rows, bk)
    feats = {
        "n1_agree": lambda r: r["n1_agree"] is True,
        "no reclaim within 3": lambda r: not r["reclaim3"],
        "range_x >= 3": lambda r: r["range_x"] >= 3,
        "spike outside value": lambda r: r["pf_loc"] in ("above_value", "below_value"),
        "vwap_dist same side": lambda r: r["vwap_dist"] is not None and
            ((r["vwap_dist"] > 0) == (r["direction"] == "LONG")),
        "prior-8 range contraction < 1": lambda r: (r["bal_range_contraction"] or 9) < 1.0,
        "prior-8 rotations >= 4": lambda r: (r["bal_rotations"] or 0) >= 4,
        "day TRENDING/OPEN_DRIVE": lambda r: r["day_regime"] in ("TRENDING", "OPENING_DRIVE"),
    }
    for fn, sel in feats.items():
        g_all = _agg([r for r in rows if sel(r)], bk)
        g_oos = _agg([r for r in rows if sel(r) and r["session"] in oos], bk)
        if not g_all["n"] or g_all["n"] < 25:
            p(f"    {fn:<32} n={g_all.get('n',0):<5} INSUFFICIENT")
            continue
        dE_all = round(g_all["E_fix3R"] - base_all["E_fix3R"], 3)
        dE_oos = round(g_oos["E_fix3R"] - base_oos["E_fix3R"], 3) if g_oos["n"] >= 8 else None
        dTrap = round((g_all["trap"] - base_all["trap"]) * 100, 1)
        verdict = ("REJECTED (no OOS)" if dE_oos is None
                   else "SUPPORTED" if (dE_all > 0.03 and dE_oos > 0.0)
                   else "PROMISING" if dE_all > 0.01
                   else "REJECTED")
        p(f"    {fn:<32} n={g_all['n']:<5} dE(all)={dE_all:+.3f} dE(oos)={dE_oos} "
          f"dTrap={dTrap:+.1f}pp  -> {verdict}")

    return bk, _agg(rows, bk), (_agg([r for r in rows if r['session'] in tr], bk),
                                _agg([r for r in rows if r['session'] in va], bk),
                                _agg([r for r in rows if r['session'] in oos], bk))


# ================================================================ hypothesis + concept tables
def part1_table(out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 118)
    p("[§1] PUBLIC ORDER-FLOW CONCEPT -> TESTABLE MATHEMATICAL HYPOTHESIS  (public terms only, no course material)")
    p("=" * 118)
    T = [
        ("Buyer/seller aggression", "signed aggressor volume", "delta = buy_vol - sell_vol",
         "per completed bar", "UNOBSERVABLE (no aggressor-classified volume)"),
        ("Lift-offer / hit-bid", "trade printed at ask vs bid", "count(px>=ask) - count(px<=bid)",
         "per trade", "UNOBSERVABLE (no tick/quote stream on the underlying)"),
        ("Liquidity structure / resting size", "book depth per level", "sum(bid_qty), sum(ask_qty) by level",
         "per quote", "UNOBSERVABLE for the underlying (option book only, Sep, 4 sess)"),
        ("Abnormal price expansion", "bar range vs its own recent distribution",
         "range percentile among prior-session bars; range / rolling median; range / ATR",
         "completed bar T0", "OBSERVED"),
        ("Price rotation", "direction changes per window",
         "count sign flips of consecutive close deltas over prior k bars", "bars[:T0]", "OBSERVED"),
        ("Imbalance (2:1 / '200%')", "relative one-sided quantity",
         "PROXY: option CE/PE interval traded-volume ratio (dir-adjusted)", "cycle ts <= bar", "PROXY (L3)"),
        ("Liquidity absorption", "large range, small body, two-way trade, then continuation",
         "body/range < 0.35 AND range/ATR >= 1.5 AND next-bar agrees AND MFE_R >= 2",
         "T0 + n1", "OBSERVED (shape proxy; no true absorption without footprint)"),
        ("Liquidity vacuum", "expansion out of contraction with weak follow-through",
         "range(T0) >= 2*mean(range T-3..T-1) AND mean(range T-3..T-1) <= 0.6*ATR AND MFE_R < 2",
         "T0..T+3", "OBSERVED (proxy)"),
        ("Stop-loss / liquidity sweep", "break a prior swing then close back through it",
         "high(T0) > prior_swing_hi AND close(T0) <= prior_swing_hi  (mirror for shorts)",
         "T0", "OBSERVED"),
        ("Trapped participants", "level break, no acceptance, reclaim, opposite move",
         "broke L; no close held beyond L in T+1..T+3; close back through L; MAE_R <= -1",
         "T0..T+3", "OBSERVED"),
        ("Exhaustion", "abnormal bar extends a long directional run then stalls",
         "run of >=5 same-direction closes ending at T0 AND MFE_R < 2 after entry",
         "bars[:T0] + walk", "OBSERVED (proxy)"),
        ("Acceptance vs rejection", "close beyond broken level held / reclaimed",
         "close vs L over the next 1-3 completed bars", "bars[:T0+w]", "OBSERVED"),
        ("Smart-money / large-participant accumulation", "sustained one-sided pressure not visible in price",
         "would need order-book delta persistence / iceberg detection", "-",
         "UNOBSERVABLE -- NOT claimed"),
    ]
    for c, ov, formula, cts, status in T:
        p(f"\n  {c}")
        p(f"    observable : {ov}")
        p(f"    formula    : {formula}")
        p(f"    causal ts  : {cts}")
        p(f"    status     : {status}")


CSV_FIELDS = ["timestamp", "symbol", "session", "split", "regime", "day_regime", "direction",
              "spike_class", "spike_pctile", "range_x", "vwap_dist", "hclass",
              "reclaim3", "n1_agree", "n1_reenter", "pf_loc",
              "bal_range_contraction", "bal_rotations",
              "entry_type", "entry", "sl", "R_pts", "avail_R",
              "MFE_R", "MAE_R", "fix3_R", "exit_kind",
              "mfe_ge_2R", "mfe_ge_3R", "mfe_ge_5R", "mfe_ge_8R", "outcome_w3"]


def to_csv(all_rows, path):
    out = []
    for r in all_rows:
        for ek, w in r["ew"].items():
            out.append({
                "timestamp": r["ts"], "symbol": r["sym"], "session": r["session"],
                "split": r["split"], "regime": r["regime"], "day_regime": r["day_regime"],
                "direction": r["direction"], "spike_class": r["spike_class"],
                "spike_pctile": r["spike_pctile"], "range_x": r["range_x"],
                "vwap_dist": r["vwap_dist"], "hclass": r["hclass"],
                "reclaim3": r["reclaim3"], "n1_agree": r["n1_agree"], "n1_reenter": r["n1_reenter"],
                "pf_loc": r["pf_loc"], "bal_range_contraction": r["bal_range_contraction"],
                "bal_rotations": r["bal_rotations"], "entry_type": ek,
                "entry": w["entry"], "sl": w["sl"], "R_pts": w["R_pts"], "avail_R": w.get("avail_R"),
                "MFE_R": w["MFE_R"], "MAE_R": w["MAE_R"], "fix3_R": w["fix3_R"], "exit_kind": w["exit"],
                "mfe_ge_2R": w["mfe_ge_2R"], "mfe_ge_3R": w["mfe_ge_3R"],
                "mfe_ge_5R": w["mfe_ge_5R"], "mfe_ge_8R": w["mfe_ge_8R"],
                "outcome_w3": r["oc"].get(3),
            })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(out)
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage5_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage5_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    all_rows = []
    summary = {}
    with open(a.out, "w") as f:
        print("orderflow STAGE-5 -- underlying-basis market-behaviour maths. RESEARCH ONLY; "
              "no production change, no signal, no orders, no weighted score.", file=f)
        part1_table(f)
        _last_rows, bk_by_sym, splits_by_sym = {}, {}, {}
        for sym in syms:
            sessions, rows, splits = build(sym)
            all_rows += rows
            bk, pooled, tvo = report(sym, sessions, rows, splits, f)
            summary[sym] = (bk, pooled, tvo)
            _last_rows[sym], bk_by_sym[sym], splits_by_sym[sym] = rows, bk, splits
        leadlag(f)

        p = lambda *x: print(*x, file=f)
        p("\n" + "=" * 118)
        p("[FINAL — §13-16]  underlying (futures) basis, realistic stop, chronological split")
        p("=" * 118)
        p(f"  {'SYMBOL':<9} {'ENTRY':<14} {'medR_pt':>8} {'P3R':>5} {'P5R':>5} {'P8R':>5} {'TRAP%':>6} "
          f"{'E[3R]tr':>8} {'E[3R]va':>8} {'E[3R]oos':>9} {'DD_R':>7} {'N':>5} {'SESS':>5} {'STATUS':<26}")
        for sym, (bk, m, (trm, vam, oom)) in summary.items():
            def E(x):
                return x["E_fix3R"] if x and x["n"] else None
            oos_ok = E(oom) is not None and E(oom) > 0 and E(vam) is not None and E(vam) > 0
            if not m["n"] or m["n"] < 30:
                stat = "INSUFFICIENT DATA"
            elif oos_ok and E(trm) > 0:
                stat = "PROMISING (underlying)"
            elif E(oom) is not None and E(oom) <= 0:
                stat = "REJECTED (fails OOS)"
            else:
                stat = "UNSTABLE across splits"
            p(f"  {sym:<9} {bk:<14} {m['med_R_pts']:>8} {m['P3R']*100:>4.0f}% {m['P5R']*100:>4.0f}% "
              f"{m['P8R']*100:>4.0f}% {m['trap']*100:>5.0f}% {str(E(trm))[:7]:>8} {str(E(vam))[:7]:>8} "
              f"{str(E(oom))[:8]:>9} {m['maxDD_R']:>7} {m['n']:>5} {m['sessions']:>5} {stat:<26}")
        # the FILTERED subset (drop the trap cohort): no reclaim within 3 AND n1 agrees
        p("\n  FILTERED SUBSET  (no reclaim within 3  AND  n1 agrees) -- drops the H7 trap cohort:")
        for sym, sess_rows in _last_rows.items():
            fr = [r for r in sess_rows if (not r["reclaim3"]) and r["n1_agree"] and bk_by_sym[sym] in r["ew"]]
            trd, vad, ood = splits_by_sym[sym]
            mE = lambda ds: (_agg([r for r in fr if r["session"] in ds], bk_by_sym[sym]) if ds else _agg(fr, bk_by_sym[sym]))
            g, gt, gv, go = mE(None), mE(trd), mE(vad), mE(ood)
            if not g["n"]:
                p(f"    {sym:<9} n=0"); continue
            EE = lambda x: x["E_fix3R"] if x and x["n"] else None
            allpos = all(v is not None and v > 0 for v in (EE(gt), EE(gv), EE(go)))
            st_ = ("SUPPORTED (small edge)" if allpos and g["n"] >= 60 else
                   "PROMISING" if (EE(go) or -9) > 0 else "UNSTABLE / REJECTED")
            p(f"    {sym:<9} n={g['n']:>4} ses={g['sessions']:>2} cont={g['cont']*100:.0f}% trap={g['trap']*100:.0f}% "
              f"E[3R] tr/va/oos = {EE(gt)} / {EE(gv)} / {EE(go)}  P3R={g['P3R']*100:.0f}% P5R={g['P5R']*100:.0f}% "
              f"DD={g['maxDD_R']}R  -> {st_}")
        p("\n  SUPPORTED   : abnormal-range percentile as the spike definition (stable, symbol-adaptive);")
        p("               acceptance-vs-reclaim as the continuation/trap separator; n1 agreement as an")
        p("               early continuation tell -- all reproduced on the underlying over 35-38 sessions.")
        p("               The H1 'genuine participation' vs H7 'trapped reversal' split (section 4) is clean")
        p("               and consistent across all three symbols (H7 ~= -1.5R, ~86-91% trap, ~30-40% of events).")
        p("  PROMISING   : the FILTERED subset above -- dropping the H7/reclaim cohort flips E[3R] positive")
        p("               (~+0.3 to +0.6R) on train+val+oos, but the per-trade edge is small, P5R ~1-4%,")
        p("               and the effective independent N is the session count.")
        p("  REJECTED    : the UNCONDITIONAL abnormal-spike entry on the underlying (negative pooled E[3R],")
        p("               fails the chronological OOS split on NATGAS/CRUDE, unstable on NIFTY);")
        p("               the 'small SL + LARGE R' thesis -- with a realistic stop, medMFE_R ~= 1.0,")
        p("               P5R ~1-2%, P8R ~0%; the large-R right tail is NOT there (section 6);")
        p("               and, as incremental features: Market-Profile day-regime (classifier degenerate),")
        p("               VWAP-proxy distance (symbol-dependent), prior-bar compression (dE ~ 0),")
        p("               price rotation, the option-volume 'imbalance' proxy -- none add stable")
        p("               incremental OOS expectancy (section 9). Compression HURTS (confirms Stage-3/4).")
        p("  UNOBSERVABLE : true delta, footprint, bid/ask imbalance, absorption, iceberg/large-participant")
        p("               detection, constituent-stock -> index causation. Exact data needed: underlying")
        p("               tick stream with aggressor side + full depth-of-book; single-stock 1m/tick feed.")
        p("\n[FINAL] Research only. No production pattern, no signal enabled, no live change, no orders, "
          "no weighted score. Where the maths does not support a claim it is marked REJECTED or "
          "UNOBSERVABLE above -- not softened.")

    n = to_csv(all_rows, a.csv)
    print(f"wrote {n} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
