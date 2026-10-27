"""Independent audit of data/research/nifty_expiry_pine/nifty_expiry_intelligence.pine on stored NIFTY 5-minute bars (PineTS engine, NOT TradingView).
 A. reference check: prev-day levels, gap, daily ATR, ATR/RSI, opening range, HTF votes recomputed in Python from the same candles and compared with the script's plots
 B. pivot confirmation delay: a swing is visible only R bars after the pivot bar
 C. causality: re-run on truncated histories; every plotted value and every closed trade before the cut must be identical (a look-ahead/repaint would change them)
usage: pine_nifty_audit.py [--bars 12000] [--cuts 4] [--out file.json]"""
import argparse, json, math, os, sys
os.environ.setdefault('SMARTCHART_NODE_CPU_SEC', '1800'); os.environ.setdefault('SMARTCHART_NODE_HEAP_MB', '3072'); os.environ.setdefault('SMARTCHART_EXEC_TIMEOUT', '1500'); os.environ.setdefault('SMARTCHART_MAX_OUTPUT_BYTES', '700000000')
from datetime import datetime, timedelta, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "scripts"))
import pine_nifty_harness as H
from app.smart_chart import runner
IST = timezone(timedelta(hours=5, minutes=30))
SRC = os.path.join(HERE, "data", "research", "nifty_expiry_pine", "nifty_expiry_intelligence.pine")
nan = float("nan")


def run(rows, inputs=None):
    r = runner.execute({"op": "run", "source": open(SRC, encoding="utf-8").read(), "candles": rows, "tf": "5", "symbol": "NIFTY", "inputs": inputs or {}, "props": {}}, "audit", timeout=900, count=False)
    if not r.get("ok"): raise SystemExit(f"script failed: {r.get('diagnostics')}")
    return r


def plots(res): return H.unpack({p["title"]: [nan if v is None else float(v) for v in p["values"]] for p in res["plots"]})


def same(a, b, tol=1e-9):
    return (math.isnan(a) and math.isnan(b)) or abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def ema(vals, n):
    out, e, buf = [], None, []
    for v in vals:
        if e is None:
            buf.append(v)
            if len(buf) == n: e = sum(buf) / n; out.append(e)
            else: out.append(nan)
        else: e = e + 2 / (n + 1) * (v - e); out.append(e)
    return out


def rma(vals, n):
    out, e, buf = [], None, []
    for v in vals:
        if e is None:
            buf.append(v)
            if len(buf) == n: e = sum(buf) / n; out.append(e)
            else: out.append(nan)
        else: e = (e * (n - 1) + v) / n; out.append(e)
    return out


