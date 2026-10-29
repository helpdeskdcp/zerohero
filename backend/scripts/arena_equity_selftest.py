#!/usr/bin/env python3
"""Arena equity self-test against the LIVE local service, as the hidden test participant AVINASH_TEST (token read from data/arena/ACCESS_AVINASH_TEST.txt, never printed).
  probe   read-only: search, quote, ticks (source + age), SSE first-message latency / tick gaps, 1-minute chart candles     (any time; ticks only mean something while NSE trades)
  trade   NSE hours only: paper lifecycle with 1-share orders: BUY + SELL at market (NEAR) -> fill -> EXIT_NOW; LIMIT BUY below the market -> AMEND_ENTRY -> CANCEL; bad input -> 422
  report  settles the test ids straight from the ledger + captured quotes and compares them with what the API showed (fill delay, charges, P&L sign); writes data/ops/equity_selftest_<date>.md
Every step appends one JSON line to data/ops/equity_selftest.jsonl. Paper only: the arena has no order path to any broker.   python scripts/arena_equity_selftest.py probe|trade|report"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
IST = timezone(timedelta(hours=5, minutes=30))
BASE = os.environ.get("ARENA_SELFTEST_BASE", "http://127.0.0.1:7060/api/arena/public")
LOG = os.path.join(HERE, "data", "ops", "equity_selftest.jsonl")
SYMS = ["RELIANCE", "TCS", "INFY"]


def token() -> str:
    t = open(os.path.join(HERE, "data", "arena", "ACCESS_AVINASH_TEST.txt")).read()
    m = re.search(r"Token[^:]*:\s*(\S+)", t); assert m, "no token line in the access file"; return m.group(1)


S = requests.Session(); S.headers["X-Arena-Token"] = ""; S.headers["Connection"] = "close"          # a fresh connection per call: the server closes idle keep-alive sockets after 5 s and a reused one raised "Connection reset by peer"


def call(method: str, path: str, **kw):
    S.headers["X-Arena-Token"] = token(); t0 = time.time()
    r = S.request(method, BASE + path, timeout=kw.pop("timeout", 20), **kw)
    try: body = r.json()
    except ValueError: body = {"raw": r.text[:200]}
    return r.status_code, body, round(time.time() - t0, 3)


def rec(kind: str, **kw) -> dict:
    d = {"kind": kind, "ts": time.time(), "ist": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"), **kw}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f: f.write(json.dumps(d, default=str) + "\n")
    print(json.dumps(d, default=str)[:300]); return d


def nse_open() -> bool:
    try:
        from app import market_calendar as mc
        return bool(mc.is_trading("NSE"))
    except Exception:
        return False


def tick(x: float) -> float: return round(round(x / 0.05) * 0.05, 2)


# ---------------------------------------------------------------- probe
def probe() -> dict:
    out = {"nse_open": nse_open(), "checks": {}}; ck = out["checks"]
    c, b, dt = call("GET", "/equity/search", params={"q": "rel"}); ck["search"] = {"ok": c == 200 and any(r["symbol"] == "RELIANCE" for r in b.get("results", [])), "status": c, "s": dt}
    c, b, dt = call("GET", "/equity/ticks", params={"symbols": ",".join(SYMS)}); tk = b.get("ticks", {}) if c == 200 else {}
    ck["ticks"] = {"status": c, "s": dt, "per_symbol": {s: {k: tk.get(s, {}).get(k) for k in ("ltp", "bid", "ask", "src", "age_s")} for s in SYMS}}
    ck["ticks"]["ok"] = all(tk.get(s, {}).get("ltp") for s in SYMS) if out["nse_open"] else True
    c, b, dt = call("GET", "/equity/quote", params={"symbol": "RELIANCE"}); ck["quote"] = {"status": c, "s": dt, "age_s": b.get("age_s"), "has_last": bool(b.get("last"))}
    c, b, dt = call("GET", "/chart/equity", params={"symbol": "RELIANCE", "tf": 1}); cs = b.get("candles", []) if c == 200 else []
    ck["chart"] = {"status": c, "s": dt, "candles": len(cs), "last_candle_age_s": (round(time.time() - (cs[-1]["time"] - 19800), 1) if cs else None)}
    # SSE: first message latency, tick count and the largest gap between price messages over 10 s
    S.headers["X-Arena-Token"] = token(); t0 = time.time(); first = None; stamps = []
    try:
        with S.get(BASE + "/equity/stream", params={"symbols": "RELIANCE"}, stream=True, timeout=15) as r:
            for line in r.iter_lines(decode_unicode=True):
                if time.time() - t0 > 10: break
                if line and line.startswith("data:"):
                    now = time.time(); first = first if first is not None else round(now - t0, 3); stamps.append(now)
    except Exception as e: ck["sse"] = {"error": type(e).__name__}
    if "sse" not in ck:
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        ck["sse"] = {"first_msg_s": first, "messages": len(stamps), "max_gap_s": round(max(gaps), 2) if gaps else None, "ok": first is not None and first < 3}
    out["ok"] = all(v.get("ok", True) if "ok" in v else v.get("status", 200) == 200 for v in ck.values())
    rec("probe", **out); return out


# ---------------------------------------------------------------- trade
def live_rows() -> dict:
    c, b, _ = call("GET", "/live"); return {r["id"]: r for r in b.get("rows", [])} if c == 200 else {}


def wait_state(sid: str, want: set, secs: float) -> tuple[str | None, float]:
    t0 = time.time(); st = None
    while time.time() - t0 < secs:
        r = live_rows().get(sid); st = r.get("state") if r else None
        if st in want: break
        time.sleep(1.0)
    return st, round(time.time() - t0, 1)


def order(sym: str, side: str, mode: str, entry: float, sl: float, tg: float) -> dict:
    body = {"instrument": "EQUITY", "underlying": sym, "side": side, "entry_mode": mode, "entry": entry, "sl": sl, "targets": [tg], "qty": 1, "note": "SELFTEST equity lifecycle (paper, 1 share)"}
    c, b, dt = call("POST", "/submit", json=body); return {"status": c, "s": dt, "id": b.get("id"), "detail": b.get("detail") or b.get("warnings"), "sent": body}


def trade() -> dict:
    if not nse_open(): rec("trade", skipped="NSE is not trading"); return {"skipped": True}
    c, tk, _ = call("GET", "/equity/ticks", params={"symbols": ",".join(SYMS)}); tk = tk.get("ticks", {})
    for s in SYMS:
        if not (tk.get(s, {}).get("bid") and tk.get(s, {}).get("ask")): rec("trade", skipped=f"no bid/ask for {s}: {tk.get(s)}"); return {"skipped": True}
    res = {"orders": {}}
    ask, bid = tk["RELIANCE"]["ask"], tk["TCS"]["bid"]
    o1 = order("RELIANCE", "BUY", "NEAR", tick(ask), tick(ask * 0.985), tick(ask * 1.02)); res["orders"]["buy_market"] = o1; rec("order", case="buy_market", **o1)
    o2 = order("TCS", "SELL", "NEAR", tick(bid), tick(bid * 1.015), tick(bid * 0.98)); res["orders"]["sell_market"] = o2; rec("order", case="sell_market", **o2)
    lim = tk["INFY"]["ask"] * 0.97
    o3 = order("INFY", "BUY", "LIMIT", tick(lim), tick(lim * 0.985), tick(lim * 1.02)); res["orders"]["limit_waiting"] = o3; rec("order", case="limit_waiting", **o3)
    bad = [("qty_zero", {"qty": 0}), ("entry_text", {"entry": "abc"}), ("entry_inf", {"entry": "inf"}), ("stop_above_entry_buy", {"sl": tick(ask * 1.01)})]
    for name, patch in bad:
        body = {"instrument": "EQUITY", "underlying": "RELIANCE", "side": "BUY", "entry_mode": "NEAR", "entry": tick(ask), "sl": tick(ask * 0.985), "targets": [tick(ask * 1.02)], "qty": 1, **patch}
        c, b, _ = call("POST", "/submit", json=body); res["orders"]["bad_" + name] = {"status": c}; rec("order", case="bad_" + name, status=c, expect=422, ok=(c == 422))
    for case in ("buy_market", "sell_market"):
        o = res["orders"][case]
        if o["id"]:
            st, dt = wait_state(o["id"], {"ACTIVE", "CLOSED"}, 45); o["to_active"] = {"state": st, "wait_s": dt}; rec("state", case=case, id=o["id"], state=st, wait_s=dt)
    o = res["orders"]["limit_waiting"]
    if o["id"]:
        st, dt = wait_state(o["id"], {"WAITING"}, 20); rec("state", case="limit_waiting", id=o["id"], state=st, wait_s=dt)
        c, b, _ = call("POST", "/amend", json={"signal_id": o["id"], "action": "AMEND_ENTRY", "entry": tick(lim * 1.005)}); rec("amend", case="limit_waiting", action="AMEND_ENTRY", status=c)
        time.sleep(5); r = live_rows().get(o["id"], {}); rec("state", case="limit_waiting_after_amend", id=o["id"], state=r.get("state"), entry=r.get("entry"))
        c, b, _ = call("POST", "/amend", json={"signal_id": o["id"], "action": "CANCEL"}); rec("amend", case="limit_waiting", action="CANCEL", status=c)
        st, dt = wait_state(o["id"], {"CANCELLED"}, 20); rec("state", case="limit_waiting_after_cancel", id=o["id"], state=st, wait_s=dt)
    time.sleep(40)                                                                                      # let the two positions run a little before the manual exit
    for case in ("buy_market", "sell_market"):
        o = res["orders"][case]
        if not o["id"]: continue
        r = live_rows().get(o["id"], {}); rec("position", case=case, id=o["id"], state=r.get("state"), fill=r.get("fill"), pnl_rs=r.get("pnl_rs"), mark_age_s=r.get("quote_age_s"))
        c, b, _ = call("POST", "/amend", json={"signal_id": o["id"], "action": "EXIT_NOW"}); rec("amend", case=case, action="EXIT_NOW", status=c)
    for case in ("buy_market", "sell_market"):
        o = res["orders"][case]
        if o["id"]:
            st, dt = wait_state(o["id"], {"CLOSED"}, 40); r = live_rows().get(o["id"], {}); rec("closed", case=case, id=o["id"], state=st, wait_s=dt, fill=r.get("fill"), exit=r.get("exit"), why=r.get("why"), pnl_rs=r.get("pnl_rs"))
    rec("trade_done", ids={k: v.get("id") for k, v in res["orders"].items() if v.get("id")}); return res


# ---------------------------------------------------------------- report
def report() -> str:
    lines = [json.loads(x) for x in open(LOG)] if os.path.exists(LOG) else []
    today = datetime.now(IST).strftime("%Y-%m-%d"); lines = [x for x in lines if x["ist"].startswith(today)]
    out = [f"# Arena equity self-test {today}", ""]
    pr = [x for x in lines if x["kind"] == "probe"]
    out.append(f"## Probes ({len(pr)})")
    for x in pr:
        ck = x["checks"]; out.append(f"- {x['ist'][11:]} NSE {'open' if x['nse_open'] else 'closed'}: search {'ok' if ck['search']['ok'] else 'FAIL'} · ticks {'ok' if ck['ticks'].get('ok') else 'FAIL'} "
                                      f"({', '.join(str(v.get('src')) + '/' + str(v.get('age_s')) + 's' for v in ck['ticks']['per_symbol'].values())}) · SSE first {ck['sse'].get('first_msg_s')}s, {ck['sse'].get('messages')} msgs, max gap {ck['sse'].get('max_gap_s')}s · "
                                      f"chart {ck['chart']['candles']} candles (last {ck['chart']['last_candle_age_s']}s old)")
    done = [x for x in lines if x["kind"] == "trade_done"]
    out += ["", "## Lifecycle trade test"]
    if not done:
        sk = [x for x in lines if x["kind"] == "trade"]; out.append("- not run" + (f": {sk[-1].get('skipped')}" if sk else "")); open(os.path.join(HERE, "data", "ops", f"equity_selftest_{today}.md"), "w").write("\n".join(out) + "\n"); return "\n".join(out)
    for x in lines:
        if x["kind"] in ("order", "state", "amend", "position", "closed"): out.append(f"- {x['ist'][11:]} {x['kind']:8} " + " ".join(f"{k}={v}" for k, v in x.items() if k not in ("kind", "ts", "ist", "sent", "detail")))
    # exact settlement from the ledger + captured quotes (what the leaderboard engine would score), compared with the delay rule
    ids = done[-1]["ids"]; from app.arena import run as R, settle as S, submit as SU
    sigs = [s for s in R.human_signals(include_practice=True) if s["id"] in ids.values()]
    res = {r["id"]: r for r in R.run(sigs, market_db=os.path.join(HERE, "data", "market_history.db")).get("AVINASH_TEST", {"results": []})["results"]}
    out += ["", "## Settlement from the ledger (same engine as the leaderboard)"]
    verdict = []
    for case, sid in ids.items():
        r = res.get(sid, {}); sg = next((s for s in sigs if s["id"] == sid), {}); out.append(f"- {case} {sid}: status {r.get('status')} fill {r.get('fill')} exit {r.get('exit')} ({r.get('exit_reason')}) gross {r.get('gross_rs')} cost {r.get('cost_rs')} net {r.get('net_rs')}")
        if case in ("buy_market", "sell_market"):
            d = (r.get("fill_t", 0) - sg.get("t", 0)) if r.get("fill_t") else None; want = S.latency_at(sg.get("t", 0))
            out.append(f"  fill delay {d if d is None else round(d, 2)} s (rule {want:g} s)"); verdict.append(("fill_delay_ok", d is not None and want <= d <= want + 4)); verdict.append(("settled", r.get("status") == "SETTLED"))
            if r.get("status") == "SETTLED": verdict.append(("cost_positive", (r.get("cost_rs") or 0) > 0))
        if case == "limit_waiting": verdict.append(("limit_cancelled_or_unfilled", r.get("status") in ("CANCELLED", "NOT_TRIGGERED")))
    last_run = max((i for i, x in enumerate(lines) if x["kind"] == "order" and x.get("case") == "buy_market"), default=0)           # only the latest trade run (earlier runs of the day would be counted twice)
    bad = [x for x in lines[last_run:] if x["kind"] == "order" and str(x.get("case", "")).startswith("bad_")]
    verdict.append(("bad_input_all_422", all(x.get("ok") for x in bad) and len(bad) == 4))
    verdict.append(("probes_ok", all(x.get("ok") for x in pr if x["nse_open"])))
    out += ["", "## Verdict"] + [f"- {'PASS' if ok else 'FAIL'}  {name}" for name, ok in verdict] + [f"", f"**{'ALL PASS' if all(ok for _, ok in verdict) else 'SOME FAILED'}** ({sum(1 for _, ok in verdict if ok)}/{len(verdict)})"]
    txt = "\n".join(out); open(os.path.join(HERE, "data", "ops", f"equity_selftest_{today}.md"), "w").write(txt + "\n"); return txt


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    {"probe": probe, "trade": trade}.get(cmd, lambda: print(report()))()
