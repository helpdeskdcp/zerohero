#!/usr/bin/env python3
"""
orderflow_stage3_validation.py -- RESEARCH ONLY. Stage-3 cross-session
validation of the Stage-1/2 hypothesis:

  ABNORMAL SPIKE -> POST-SPIKE REACTION -> ACCEPTANCE -> EARLIEST VALID ENTRY
  -> SMALL STRUCTURAL RISK -> LARGE AVAILABLE_R -> CONTINUATION (vs TRAP)

Now with ~36-39 independent sessions per symbol (2026-07-13 .. 09-04) from
/root/oi_dashboard/oi_history.db + zerohero histcap, so a chronological
train / validation / out-of-sample split is finally possible.

HARD RULES: research only; no production pattern; no live change; no orders;
NO weighted score. Strictly causal -- completed candles only, developing
volume/time profile from bars[:idx], OI deltas from <= event ts, thresholds
chosen for STABILITY not P&L, sessions split chronologically (never shuffled).

Reuses the pure helpers from orderflow_continuation_trap.py /
orderflow_entry_timing.py; drives them off orderflow_histsrc (multi-source).
Index-structural R (R = |entry - structural SL| in underlying points;
MFE_R / MAE_R = index favourable / adverse excursion / R).

Outputs:
  data/orderflow_stage3_events.csv          (§13 event-level dataset)
  data/orderflow_stage3_report_<date>.txt   (§1-§14 + final table + status)
"""
from __future__ import annotations

import argparse
import csv as _csv
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import orderflow_histsrc as HS
from scripts.orderflow_continuation_trap import (
    _classify_spike, _levels, _level_interaction, _prof_loc, _atr_series,
    _walk, _broken_level, _held_beyond, _reclaimed_by, _outcome, _struct_sl,
    _avail_R, _rejection,
)
from scripts.orderflow_entry_timing import (
    _n1, _first_close_beyond, _acc_entry_bar, _entry_stops, ACC_DEFS,
)
from scripts.orderflow_spike_ledger import _asof

RL = (1, 2, 3, 4, 5, 6, 8)
OUT_WIN = 3
SPIKE_PCTLS = (0.90, 0.95, 0.97, 0.98, 0.99)


# ================================================================ HSess
class HSess:
    """Sess-compatible session object built from historical source bars."""
    __slots__ = ("sym", "date", "src", "clean", "regime", "base", "avgvol",
                 "va", "frac_lo", "frac_hi", "oi")

    def __init__(self, sym, date, src):
        self.sym, self.date, self.src = sym, date, src
        raw = HS.session_bars(sym, date, src)
        self.clean = [b for b in raw if b["h"] >= b["l"] and b["h"] is not None]
        n = len(self.clean)
        self.base = [0.0] * n
        self.avgvol = [0.0] * n
        self.va = [None] * n
        rs = []
        last_va = None
        for i, b in enumerate(self.clean):
            self.base[i] = st.median(rs) if rs else 0.0
            if b["h"] > b["l"]:
                rs.append(b["h"] - b["l"])
            if i >= 15 and i % 3 == 0:
                last_va = _dev_va(self.clean[:i])
            self.va[i] = last_va
        self.frac_lo = [False] * n
        self.frac_hi = [False] * n
        for i in range(1, n - 1):
            c = self.clean
            self.frac_lo[i] = c[i]["l"] <= c[i - 1]["l"] and c[i]["l"] <= c[i + 1]["l"]
            self.frac_hi[i] = c[i]["h"] >= c[i - 1]["h"] and c[i]["h"] >= c[i + 1]["h"]
        self.regime = _regime(self.clean)
        self.oi = HS.session_oi_series(sym, date, src)


def _dev_va(bars, value_pct=0.70, nbins=48):
    """Time (TPO-style) value area -- equal weight per bar across its range.
    Volume-free, so it works on the volumeless oi_dashboard feed. Returns
    (VAL, POC, VAH)."""
    if len(bars) < 8:
        return None
    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    if hi <= lo:
        return None
    w = (hi - lo) / nbins
    cnt = [0.0] * nbins
    for b in bars:
        i0 = max(0, min(nbins - 1, int((b["l"] - lo) / w)))
        i1 = max(0, min(nbins - 1, int((b["h"] - lo) / w)))
        share = 1.0 / (i1 - i0 + 1)
        for i in range(i0, i1 + 1):
            cnt[i] += share
    poc_i = max(range(nbins), key=lambda i: cnt[i])
    total = sum(cnt)
    lo_i = hi_i = poc_i
    acc = cnt[poc_i]
    while acc < value_pct * total and (lo_i > 0 or hi_i < nbins - 1):
        left = cnt[lo_i - 1] if lo_i > 0 else -1
        right = cnt[hi_i + 1] if hi_i < nbins - 1 else -1
        if right >= left:
            hi_i += 1
            acc += cnt[hi_i]
        else:
            lo_i -= 1
            acc += cnt[lo_i]
    price = lambda i: lo + (i + 0.5) * w
    return (round(price(lo_i), 2), round(price(poc_i), 2), round(price(hi_i), 2))


