"""Trading Intelligence (ZeroHero V2) view for the Arena charts. READ-ONLY file reads of the V2 output (no import of V2 code, no writes, no orders):
  decisions.jsonl (latest decision), experiment_ledger.jsonl (verdicts), levels.json (empirical premium-excursion levels), zh2.db paper_trades (read-only).
The honest status is part of the payload: while no experiment is ACCEPTed the dashboard says 'NO VALIDATED EDGE' and every level drawn is labelled as a REFERENCE from statistics, not a signal."""
from __future__ import annotations
import json, os, sqlite3, time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
TI_DIR = os.environ.get("TI_DIR", "/root/zerohero_v2")
COVERED = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM")


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
    out["paper"] = _paper(d, expiry, strike, option_type)
    rows = [["TI v2 · " + u, "paper-only, no orders"], ["Edge", edge["text"]]]
    if last:
        age = now - last.get("ts", now); out["decision"] = {k: last.get(k) for k in ("action", "mode", "confidence", "regime", "reasons", "entry", "sl", "targets", "expected_cost_rs", "breakeven_points", "p_target_first", "p_stop", "ist", "spot", "atm", "option_type", "strike", "expiry", "legs", "cost_verified")}; out["decision"]["age_s"] = round(age)
        reason = (last.get("reasons") or [""])[0]
        rows += [["Now", f"{last.get('action')}" + (f" ({int(age // 60)} min ago)" if age > 120 else "")], ["Score", f"{last.get('confidence')}" if last.get("confidence") is not None else "-"], ["Why", reason[:70]]]
        if last.get("expected_cost_rs"): rows.append(["Cost", f"Rs{last['expected_cost_rs']:.0f}/lot · breakeven +{last.get('breakeven_points')} pts" + ("" if last.get("cost_verified", True) else " (cost UNVERIFIED)")])
        leg = (last.get("legs") or {}).get("CE") or {}
        if leg.get("spread_pct") is not None: rows.append(["ATM", f"{last.get('atm')} · spread {leg['spread_pct']}% · {'liquid' if leg.get('tradable') else 'not tradable'}" + (f" · {last.get('regime')}" if last.get("regime") else "")])
    else:
        rows.append(["Now", "no decision yet (market closed?)"])
    s = out["paper"]["summary"]
    if s: rows.append(["Paper", f"{s['closed']}/{s['milestone']} closed · net Rs{s['net_rs']:.0f}"])
    out["dash"] = rows; return out
