"""PHASE 1/2: forensic of the INSTITUTION_TRADER ("Mohit") channel signals, independent of his own entry/SL/targets (read-only; nothing is deleted or altered; the original records stay in the ledger).
For each shown signal (days excluded from learning are NOT included): what the market looked like at the post (index 15m bias, 5m structure events in the previous 30 minutes via the hybrid S/R engine, lateness of the post,
risk/reward and stop width of the call), and an independent option-premium outcome reconstructed from the cached 1-minute candles (limit fill, first touch of stop vs T1; the stop first when one bar touches both).
Labels are EVIDENCE flags, never proof of intent: SUSPECT needs >= 3 independent flags incl. one market-based flag. Writes datasets/ A-E (local only) and p2_forensic.json."""
import hashlib, json, os, sys, time
from collections import Counter
from datetime import datetime, timedelta, timezone
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.arena import mohit_view as MV
from app.research_engines.hybrid_sr_mtf import data as D, engine as E
IST = timezone(timedelta(hours=5, minutes=30))
LOOKBACK_MIN = 30


def abs_min(t_epoch: float) -> int:
    dt = datetime.fromtimestamp(t_epoch, IST); return int(np.datetime64(dt.date(), "D").astype(np.int64)) * 1440 + dt.hour * 60 + dt.minute


def option_outcome(sig: dict, candles: list[dict]):
    """Independent reconstruction on LTP candles: limit buy at `entry` (fill at the open when the candle opens below it) inside 15 minutes of the post, then stop vs T1 (stop first on a double touch)."""
    post = sig["post_shift"]; entry = sig["entry"]; sl = sig["sl"]; t1 = sig["t1"]
    if not candles or sl is None or t1 is None or entry is None: return {"status": "UNVERIFIABLE", "why": "no candles or incomplete levels"}
    after = [c for c in candles if c["time"] >= post + 60]
    if not after: return {"status": "UNVERIFIABLE", "why": "no candle after the post"}
    first_px = after[0]["open"]; late_pct = round((first_px / entry - 1) * 100, 2)
    fill = None
    for c in after:
        if c["time"] > post + 15 * 60: break
        if c["low"] <= entry: fill = min(entry, c["open"]) if c["open"] <= entry else entry; ft = c["time"]; break
    if fill is None: return {"status": "NOT_FILLED_15MIN", "late_pct": late_pct, "price_after_post": first_px}
    risk = fill - sl
    if risk <= 0: return {"status": "INVALID_LEVELS", "late_pct": late_pct}
    res = "OPEN_AT_SESSION_END"; k = 0
    for c in after:
        if c["time"] < ft: continue
        stop_hit = c["low"] <= sl; tgt_hit = c["high"] >= t1 and t1 > fill
        if stop_hit: res = "STOP_FIRST" if not tgt_hit else "STOP_FIRST_AMBIGUOUS_BAR"; break
        if tgt_hit: res = "T1_FIRST"; break
    last = candles[-1]["close"]; r = (t1 - fill) / risk if res == "T1_FIRST" else -1.0 if res.startswith("STOP") else (last - fill) / risk
    return {"status": "RECONSTRUCTED", "fill": fill, "result": res, "R": round(r, 2), "late_pct": late_pct, "price_after_post": first_px, "rr_t1": round((t1 - entry) / (entry - sl), 2) if entry > sl else None}


def structure_context(sig: dict, preps: dict):
    und = sig["underlying"]
    if und not in preps: return {"status": "NO_INDEX_CONTEXT", "why": f"{und}: engine supports NSE/BSE index 5m/15m only"}
    prep = preps[und]; tam = abs_min(sig["t"]); side = 1 if sig["option_type"] == "CE" else -1; day = int(datetime.fromtimestamp(sig["t"], IST).strftime("%Y%m%d"))
    if day not in set(prep.day[-400000:].tolist() if len(prep.day) > 400000 else prep.day.tolist()): return {"status": "NO_INDEX_CONTEXT", "why": "no index candles that day"}
    t15 = prep.tf15; i15 = int(np.searchsorted(t15.end, tam, side="right")) - 1
    bias = E._bias15(prep, i15) if i15 >= 0 else 0
    ev = []
    for cand in E.CANDIDATES:
        events: list = []; E.scan(prep, cand, E.Params(win_from=0, win_to=24 * 60, max_trades_day=99, max_consec_loss=99, cooldown_min=0), events=events, start_day=day, end_day=day)
        ev += [e for e in events if tam - LOOKBACK_MIN <= e["t_setup"] <= tam]
    same = [e for e in ev if e["side"] == side]; opp = [e for e in ev if e["side"] == -side]
    i1 = int(np.searchsorted(prep.t1, tam, side="right")) - 1; ordt = abs_min(sig["t"]) - 1
    day_idx = np.flatnonzero(prep.day == day); upto = day_idx[day_idx <= i1]
    loc = None
    if len(upto) > 10:
        hi = prep.h[upto].max(); lo = prep.l[upto].min(); px = prep.c[upto[-1]]; loc = round((px - lo) / (hi - lo), 2) if hi > lo else None
    return {"status": "OK", "bias15": int(bias), "bias_vs_direction": "ALIGNED" if bias == side else "AGAINST" if bias == -side else "NEUTRAL", "engine_setups_same_direction": len(same),
            "engine_trades_same_direction": sum(1 for e in same if e["outcome"] == "TRADE"), "engine_setups_opposite_direction": len(opp), "reject_reasons_same_direction": dict(Counter(e["outcome"] for e in same)),
            "day_range_location_0_1": loc}