def _regime(clean):
    if len(clean) < 6:
        return "NA"
    o0 = clean[0].get("o")
    net = clean[-1]["c"] - (o0 if o0 is not None else clean[0]["c"])
    rng = max(b["h"] for b in clean) - min(b["l"] for b in clean)
    if rng <= 0:
        return "NA"
    if abs(net) >= 0.5 * rng:
        return "TREND_UP" if net > 0 else "TREND_DOWN"
    return "CHOP"


# ================================================================ OI feature
def _oi_at(oi_series, ref_price, side, bar_ts):
    strikes = sorted({k[0] for k in oi_series if k[1] == side and oi_series[k]})
    if not strikes:
        return None, None, None
    ser = oi_series[(min(strikes, key=lambda s: abs(s - ref_price)), side)]
    oi_now = _asof(ser, bar_ts, 2)
    # ~2 bars earlier (10 min) baseline
    from datetime import datetime, timedelta
    try:
        pv = datetime.fromisoformat(bar_ts.replace("Z", "")) - timedelta(minutes=10)
        prev_ts = pv.isoformat()
    except Exception:
        prev_ts = bar_ts
    oi_prev = _asof(ser, prev_ts, 2)
    d = (oi_now - oi_prev) if (oi_now is not None and oi_prev is not None) else None
    return oi_now, d, _asof(ser, bar_ts, 3)


# ================================================================ §1 spike defs
def spike_percentile(s, idx):
    r = s.clean[idx]["h"] - s.clean[idx]["l"]
    prior = [x["h"] - x["l"] for x in s.clean[:idx] if x["h"] > x["l"]]
    if len(prior) < 12:
        return None
    return sum(1 for x in prior if x <= r) / len(prior)


def spike_defs_survey(sess_by_sym, out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 100)
    p("[§1] SYMBOL-ADAPTIVE ABNORMAL-SPIKE DETECTION  (range-percentile; chosen for STABILITY, not P&L)")
    p("=" * 100)
    chosen = {}
    for sym, sessions in sess_by_sym.items():
        p(f"\n  {sym}:  {len(sessions)} sessions, regimes "
          f"{sorted({s.regime for s in sessions})}")
        p(f"    {'pctile':>7} {'events':>7} {'ev/sess':>8} {'sess>=1':>8} {'CoV(ev/sess)':>13} {'regimes':>9}")
        best = None
        for thr in SPIKE_PCTLS:
            per = []
            for s in sessions:
                n = sum(1 for i in range(4, len(s.clean) - 1)
                        if (spike_percentile(s, i) or 0) >= thr)
                per.append(n)
            tot = sum(per)
            withev = sum(1 for x in per if x >= 1)
            mean = st.fmean(per) if per else 0
            cov = (st.pstdev(per) / mean) if mean > 0 else 9.9
            regs = len({s.regime for s, n in zip(sessions, per) if n})
            p(f"    {thr:>7.2f} {tot:>7} {mean:>8.2f} {withev:>4}/{len(sessions):<3} "
              f"{cov:>13.2f} {regs:>9}")
            # stability pick: >=50 events, >=60% sessions with an event, min CoV
            if tot >= 50 and withev >= 0.6 * len(sessions):
                if best is None or cov < best[1]:
                    best = (thr, cov, tot)
        chosen[sym] = best[0] if best else 0.97
        p(f"    -> adaptive threshold for {sym}: pctile >= {chosen[sym]}  "
          f"({'stability pick' if best else 'fallback 0.97 -- no threshold met the stability bar'})")
    return chosen


