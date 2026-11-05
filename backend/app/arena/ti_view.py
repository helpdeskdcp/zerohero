"""Trading Intelligence (ZeroHero V2) view for the Arena charts. READ-ONLY file reads of the V2 output (no import of V2 code, no writes, no orders):
  decisions.jsonl (latest decision), experiment_ledger.jsonl (verdicts), levels.json (empirical premium-excursion levels), zh2.db paper_trades (read-only).
The honest status is part of the payload: while no experiment is ACCEPTed the dashboard says 'NO VALIDATED EDGE' and every level drawn is labelled as a REFERENCE from statistics, not a signal."""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TI_DIR = os.environ.get("TI_DIR", "/root/zerohero_v2")
COVERED = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM")
SESSION = {"NIFTY": (555, 930, "NSE 09:15-15:30"), "BANKNIFTY": (555, 930, "NSE 09:15-15:30"), "SENSEX": (555, 930, "BSE 09:15-15:30"), "NATURALGAS": (540, 1435, "MCX 09:00-23:55"), "CRUDEOIL": (540, 1435, "MCX 09:00-23:55"), "GOLDM": (540, 1435, "MCX 09:00-23:55")}


def _open_now(u: str, now: float) -> tuple:
    t = datetime.fromtimestamp(now, IST); lo, hi, label = SESSION.get(u, (555, 930, "")); m = t.hour * 60 + t.minute
    return (t.weekday() < 5 and lo <= m <= hi), label


def _lines(path: str, last: int = 400) -> list:
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 600_000)); data = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return out
    for l in data[-last:]:
        try: out.append(json.loads(l))
        except ValueError: pass
    return out


def _json(path: str):
    try:
        with open(path, encoding="utf-8") as f: return json.load(f)
    except (OSError, ValueError): return None


def edge_status(ti_dir: str | None = None) -> dict:
    d = ti_dir or TI_DIR; latest: dict = {}
    try:
        with open(os.path.join(d, "docs", "experiment_ledger.jsonl"), encoding="utf-8") as f:
            for l in f:
                try: r = json.loads(l)
                except ValueError: continue
                latest[r["id"]] = {**latest.get(r["id"], {}), **{k: v for k, v in r.items() if v is not None}}
    except OSError:
        return {"validated": False, "n": 0, "verdicts": {}, "text": "no experiment ledger found"}
    v = {k: x.get("verdict") for k, x in sorted(latest.items()) if x.get("status") == "DONE"}; ok = any(x.get("verdict") == "ACCEPT" and x.get("tradable") for x in latest.values())
    rej = sum(1 for x in v.values() if x == "REJECT")
    return {"validated": ok, "n": len(v), "rejected": rej, "verdicts": v, "text": "validated edge present" if ok else f"NO VALIDATED EDGE ({rej}/{len(v)} experiments REJECT)"}


def _paper(ti_dir: str, expiry: str | None, strike: float | None, ot: str | None) -> dict:
    p = os.path.join(ti_dir, "data", "zh2.db"); out = {"trades": [], "summary": None}
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        rows = c.execute("select id, opened_ts, closed_ts, expiry, strike, option_type, entry, sl, t1, t2, t3, t4, exit, exit_reason, net_rs from paper_trades order by id desc limit 40").fetchall()
        s = c.execute("select count(*), coalesce(sum(net_rs),0) from paper_trades where closed_ts is not null").fetchone(); c.close()
    except sqlite3.Error:
        return out
    out["summary"] = {"closed": s[0], "net_rs": round(s[1], 2), "milestone": 200}
    for r in rows:
        if expiry and r[3] != expiry: continue
        if strike is not None and float(r[4]) != float(strike): continue
        if ot and r[5] != ot: continue
        out["trades"].append({"id": r[0], "opened": r[1], "closed": r[2], "entry": r[6], "sl": r[7], "targets": [x for x in r[8:12] if x is not None], "exit": r[12], "reason": r[13], "net_rs": r[14]})
    return out