def main():
    sigs = MV._signals(); rows = {r["id"]: r for r in MV.list_signals()}
    need = sorted({s["underlying"] for s in sigs if s["underlying"] in ("NIFTY", "BANKNIFTY", "SENSEX")}); preps = {}
    for u in need: preps[u] = D.prepare(u, D.load_index_1m(u, "2026-10-01"))
    out = []
    for s in sigs:
        ch = MV.chart_for(s["id"], 1) or {}; candles = ch.get("candles") or []; sg = ch.get("signal") or MV._row(s, None)
        sg = {**sg, "post_shift": s["t"] + 19800, "t": s["t"]}
        oc = option_outcome(sg, candles); sc = structure_context(s, preps)
        flags = []
        if sc.get("status") == "OK":
            if sc["bias_vs_direction"] == "AGAINST": flags.append(("HTF_AGAINST", "market"))
            if sc["engine_setups_same_direction"] == 0: flags.append(("NO_STRUCTURE_EVENT_IN_PREVIOUS_30MIN", "market"))
        if oc.get("late_pct") is not None and oc["late_pct"] >= 3.0: flags.append(("POSTED_AFTER_THE_MOVE", "market"))
        if oc.get("rr_t1") is not None and oc["rr_t1"] < 0.7: flags.append(("T1_REWARD_BELOW_0_7R", "call"))
        if sg.get("sl") and sg.get("entry") and (sg["entry"] - sg["sl"]) / sg["entry"] > 0.80: flags.append(("STOP_ALMOST_AT_ZERO_(>80%_WIDE)", "call"))
        if sg.get("sl") and sg.get("entry") and (sg["entry"] - sg["sl"]) / sg["entry"] < 0.06: flags.append(("STOP_UNDER_6%_OF_PREMIUM", "call"))
        n_flags = len(flags); has_market = any(k == "market" for _, k in flags)
        if oc["status"] == "UNVERIFIABLE" or sc.get("status") == "NO_INDEX_CONTEXT" and not candles: cat = "UNVERIFIED"
        elif sc.get("status") != "OK": cat = "UNVERIFIED_STRUCTURE"
        elif n_flags >= 3 and has_market: cat = "SUSPECT_MISLEADING_EVIDENCE"
        elif n_flags == 0 and sc["bias_vs_direction"] != "AGAINST": cat = "CONFIRMED_CANDIDATE"
        else: cat = "UNCONFIRMED"
        out.append({"id": s["id"], "date": sg["date"], "time": sg["time"], "und": s["underlying"], "strike": s["strike"], "ot": s["option_type"], "entry": sg["entry"], "sl": sg["sl"], "t1": sg["t1"], "t2": sg["t2"], "t3": sg["t3"],
                    "arena_result": (rows.get(s["id"]) or {}).get("result"), "independent_outcome": oc, "market_context": sc, "flags": [f for f, _ in flags], "category": cat})
    tot = Counter(o["category"] for o in out); by = {}
    for cat in tot:
        sub = [o for o in out if o["category"] == cat and o["independent_outcome"].get("status") == "RECONSTRUCTED"]
        by[cat] = {"n": tot[cat], "reconstructed": len(sub), "t1_first": sum(1 for o in sub if o["independent_outcome"]["result"] == "T1_FIRST"), "stop_first": sum(1 for o in sub if o["independent_outcome"]["result"].startswith("STOP")),
                   "mean_R": round(float(np.mean([o["independent_outcome"]["R"] for o in sub])), 2) if sub else None}
    summ = {"generated": datetime.now(IST).isoformat(timespec="seconds"), "signals": len(out), "excluded_from_learning": "days listed in data/arena/learning_exclusions.json", "categories": dict(tot), "by_category": by,
            "flag_counts": dict(Counter(f for o in out for f in o["flags"]))}
    json.dump({"summary": summ, "signals": out}, open(os.path.join(HERE, "p2_forensic.json"), "w"), indent=1, default=str)
    # datasets A-E (local only): provenance + hashes
    ds = os.path.join(HERE, "datasets"); os.makedirs(ds, exist_ok=True)
    def w(name, items, note):
        body = "\n".join(json.dumps(x, default=str) for x in items); open(os.path.join(ds, name), "w").write(json.dumps({"_provenance": {"note": note, "count": len(items), "sha256_of_rows": hashlib.sha256(body.encode()).hexdigest(), "created": summ["generated"]}}) + "\n" + body + "\n")
    w("A_verified_market_observations.jsonl", [{"source": "data/research/hybrid_sr_mtf/p1_data_audit.json", "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"]}], "pointer to the candle audit; the candles themselves stay in market_candles")
    w("B_mohit_original_signals.jsonl", [{k: o[k] for k in ("id", "date", "time", "und", "strike", "ot", "entry", "sl", "t1", "t2", "t3")} for o in out], "originals exactly as posted (from the ledger via mohit_view; unchanged)")
    w("C_independent_reconstructed_outcomes.jsonl", [{"id": o["id"], **o["independent_outcome"]} for o in out], "option-candle reconstruction, NOT Mohit's labels")
    w("D_suspect_adversarial_evidence.jsonl", [o for o in out if o["category"] == "SUSPECT_MISLEADING_EVIDENCE"], "evidence flags only; not proof of intent; excluded from any training set")
    w("E_unverified_or_data_gap.jsonl", [o for o in out if o["category"].startswith("UNVERIFIED")], "no option candles / no structure context")
    print(json.dumps(summ, indent=1))


if __name__ == "__main__": main()