# ================================================================ event build
def collect(sym, sessions, thr):
    ev = []
    for s in sessions:
        for idx in range(4, len(s.clean) - 1):
            if s.base[idx] <= 0:
                continue
            pct = spike_percentile(s, idx)
            if pct is None or pct < thr:
                continue
            b = s.clean[idx]
            cls, *_ = _classify_spike(b)
            direction = ("LONG" if cls.startswith("bull") else "SHORT" if cls.startswith("bear")
                         else ("LONG" if (b.get("c") or 0) >= (b.get("o") or 0) else "SHORT"))
            lv = _levels(s, idx)
            L, _ = _broken_level(s, idx, direction, lv)
            li, _ = _level_interaction(s, idx, direction, lv)
            loc, dpoc, dvah, dval = _prof_loc(s, idx, b["c"])
            pcls = ("NA" if loc == "NA" else
                    "acceptance_outside_value" if (loc in ("ABOVE_VA", "BELOW_VA") and li == "C_broke_accepted") else
                    "above_VAH" if loc == "ABOVE_VA" else "below_VAL" if loc == "BELOW_VA" else
                    "near_POC" if loc == "AT_POC" else "inside_value")
            oi_now, oi_d, dlt = _oi_at(s.oi, b["c"], "CE" if direction == "LONG" else "PE", b["bar_start"])

            ref_sl = _struct_sl(s, idx, idx + 1, direction, lv, "spike")
            ref = _walk(s.clean, idx + 1, b["c"], ref_sl, direction)
            if ref is None:
                continue
            R0 = ref["R_pts"]
            n1 = _n1(s, idx, direction, L)

            # ----- 5 entry timings (§2) -----
            T = {}
            T["A_spikeclose"] = (b["c"], idx + 1, b["bar_start"])
            if n1 and n1["agree"] and idx + 2 < len(s.clean):
                T["B_reaction"] = (s.clean[idx + 1]["c"], idx + 2, s.clean[idx + 1]["bar_start"])
                T["C_n1agree"] = T["B_reaction"]        # same bar; kept as a labelled view
            cj = _acc_entry_bar(s, idx, direction, L, "close_no_reclaim")
            if cj is not None and cj + 1 < len(s.clean):
                T["D_earlyaccept"] = (s.clean[cj]["c"], cj + 1, s.clean[cj]["bar_start"])
            dj = _acc_entry_bar(s, idx, direction, L, "hold2")
            if dj is not None and dj + 1 < len(s.clean):
                T["E_fullaccept"] = (s.clean[dj]["c"], dj + 1, s.clean[dj]["bar_start"])
            # F: fully-causal confirmation -- enter at the close of bar idx+3 IFF,
            # judged only from bars idx+1..idx+3: price closed beyond L at least
            # once AND never closed back through L (no reclaim). Zero look-ahead:
            # every input is a completed candle at or before the entry bar.
            if L is not None and idx + 4 < len(s.clean):
                w3 = s.clean[idx + 1:idx + 4]
                beyond_once = any((x["c"] > L) if direction == "LONG" else (x["c"] < L) for x in w3)
                reclaimed = any((x["c"] < L) if direction == "LONG" else (x["c"] > L) for x in w3)
                if beyond_once and not reclaimed:
                    T["F_confirm3"] = (s.clean[idx + 3]["c"], idx + 4, s.clean[idx + 3]["bar_start"])

            E = {}
            for k, (ep, ebar, vts) in T.items():
                stops = _entry_stops(s, idx, ebar, direction, lv)
                sw = {}
                for sn, sp in stops.items():
                    w = _walk(s.clean, ebar, ep, sp, direction)
                    if w:
                        sw[sn] = {**w, "avail_R": _avail_R(lv, ep, w["R_pts"], direction), "sl": round(sp, 2)}
                if not sw:
                    continue
                prim = "reaction" if "reaction" in sw else ("window" if "window" in sw else "spike")
                prim = prim if prim in sw else next(iter(sw))
                E[k] = {"entry": round(ep, 2), "ebar": ebar, "valid_ts": vts,
                        "prim": prim, "walk": sw[prim], "stops": sw,
                        "runner": _runner(s, ebar, ep, sw[prim]["sl"], direction)}

            oc = {w: _outcome(s, idx, direction, L, R0, w) for w in (1, 2, 3, 5)}
            reclaim3 = _reclaimed_by(s, idx, direction, L, 3)

            ev.append({
                "sym": sym, "session": s.date, "src": s.src, "regime": s.regime,
                "ts": b["bar_start"], "direction": direction, "spike_class": cls,
                "spike_pctile": round(pct, 3), "spike_range": round(b["h"] - b["l"], 2),
                "spike_high": b["h"], "spike_low": b["l"], "spike_close": b["c"],
                "range_x": round((b["h"] - b["l"]) / s.base[idx], 2),
                "level_class": li, "broke_level": L is not None, "L": L,
                "profile_class": pcls, "dist_poc": dpoc, "dist_vah": dvah, "dist_val": dval,
                "oi_now": oi_now, "oi_delta": oi_d, "entry_delta": dlt,
                "n1_agree": (n1 or {}).get("agree"),
                "n1_reenter": (n1 or {}).get("reenter_spike"),
                "n1_beyond": (n1 or {}).get("beyond_level"),
                "reclaim_within3": reclaim3 is not None,
                "R0": R0, "oc": oc, "E": E, "ref": ref,
            })
    return ev