def view(underlying: str, expiry: str | None = None, strike: float | None = None, option_type: str | None = None, ti_dir: str | None = None, now: float | None = None) -> dict:
    d = ti_dir or TI_DIR; now = now or time.time(); u = (underlying or "").upper(); edge = edge_status(d)
    out = {"underlying": u, "edge": edge, "mode": "FORWARD_PAPER (unvalidated candidate)", "covered": u in COVERED, "dash": [], "reference": None, "paper": {"trades": [], "summary": None}, "decision": None}
    if u not in COVERED:
        out["dash"] = [["TI · " + u, "not an Arena instrument"], ["Edge", edge["text"]]]; return out
    last = next((r for r in reversed(_lines(os.path.join(d, "data", "decisions.jsonl"), 1500)) if r.get("action") is not None and (r.get("underlying") or "NIFTY") == u), None)
    lv = _json(os.path.join(d, "data", "levels.json")); today = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d"); regime = "expiry_day" if (expiry and expiry == today) else "normal"
    ref = None
    if lv and u == "NIFTY": ref = (lv["grid"][regime], "NIFTY premium excursions, dev data 2025-10..2026-08")
    elif lv and (lv.get("instruments") or {}).get(u): ref = ((lv["instruments"][u].get(regime) or lv["instruments"][u].get("normal")), lv["instruments"][u].get("source", "Drive quote archive 2026-09..10"))
    if ref and ref[0]:
        g, src = ref; m = g["mfe_q"]; cell = (g.get("cells") or {}).get("sl0.2_t0.1") or {}
        out["reference"] = {"regime": regime, "sl_pct": -0.20, "targets_pct": [m[q] for q in ("0.5", "0.75", "0.9", "0.95")], "p_target_first_10pct": cell.get("p_target_first_cons"), "p_stop_20pct": cell.get("p_stop"), "n": g.get("n"),
                            "note": f"REFERENCE levels from {src}: 15-minute premium-excursion statistics; NOT a signal. T = 50/75/90/95th percentile of the favourable move."}
    out["paper"] = _paper(d, expiry, strike, option_type) if u == "NIFTY" else {"trades": [], "summary": None}
    is_open, label = _open_now(u, now); n = 36; c = lambda x: (str(x) if len(str(x)) <= n else str(x)[: n - 1] + "…")
    rows = [["TI · " + u, "paper only"], ["Edge", c(f"none validated · {edge.get('rejected', 0)} tests rejected" if not edge["validated"] else "validated")]]
    if last:
        age = now - last.get("ts", now); out["decision"] = {k: last.get(k) for k in ("action", "mode", "confidence", "regime", "reasons", "entry", "sl", "targets", "expected_cost_rs", "breakeven_points", "p_target_first", "p_stop", "ist", "spot", "atm", "option_type", "strike", "expiry", "legs", "cost_verified")}; out["decision"]["age_s"] = round(age)
        stale = age > 900; hhmm = str(last.get("ist") or "")[11:16]
        rows.append(["Now", c(f"{last.get('action')}" + (f" · last {hhmm}" + ("" if is_open else " · closed") if stale else ""))])
        reason = (last.get("reasons") or [""])[0]
        if last.get("mode") == "CONTEXT_ONLY" or "no model has been trained" in reason: rows.append(["Mode", "data only (no model)"])
        elif last.get("confidence") is not None: rows.append(["Score", c(f"{last.get('confidence')} · " + ("below threshold" if "below" in reason else "ok"))])
        if last.get("expected_cost_rs"): rows.append(["Cost", c(f"Rs{last['expected_cost_rs']:.0f}/lot · BE +{last.get('breakeven_points')} pts" + ("" if last.get("cost_verified", True) else " ~"))])
        leg = (last.get("legs") or {}).get("CE") or {}
        if leg.get("spread_pct") is not None: rows.append(["ATM", c(f"{last.get('atm'):g} · spread {leg['spread_pct']}% · " + ("liquid" if leg.get("tradable") else "thin"))])
    else:
        rows.append(["Now", c("waiting for first cycle" if is_open else f"closed · {label.split(' ')[1].split('-')[0]} IST opens")])
    s2 = out["paper"]["summary"]
    if u == "NIFTY" and s2: rows.append(["Paper", c(f"{s2['closed']}/{s2['milestone']} · net Rs{s2['net_rs']:.0f}")])
    out["dash"] = rows; return out