def reference(rows):
    n = len(rows); t = [r["time"] // 1000 for r in rows]
    dt = [datetime.fromtimestamp(x, IST) for x in t]; day = [d.strftime("%Y-%m-%d") for d in dt]; mod = [d.hour * 60 + d.minute for d in dt]
    O = [r["open"] for r in rows]; Hh = [r["high"] for r in rows]; L = [r["low"] for r in rows]; C = [r["close"] for r in rows]
    prevH = [nan] * n; prevL = [nan] * n; prevC = [nan] * n; gap = [nan] * n; datr = [nan] * n; orH = [nan] * n; orL = [nan] * n
    tr_arr = []; dO = dH = dL = dC = None; pH = pL = pC = None; orh = orl = None
    for i in range(n):
        new = i == 0 or day[i] != day[i - 1]
        if new:
            if dH is not None:
                refC = dO if pC is None else pC
                tr_arr.append(max(dH - dL, abs(dH - refC), abs(dL - refC)))
                if len(tr_arr) > 14: tr_arr.pop(0)
                pH, pL, pC = dH, dL, dC
            dO, dH, dL = O[i], Hh[i], L[i]; orh, orl = Hh[i], L[i]
        else:
            dH = max(dH, Hh[i]); dL = min(dL, L[i])
            if mod[i] < 555 + 15: orh = max(orh, Hh[i]); orl = min(orl, L[i])
        dC = C[i]
        prevH[i] = nan if pH is None else pH; prevL[i] = nan if pL is None else pL; prevC[i] = nan if pC is None else pC
        if new and pC is not None: gap[i] = O[i] - pC
        elif i > 0 and not new: gap[i] = gap[i - 1]
        datr[i] = sum(tr_arr) / len(tr_arr) if len(tr_arr) >= 5 else nan
        orH[i], orL[i] = orh, orl
    trs = [Hh[0] - L[0]] + [max(Hh[i] - L[i], abs(Hh[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, n)]
    atr = rma(trs, 14)
    ch = [C[i] - C[i - 1] for i in range(1, n)]                                   # changes start at bar 1: the first RSI(14) exists at bar 14
    ru = [nan] + rma([max(x, 0) for x in ch], 14); rd = [nan] + rma([max(-x, 0) for x in ch], 14)
    rsi = [nan if (math.isnan(a) or math.isnan(b)) else (100.0 if b == 0 else 100 - 100 / (1 + a / b)) for a, b in zip(ru, rd)]
    # RSI first value uses changes from bar 1 (bar 0 has no change): Pine seeds after 14 changes
    votes = {}
    for tfm in (15, 30, 60, 240):
        keys = [(day[i], (mod[i] - 555) // tfm) for i in range(n)]
        bars = []                                     # (key, close) of completed + current HTF bars in order
        for i in range(n):
            if not bars or bars[-1][0] != keys[i]: bars.append([keys[i], C[i]])
            else: bars[-1][1] = C[i]
        closes = [b[1] for b in bars]; ef = ema(closes, 9); es = ema(closes, 21)
        idx = {b[0]: j for j, b in enumerate(bars)}
        v = []
        for i in range(n):
            j = idx[keys[i]] - 1                      # previous COMPLETED HTF bar
            if j < 0 or math.isnan(ef[j]) or math.isnan(es[j]): v.append(0)
            else: v.append(1 if ef[j] > es[j] and closes[j] > es[j] else -1 if ef[j] < es[j] and closes[j] < es[j] else 0)
        votes[tfm] = v
    return {"Ref prevH": prevH, "Ref prevL": prevL, "Ref prevC": prevC, "Gap points": gap, "Daily ATR": datr, "Ref orH": orH, "Ref orL": orL, "Ref ATR": atr, "Ref RSI": rsi,
            "HTF vote 1": votes[15], "HTF vote 2": votes[30], "HTF vote 3": votes[60], "HTF vote 4": votes[240]}


def compare(pl, ref, skip=0):
    out = {}
    for k, rv in ref.items():
        pv = pl[k]; bad = [i for i in range(skip, len(rv)) if not same(pv[i], float(rv[i]), 1e-7)]
        out[k] = {"bars": len(rv) - skip, "mismatches": len(bad), "first_bad": [(i, pv[i], rv[i]) for i in bad[:3]]}
    return out


def pivot_check(rows, pl, R=3, L_=3):
    sw = pl["Ref swing high"]; Hh = [r["high"] for r in rows]; bad, seen = [], 0
    for i in range(1, len(sw)):
        if not math.isnan(sw[i]) and (math.isnan(sw[i - 1]) or sw[i] != sw[i - 1]):
            seen += 1; j = i - R
            win = Hh[max(0, j - L_): j + R + 1]
            if not (abs(Hh[j] - sw[i]) < 1e-9 and Hh[j] >= max(win)): bad.append((i, sw[i], Hh[j]))
    return {"swing_highs_seen": seen, "bad": len(bad), "first_bad": bad[:3]}


def causality(rows, full, cuts):
    fp, res = plots(full), []
    fc = {(t["entry_time"], round(t["entry_price"], 6)): t for t in full["strategy"]["closed_trades"]}
    for cut in cuts:
        tr = run(rows[:cut]); tp = plots(tr); diffs = []
        for k, v in tp.items():
            for i in range(cut - 1):                       # the last bar of a truncated run may legitimately still be "forming" for display only
                if not same(v[i], fp[k][i]): diffs.append((k, i, v[i], fp[k][i])); break
        tclosed = [(t["entry_time"], round(t["entry_price"], 6), round(t["exit_price"], 6), round(t["profit"], 4)) for t in tr["strategy"]["closed_trades"]]
        fclosed = [(t["entry_time"], round(t["entry_price"], 6), round(t["exit_price"], 6), round(t["profit"], 4)) for t in full["strategy"]["closed_trades"] if t["exit_time"] < rows[cut - 1]["time"]] if "exit_time" in full["strategy"]["closed_trades"][0] else None
        res.append({"cut_bar": cut, "plot_series_checked": len(tp), "plot_diffs": len(diffs), "first_diffs": diffs[:3], "trades_in_truncated": len(tclosed), "trades_match_full_prefix": (None if fclosed is None else sorted(tclosed) == sorted(fclosed))})
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=12000); ap.add_argument("--cuts", type=int, default=4); ap.add_argument("--out"); a = ap.parse_args()
    rows = H.candles("5", bars=a.bars); full = run(rows); pl = plots(full)
    rep = {"bars": len(rows), "first": rows[0]["time"], "last": rows[-1]["time"], "engine": "PineTS (not TradingView)"}
    rep["reference"] = compare(pl, reference(rows)); rep["pivot_delay"] = pivot_check(rows, pl)
    cuts = [int(len(rows) * f) for f in (0.35, 0.55, 0.75, 0.92)][: a.cuts]
    rep["causality"] = causality(rows, full, cuts)
    print(json.dumps(rep, indent=1, default=str))
    if a.out: json.dump(rep, open(a.out, "w"), indent=1, default=str)


# ───────────────────────── independent Python reference for the SMC state machine (events + FVG) ─────────────────────────
def ref_smc(rows, atr, L=3, R=3, fb=6, rt=8, tol=0.25):
    n = len(rows); Hh = [r["high"] for r in rows]; Lw = [r["low"] for r in rows]; C = [r["close"] for r in rows]
    bits = [0] * n; swh_out = [nan] * n; swl_out = [nan] * n; fvg = [0] * n; open_bull = [0] * n; open_bear = [0] * n
    swH = swL = None; hUsed = lUsed = hSw = lSw = False; trend = 0
    bUpL = bUpB = bDnL = bDnB = None; bUpDone = bDnDone = False
    fv = []
    for i in range(n):
        j = i - R
        if j - L >= 0:
            if all(Hh[j] > Hh[k] for k in range(j - L, j)) and all(Hh[j] >= Hh[k] for k in range(j + 1, j + R + 1)): swH = Hh[j]; hUsed = False; hSw = False
            if all(Lw[j] < Lw[k] for k in range(j - L, j)) and all(Lw[j] <= Lw[k] for k in range(j + 1, j + R + 1)): swL = Lw[j]; lUsed = False; lSw = False
        b = 0
        if swH is not None and not hUsed and C[i] > swH:
            hUsed = True; bUpL = swH; bUpB = i; bUpDone = False; b |= 4 if trend == -1 else 1; trend = 1
        if swL is not None and not lUsed and C[i] < swL:
            lUsed = True; bDnL = swL; bDnB = i; bDnDone = False; b |= 8 if trend == 1 else 2; trend = -1
        if swH is not None and not hUsed and not hSw and Hh[i] > swH and C[i] < swH: b |= 16; hSw = True
        if swL is not None and not lUsed and not lSw and Lw[i] < swL and C[i] > swL: b |= 32; lSw = True
        if bUpB is not None and not bUpDone and i > bUpB and i - bUpB <= fb and C[i] < bUpL: b |= 64; bUpDone = True
        if bDnB is not None and not bDnDone and i > bDnB and i - bDnB <= fb and C[i] > bDnL: b |= 128; bDnDone = True
        a = atr[i]
        if bUpB is not None and not bUpDone and i > bUpB and i - bUpB <= rt and not math.isnan(a) and Lw[i] <= bUpL + tol * a and C[i] > bUpL: b |= 256
        if bDnB is not None and not bDnDone and i > bDnB and i - bDnB <= rt and not math.isnan(a) and Hh[i] >= bDnL - tol * a and C[i] < bDnL: b |= 512
        bits[i] = b; swh_out[i] = nan if swH is None else swH; swl_out[i] = nan if swL is None else swL
        fbu = i >= 2 and Lw[i] > Hh[i - 2]; fbe = i >= 2 and Hh[i] < Lw[i - 2]
        fvg[i] = (1 if fbu else 0) - (1 if fbe else 0)
        rngp = Hh[i] - Lw[i] > 0
        if fbu and rngp: fv.append([Lw[i], Hh[i - 2], 1])                       # [top, bottom, dir]
        if fbe and rngp: fv.append([Lw[i - 2], Hh[i], -1])
        keep = []
        for top, bot, d in fv:
            if (d == 1 and C[i] < bot) or (d == -1 and C[i] > top): continue
            keep.append([top, bot, d])
        fv = keep[-8:] if len(keep) > 8 else keep
        open_bull[i] = sum(1 for t in fv if t[2] == 1); open_bear[i] = sum(1 for t in fv if t[2] == -1)
    return {"Event bits": bits, "Ref swing high": swh_out, "Ref swing low": swl_out, "FVG formed (+1 bull / -1 bear)": fvg, "Open bull FVG": open_bull, "Open bear FVG": open_bear}