# ================================================================ §8 runner
def _runner(s, ebar, entry, sl, direction):
    R = abs(entry - sl)
    if R <= 0:
        return None
    c = s.clean
    out = {}
    for mode in ("fix3R", "half", "full"):
        t3 = entry + 3 * R if direction == "LONG" else entry - 3 * R
        maxf = 0.0
        cur = sl
        hit1 = hit2 = hit3 = False
        booked = 0.0
        frac = 1.0
        fin = None
        for i in range(ebar, len(c)):
            bb = c[i]
            fav = (bb["h"] - entry) if direction == "LONG" else (entry - bb["l"])
            maxf = max(maxf, fav)
            rr = maxf / R
            if not hit1 and rr >= 1:
                hit1 = True
                cur = entry              # breakeven at +1R
            if not hit2 and rr >= 2:
                hit2 = True
                cur = entry + R if direction == "LONG" else entry - R   # +1R at +2R
            if not hit3 and ((bb["h"] >= t3) if direction == "LONG" else (bb["l"] <= t3)):
                hit3 = True
                if mode == "fix3R":
                    fin = 3.0
                    break
                if mode == "half":
                    booked, frac, cur = 1.5, 0.5, (entry + R if direction == "LONG" else entry - R)
            if hit3 and mode in ("half", "full"):
                for j in range(i, ebar, -1):
                    if direction == "LONG" and s.frac_lo[j] and c[j]["l"] > cur:
                        cur = c[j]["l"]
                        break
                    if direction == "SHORT" and s.frac_hi[j] and c[j]["h"] < cur:
                        cur = c[j]["h"]
                        break
            hit_stop = (bb["l"] <= cur) if direction == "LONG" else (bb["h"] >= cur)
            if hit_stop:
                rem = ((cur - entry) / R) if direction == "LONG" else ((entry - cur) / R)
                fin = booked + frac * rem
                break
        if fin is None:
            last = c[-1]["c"]
            rem = ((last - entry) / R) if direction == "LONG" else ((entry - last) / R)
            fin = booked + frac * rem
        out[mode] = {"final_R": round(fin, 3), "max_R": round(maxf / R, 2),
                     "giveback_R": round(maxf / R - fin, 2)}
    return out


# ================================================================ stats
def _mstats(rows, ekey, stop=None):
    W = []
    for r in rows:
        e = r["E"].get(ekey)
        if not e:
            continue
        w = e["stops"].get(stop) if stop else e["walk"]
        if w:
            W.append((r, w))
    if not W:
        return None
    n = len(W)
    mfe = sorted(w["MFE_R"] for _, w in W)
    mae = sorted(w["MAE_R"] for _, w in W)
    mx = sorted(w["max_R"] for _, w in W)
    av = sorted(w["avail_R"] for _, w in W if w.get("avail_R"))
    rp = sorted(w["R_pts"] for _, w in W)
    cont = sum(1 for r, _ in W if r["oc"].get(OUT_WIN) == "CONTINUATION")
    trap = sum(1 for r, _ in W if r["oc"].get(OUT_WIN) == "TRAP")
    pk = lambda k: round(sum(1 for _, w in W if w.get(f"reached_{k}R")) / n, 3)
    # expectancy proxy: fixed-3R final_R across events that had this entry
    fr = [r["E"][ekey]["runner"]["fix3R"]["final_R"] for r, _ in W
          if r["E"][ekey].get("runner")]
    return {
        "n": n, "cont": round(cont / n, 3), "trap": round(trap / n, 3),
        "medMFE_R": mfe[n // 2], "medMAE_R": mae[n // 2],
        "med_maxR": mx[n // 2], "p90_maxR": mx[min(n - 1, int(n * 0.9))],
        "med_availR": av[len(av) // 2] if av else None,
        "med_R_pts": rp[n // 2],
        **{f"P{k}R": pk(k) for k in RL},
        "exp_fix3R": round(st.fmean(fr), 3) if fr else None,
        "sessions": len({r["session"] for r, _ in W}),
    }


def _erow(name, m):
    if not m:
        return f"    {name:<28} n=0"
    return (f"    {name:<28} n={m['n']:>4} ses={m['sessions']:>3} cont={m['cont']*100:>3.0f}% "
            f"trap={m['trap']*100:>3.0f}% mMAE_R={m['medMAE_R']:>6} mMFE_R={m['medMFE_R']:>5} "
            f"P3R={m['P3R']*100:>3.0f}% P4R={m['P4R']*100:>3.0f}% P5R={m['P5R']*100:>3.0f}% "
            f"P6R={m['P6R']*100:>3.0f}% availR={m['med_availR']} R={m['med_R_pts']} "
            f"E[3R]={m['exp_fix3R']}")


# ================================================================ report
def _split(sessions):
    ds = [s.date for s in sessions]
    n = len(ds)
    tr, va = int(n * 0.55), int(n * 0.77)
    return set(ds[:tr]), set(ds[tr:va]), set(ds[va:])


def report(sym, sessions, ev, thr, out):
    p = lambda *a: print(*a, file=out)
    tr, va, oos = _split(sessions)
    p("\n" + "#" * 116)
    p(f"# {sym}  --  {len(ev)} events over {len({e['session'] for e in ev})} sessions "
      f"(spike pctile >= {thr})  regimes {sorted({e['regime'] for e in ev})}")
    p(f"#   chronological split: train {len(tr)} sess / val {len(va)} sess / oos {len(oos)} sess")
    p("#" * 116)
    ekeys = ("A_spikeclose", "B_reaction", "D_earlyaccept", "E_fullaccept", "F_confirm3")

    p("\n[§2/§9 ENTRY-TIMING comparison]  (each entry, its primary structural stop, outcome window 3)")
    for k in ekeys:
        p(_erow(k, _mstats(ev, k)))

    p("\n[§9 same, chronological OUT-OF-SAMPLE only]")
    oosev = [e for e in ev if e["session"] in oos]
    p(f"    (oos: {len(oosev)} events / {len(oos)} sessions)")
    for k in ekeys:
        p(_erow(k + " [oos]", _mstats(oosev, k)))

    p("\n[§6 STRUCTURAL STOP]  (early-acceptance entry D; median over events)")
    de = [e for e in ev if "F_confirm3" in e["E"]]
    for sn in ("spike", "reaction", "window", "swing", "prevstruct"):
        m = _mstats(de, "F_confirm3", sn)
        if m:
            p(f"    stop={sn:<11} n={m['n']:>4} medR={m['med_R_pts']:>6}pt medMAE_R={m['medMAE_R']:>6} "
              f"medMFE_R={m['medMFE_R']:>5} availR={m['med_availR']} P4R={m['P4R']*100:.0f}% P5R={m['P5R']*100:.0f}%")

    p("\n[§7/§10 LARGE-R + conditional probabilities]  (spike-close entry unless noted)")
    allm = _mstats(ev, "A_spikeclose")
    p("    ALL:            " + " ".join(f"P{k}R={allm[f'P{k}R']*100:.0f}%" for k in RL)
      + f"   med_maxR={allm['med_maxR']} p90_maxR={allm['p90_maxR']}")
    acc = [e for e in ev if e["broke_level"] and e["E"].get("E_fullaccept")]
    accm = _mstats(acc, "E_fullaccept")
    if accm:
        p("    | full-acceptance " + " ".join(f"P{k}R={accm[f'P{k}R']*100:.0f}%" for k in (3, 4, 5, 6)))
    n1a = _mstats([e for e in ev if e["n1_agree"]], "A_spikeclose")
    if n1a:
        p("    | n1 agrees      " + " ".join(f"P{k}R={n1a[f'P{k}R']*100:.0f}%" for k in (3, 4, 5, 6)))
    ea = _mstats([e for e in ev if e["E"].get("D_earlyaccept")], "D_earlyaccept")
    if ea:
        p("    | early-accept   " + " ".join(f"P{k}R={ea[f'P{k}R']*100:.0f}%" for k in (3, 4, 5, 6)))

    p("\n[§8 R-MULTIPLE profit management]  (events reaching 3R on the spike-close entry)")
    r3 = [e for e in ev if e["ref"]["reached_3R"] and e["E"].get("A_spikeclose", {}).get("runner")]
    for mode in ("fix3R", "half", "full"):
        fr = [e["E"]["A_spikeclose"]["runner"][mode]["final_R"] for e in r3]
        gb = [e["E"]["A_spikeclose"]["runner"][mode]["giveback_R"] for e in r3]
        mx = [e["E"]["A_spikeclose"]["runner"][mode]["max_R"] for e in r3]
        if fr:
            wins = sum(1 for x in fr if x > 0)
            p(f"    {mode:<8} n={len(fr):>3} E[final_R]={st.fmean(fr):.2f} med={st.median(fr):.2f} "
              f"win%={wins/len(fr)*100:.0f} med_maxR={st.median(mx):.2f} med_giveback={st.median(gb):.2f}")

    p("\n[§5 SPIKE LOCATION]  (spike-close entry)")
    for k, g in sorted(_grp(ev, lambda e: e["profile_class"]).items()):
        p(_erow("loc " + str(k), _mstats(g, "A_spikeclose")))
    for k, g in sorted(_grp(ev, lambda e: e["level_class"]).items()):
        p(_erow("lvl " + str(k), _mstats(g, "A_spikeclose")))

    p("\n[§10 RETAIL-TRAP hypothesis]")
    p(_erow("n1 AGREES", _mstats([e for e in ev if e["n1_agree"]], "A_spikeclose")))
    p(_erow("n1 DISAGREES", _mstats([e for e in ev if e["n1_agree"] is False], "A_spikeclose")))
    p(_erow("n1 re-enters spike range", _mstats([e for e in ev if e["n1_reenter"]], "A_spikeclose")))
    p(_erow("reclaim within 3", _mstats([e for e in ev if e["reclaim_within3"]], "A_spikeclose")))
    p(_erow("no reclaim within 3", _mstats([e for e in ev if e["broke_level"] and not e["reclaim_within3"]], "A_spikeclose")))

    p("\n[§9 OI / §10 volume as secondary evidence]  (early-accept entry, controlled on level=C_broke_accepted)")
    base = [e for e in ev if e["level_class"] == "C_broke_accepted" and e["E"].get("D_earlyaccept")]
    p(_erow("C_broke_accepted (base)", _mstats(base, "D_earlyaccept")))
    oi_same = [e for e in base if e["oi_delta"] is not None
               and ((e["direction"] == "LONG") == (e["oi_delta"] > 0))]
    p(_erow(" + OI rising w/ price", _mstats(oi_same, "D_earlyaccept")))
    p(_erow(" + OI falling w/ price", _mstats([e for e in base if e["oi_delta"] is not None and e not in oi_same], "D_earlyaccept")))

    _headline_setup(sym, ev, tr, va, oos, out)
    _qc(sym, sessions, ev, out)


def _grp(rows, fn):
    g = {}
    for r in rows:
        g.setdefault(fn(r), []).append(r)
    return g


HKEY = "D_earlyaccept"      # Stage-2 recommended entry: first close beyond the
                            # broken level with no same-bar reclaim (fully causal,
                            # ~1 bar after the spike). F_confirm3 kept as a
                            # later-confirmation comparison in the entry table.


def hset_core(evs):
    """CORE defensible setup (Stage-2 recommendation, validated here):
    abnormal spike (range pctile >= adaptive) + EARLY-ACCEPTANCE entry
    (close of the first bar that closes beyond the broken level without
    the same bar wicking back through -- ~1 bar after the spike, fully
    causal) + reaction-candle (idx+1) low/high SL. No available_R / n1
    AND-filters (those crush the sample)."""
    return [e for e in evs
            if HKEY in e["E"] and e["E"][HKEY]["stops"].get("reaction")]


def hset_strict(evs):
    """STRICT: CORE + n1 agrees + available_R >= 3 on the reaction stop."""
    return [e for e in hset_core(evs)
            if e["n1_agree"]
            and (e["E"][HKEY]["stops"]["reaction"].get("avail_R") or 0) >= 3]


def _dd_of(rows):
    seq = [r["E"][HKEY]["runner"]["fix3R"]["final_R"]
           for r in sorted(rows, key=lambda x: x["session"]) if r["E"][HKEY].get("runner")]
    peak = cum = dd = 0.0
    for x in seq:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return round(dd, 1)


def _exp_of(rows):
    fr = [r["E"][HKEY]["runner"]["fix3R"]["final_R"]
          for r in rows if r["E"][HKEY].get("runner")]
    return round(st.fmean(fr), 3) if fr else None


def _headline_setup(sym, ev, tr, va, oos, out):
    p = lambda *a: print(*a, file=out)
    for name, hset in (("CORE", hset_core), ("STRICT (+n1-agree +availR>=3)", hset_strict)):
        p(f"\n[§14 HEADLINE SETUP -- {name}]  early-acceptance entry (1st close beyond level, no same-bar reclaim) + reaction-candle SL"
          + ("" if name == "CORE" else "  (+ n1 agrees, available_R>=3)"))
        for lab, ds in (("TRAIN", tr), ("VALIDATION", va), ("OUT-OF-SAMPLE", oos), ("POOLED", None)):
            rows = hset(ev if ds is None else [e for e in ev if e["session"] in ds])
            m = _mstats(rows, HKEY, "reaction")
            if not m:
                p(f"    {lab:<13} n=0")
                continue
            p(f"    {lab:<13} n={m['n']:>3} ses={m['sessions']:>2} cont={m['cont']*100:.0f}% "
              f"trap={m['trap']*100:.0f}% medR={m['med_R_pts']}pt medMAE_R={m['medMAE_R']} "
              f"availR={m['med_availR']} P3R={m['P3R']*100:.0f}% P4R={m['P4R']*100:.0f}% "
              f"P5R={m['P5R']*100:.0f}% P6R={m['P6R']*100:.0f}% E[fix3R]={_exp_of(rows)} maxDD={_dd_of(rows)}R")


def _qc(sym, sessions, ev, out):
    p = lambda *a: print(*a, file=out)
    p("\n[§12 STATISTICAL QUALITY CONTROL]")
    n_sess = len({e["session"] for e in ev})
    regs = {}
    for e in ev:
        regs[e["regime"]] = regs.get(e["regime"], 0) + 1
    p(f"    events={len(ev)}  sessions_with_events={n_sess}/{len(sessions)}  regimes={regs}")
    by_src = {}
    for e in ev:
        by_src[e["src"]] = by_src.get(e["src"], 0) + 1
    p(f"    by bar source: {by_src}   (robustness: cycles_resampled highs/lows are slightly understated)")
    # single-session dominance on the CORE headline setup's fixed-3R final_R
    hs = [e for e in hset_core(ev) if e["E"][HKEY].get("runner")]
    if hs:
        tot = sum(e["E"][HKEY]["runner"]["fix3R"]["final_R"] for e in hs)
        by_s = {}
        for e in hs:
            by_s[e["session"]] = by_s.get(e["session"], 0) + e["E"][HKEY]["runner"]["fix3R"]["final_R"]
        if by_s:
            dom = max(by_s.items(), key=lambda kv: abs(kv[1]))
            p(f"    headline setup: {len(hs)} trades over {len(by_s)} sessions, total {tot:.1f}R (fix3R); "
              f"largest single-session contribution {dom[0]} = {dom[1]:.1f}R "
              f"({'DOMINATES' if abs(dom[1]) > 0.5 * abs(tot) and tot != 0 else 'no single-session dominance'})")
            # leave-one-session-out sign stability
            flips = 0
            for hold in by_s:
                if (tot - by_s[hold]) * tot < 0:
                    flips += 1
            p(f"    leave-one-session-out: {flips}/{len(by_s)} holdouts flip the total-R sign")


# ================================================================ CSV (§13)
CSV_FIELDS = ["timestamp", "symbol", "session", "bar_src", "regime",
              "spike_percentile", "spike_range", "range_x", "spike_direction",
              "spike_high", "spike_low", "spike_close",
              "n1_agree", "n1_reenter", "reclaim_within3", "acceptance_early_bar",
              "level_class", "profile_class", "dist_poc", "dist_vah", "dist_val",
              "oi_now", "oi_delta", "entry_delta",
              "entry_type", "entry_price", "initial_SL", "R_pts", "available_R",
              "MFE_R", "MAE_R", "max_R", "reached_3R", "reached_4R", "reached_5R", "reached_6R",
              "fixed3R_final_R", "runner50_final_R", "runner100_final_R", "runner_giveback",
              "outcome_w3"]


def to_csv(all_ev, path):
    rows = []
    for e in all_ev:
        for ek, E in e["E"].items():
            w = E["walk"]
            rn = E.get("runner") or {}
            rows.append({
                "timestamp": e["ts"], "symbol": e["sym"], "session": e["session"],
                "bar_src": e["src"], "regime": e["regime"],
                "spike_percentile": e["spike_pctile"], "spike_range": e["spike_range"],
                "range_x": e["range_x"], "spike_direction": e["direction"],
                "spike_high": e["spike_high"], "spike_low": e["spike_low"],
                "spike_close": e["spike_close"], "n1_agree": e["n1_agree"],
                "n1_reenter": e["n1_reenter"], "reclaim_within3": e["reclaim_within3"],
                "acceptance_early_bar": "D_earlyaccept" in e["E"],
                "level_class": e["level_class"], "profile_class": e["profile_class"],
                "dist_poc": e["dist_poc"], "dist_vah": e["dist_vah"], "dist_val": e["dist_val"],
                "oi_now": e["oi_now"], "oi_delta": e["oi_delta"], "entry_delta": e["entry_delta"],
                "entry_type": ek, "entry_price": E["entry"], "initial_SL": w.get("sl"),
                "R_pts": w["R_pts"], "available_R": w.get("avail_R"),
                "MFE_R": w["MFE_R"], "MAE_R": w["MAE_R"], "max_R": w["max_R"],
                "reached_3R": w["reached_3R"], "reached_4R": w["reached_4R"],
                "reached_5R": w["reached_5R"], "reached_6R": w["reached_6R"],
                "fixed3R_final_R": rn.get("fix3R", {}).get("final_R"),
                "runner50_final_R": rn.get("half", {}).get("final_R"),
                "runner100_final_R": rn.get("full", {}).get("final_R"),
                "runner_giveback": rn.get("full", {}).get("giveback_R"),
                "outcome_w3": e["oc"].get(3),
            })
    with open(path, "w", newline="") as f:
        wr = _csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    return len(rows)


# ================================================================ final table + status
def final_table(per_sym, out):
    p = lambda *a: print(*a, file=out)
    p("\n" + "=" * 128)
    p("[§14 FINAL TABLE]  CORE setup = abnormal spike(pctile>=adaptive) + early-acceptance entry (1st close beyond level, no same-bar reclaim) + reaction-candle SL  |  R is INDEX-STRUCTURAL (underlying points), NOT option premium P&L")
    p("=" * 128)
    p(f"  {'SYMBOL':<9} {'ENTRY':<14} {'SL':<10} {'medR':>6} {'P3R':>5} {'P4R':>5} {'P5R':>5} "
      f"{'P6R':>5} {'TRAP%':>6} {'E[3R]':>7} {'maxDD_R':>8} {'N':>4} {'SESS':>5} {'STATUS':<28}")
    for sym, d in per_sym.items():
        m = d["headline_pooled"]
        st_ = d["status"]
        if not m:
            p(f"  {sym:<9} {'-':<14} {'-':<10} {'—':>6} {'—':>5} {'—':>5} {'—':>5} {'—':>5} "
              f"{'—':>6} {'—':>7} {'—':>8} {0:>4} {0:>5} {st_:<28}")
            continue
        p(f"  {sym:<9} {'early-accept':<14} {'reaction-lo/hi':<10} {m['med_R_pts']:>6} "
          f"{m['P3R']*100:>4.0f}% {m['P4R']*100:>4.0f}% {m['P5R']*100:>4.0f}% {m['P6R']*100:>4.0f}% "
          f"{m['trap']*100:>5.0f}% {str(d['exp'])[:6]:>7} {str(d['maxdd'])[:7]:>8} {m['n']:>4} "
          f"{m['sessions']:>5} {st_:<28}")
    p("\n  CAVEATS ON THE STATUS (why it is not stronger than PROMISING):")
    p("   * R here is INDEX-STRUCTURAL. Stage-1 measured the ATM option premium captures ~0.4x")
    p("     the index move -> apply a ~2.5x haircut + spread + theta to translate E[3R] to a")
    p("     tradable option-premium expectancy (most of the margin is consumed).")
    p("   * medMAE_R ~ -1.6 to -1.9: the 5m bar that hits the reaction-candle stop typically")
    p("     overshoots it by ~0.6-0.9R -> the loss side is understated by the fixed-3R model.")
    p("   * NATURALGAS median R = 0.23 pt is below realistic slippage -> its R-multiples are")
    p("     inflated by an untradably tight stop; treat NATGAS numbers as directional only.")
    p("   * intraday events are correlated; effective independent N ~ the session count (33-38).")


def _status(tr_m, va_m, oos_m):
    ms = [x for x in (tr_m, va_m, oos_m) if x]
    if len(ms) < 3 or any(x["n"] < 8 for x in ms):
        return "INSUFFICIENT DATA"
    # ceiling is PROMISING: index-structural R + unmodeled premium haircut/slippage
    # mean the tradable edge is not demonstrated even when the splits agree.
    exps = [x.get("exp_fix3R") for x in ms]
    if all(e is not None and e > 0 for e in exps) and va_m["cont"] > 0.5 and oos_m["cont"] > 0.5:
        return "PROMISING - MORE DATA REQUIRED"
    if max(x["cont"] for x in ms) - min(x["cont"] for x in ms) > 0.35:
        return "UNSTABLE across splits"
    return "NO ESTABLISHED EDGE"
    # (dead code below kept for reference of the earlier thresholds)
    conts = [x["cont"] for x in ms]
    p3s = [x["P3R"] for x in ms]
    # SUPPORTED: continuation stays > 0.5 and P3R stays > 0.20 on val AND oos
    if va_m["cont"] > 0.5 and oos_m["cont"] > 0.5 and va_m["P3R"] > 0.2 and oos_m["P3R"] > 0.2:
        return "SUPPORTED (small sample)"
    if max(conts) - min(conts) > 0.35 or (va_m["cont"] < 0.4 or oos_m["cont"] < 0.4):
        return "UNSTABLE across splits"
    return "PROMISING - MORE DATA REQUIRED"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,NATURALGAS,CRUDEOIL")
    ap.add_argument("--out", default="data/orderflow_stage3_report_2026-09-06.txt")
    ap.add_argument("--csv", default="data/orderflow_stage3_events.csv")
    a = ap.parse_args()
    syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]

    with open(a.out, "w") as f:
        print("orderflow STAGE-3 validation -- RESEARCH ONLY, no production change, no score", file=f)
        sess_by_sym = {}
        for sym in syms:
            ss = HS.sessions(sym)
            sess_by_sym[sym] = [HSess(sym, d, src) for d, src in ss]
        chosen = spike_defs_survey(sess_by_sym, f)

        per_sym = {}
        all_ev = []
        for sym in syms:
            sessions = sess_by_sym[sym]
            ev = collect(sym, sessions, chosen[sym])
            all_ev += ev
            report(sym, sessions, ev, chosen[sym], f)
            tr, va, oos = _split(sessions)
            core = hset_core(ev)
            tr_m = _mstats([e for e in core if e["session"] in tr], HKEY, "reaction")
            va_m = _mstats([e for e in core if e["session"] in va], HKEY, "reaction")
            oos_m = _mstats([e for e in core if e["session"] in oos], HKEY, "reaction")
            per_sym[sym] = {"headline_pooled": _mstats(core, HKEY, "reaction"),
                            "status": _status(tr_m, va_m, oos_m),
                            "exp": _exp_of(core), "maxdd": _dd_of(core)}
        final_table(per_sym, f)
        print("\n[FINAL] Research only. No production pattern implemented, no signal enabled, "
              "no live change, no orders, no weighted score. Statuses above are the ceiling "
              "the data supports -- never 'PROVEN' from this sample.", file=f)

    nrows = to_csv(all_ev, a.csv)
    print(f"wrote {nrows} rows -> {a.csv}")
    print(f"wrote report -> {a.out}")
    print(open(a.out).read())


if __name__ == "__main__":
    main()
