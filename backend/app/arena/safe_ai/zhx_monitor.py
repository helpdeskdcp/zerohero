"""Safe AI x ZHX monitor (SHADOW: records a decision for every ZHX signal, submits NOTHING to the ledger).
ZHX (price-only Pine script, app/smart_chart/pine_scripts/zhx_v1.pine) is the candidate generator. Safe AI adds what the chart cannot see: the live option chain (OI walls, PCR, max pain, IV skew, GEX, bid-ask, spread, DTE) through the existing
Layer-5 qualifier (optionchain.qualify), plus its own hard gates. The decision is TAKE / WATCH / SKIP with every reason; TAKE is only a recommendation until the forward evidence says otherwise.
Append-only log: data/safe_ai/zhx_decisions.jsonl (one row per symbol and signal bar). Read-only on every database; uses the CAPTURED chain only (no broker call)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
LOG = os.path.join(BASE, "data", "safe_ai", "zhx_decisions.jsonl")
SRC = os.path.join(BASE, "app", "smart_chart", "pine_scripts", "zhx_v1.pine")
SYMS = ("NIFTY", "SENSEX", "BANKNIFTY", "NATURALGAS", "CRUDEOIL", "GOLDM")
MAX_SIGNAL_AGE_MIN = 12
MAX_SPREAD, MIN_PREMIUM, MAX_DTE = 0.03, 30.0, 2                      # the Safe-AI hard gates (same values as safe_ai.live)
_SRC: dict = {}


def _source() -> str:
    if "s" not in _SRC: _SRC["s"] = open(SRC, encoding="utf-8").read()
    return _SRC["s"]


def latest_signal(sym: str, tf: int = 5, now: float | None = None, bars=None, execute=None) -> dict | None:
    """Run ZHX on the symbol's 5-minute bars and return the signal of the last two confirmed bars (None if none / too old)."""
    from .. import terminal as TM
    from ...smart_chart import runner
    now = now or time.time()
    d = bars if bars is not None else TM._bars_from_capture(sym, tf, now, days=12)
    if d is None or len(d) < 120: return None
    d = d.tail(700); cs = [{"time": int(ts.timestamp() * 1000), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(getattr(r, "volume", 0.0) or 0.0)} for ts, r in zip(d["timestamp"], d.itertuples())]
    res = (execute or runner.execute)({"op": "run", "source": _source(), "candles": cs, "tf": str(tf), "symbol": sym, "inputs": {"chartKind": "Underlying / Index"}}, count=False)
    if not res.get("ok"): return {"error": "; ".join(str(x.get("message")) for x in res.get("diagnostics", [])[:2]) or "pine run failed"}
    P = {p["title"]: p["values"] for p in res.get("plots", [])}; n = len(cs)
    for k in (n - 1, n - 2):
        if k >= 0 and P.get("ZHX Signal") and P["ZHX Signal"][k]:
            t_bar = cs[k]["time"] / 1000.0; age = (now - (t_bar + tf * 60)) / 60.0
            if age > MAX_SIGNAL_AGE_MIN: return None
            g = lambda t: P[t][k] if P.get(t) else None
            return {"side": int(P["ZHX Signal"][k]), "bar_time": t_bar, "bar_ist": datetime.fromtimestamp(t_bar, IST).strftime("%Y-%m-%d %H:%M"), "age_min": round(age, 1), "entry": g("ZHX Entry"), "sl": g("ZHX SL"), "t1": g("ZHX T1"), "t2": g("ZHX T2"), "t3": g("ZHX T3"), "score": g("ZHX Score")}
    return None


def _atm_leg(chain, side: int):
    if not chain or not chain.rows or chain.spot is None: return None, None
    row = min(chain.rows, key=lambda r: abs(r.strike - chain.spot)); return row.strike, (row.ce if side > 0 else row.pe)


def decide(sym: str, sig: dict, chain, qualify_fn=None, compute_fn=None) -> dict:
    """Pure decision given a signal and a chain (both injected in tests)."""
    from ...optionchain import qualify as Q, analytics as A
    reasons: list = []; out = {"symbol": sym, "signal": sig, "decision": "SKIP", "reasons": reasons}
    if chain is None: reasons.append("no captured option chain for this underlying"); return out
    side = sig["side"]; strike, leg = _atm_leg(chain, side); out["chain"] = {"source": chain.source, "ts": chain.ts, "expiry": chain.expiry, "spot": chain.spot, "dte": (chain.expiry_ctx or {}).get("dte")}
    try:
        qf = qualify_fn or Q.qualify_from_chain
        q = qf(chain, external_signal={"direction": "BUY" if side > 0 else "SELL", "score": (sig.get("score") or 0) / 10.0, "source": "ZHX"}); qd = q.to_dict() if hasattr(q, "to_dict") else dict(q)
        out["qualification"] = {"verdict": qd.get("verdict"), "direction": qd.get("direction"), "blocking": qd.get("blocking"), "watch_reasons": qd.get("watch_reasons"), "gates": [(g.get("name"), g.get("status")) for g in qd.get("gates", [])], "summary": qd.get("summary")}
    except Exception as e:
        qd = {"verdict": "NO_TRADE"}; out["qualification"] = {"error": f"{type(e).__name__}: {e}"}; reasons.append("chain qualification failed")
    walls = {}
    try:
        al = (compute_fn or A.compute_all)(chain); w = al["oi_walls"].to_dict() if hasattr(al["oi_walls"], "to_dict") else al["oi_walls"]
        walls = {"nearest_resistance": w.get("nearest_resistance"), "nearest_support": w.get("nearest_support"), "ce_wall": w.get("ce_wall"), "pe_wall": w.get("pe_wall")}
        pcr = al.get("pcr"); out["context"] = {"pcr": getattr(pcr, "pcr_oi", None) if pcr is not None else None, "max_pain": getattr(al.get("max_pain"), "max_pain", None), "weighted_iv": al.get("weighted_iv") if isinstance(al.get("weighted_iv"), (int, float)) else None}
    except Exception as e:
        out["context"] = {"error": f"{type(e).__name__}"}
    out["walls"] = walls
    if leg is not None:
        bid, ask, ltp = leg.bid, leg.ask, leg.ltp; mid = (bid + ask) / 2 if bid and ask else ltp; spread = (ask - bid) / mid if bid and ask and mid else None
        out["option"] = {"strike": strike, "type": "CE" if side > 0 else "PE", "bid": bid, "ask": ask, "ltp": ltp, "spread_pct": round(spread * 100, 2) if spread is not None else None, "iv": leg.iv, "delta": leg.delta, "oi": leg.oi, "oi_change": leg.oi_change, "volume": leg.volume}
        if mid is None or mid < MIN_PREMIUM: reasons.append(f"premium {mid} below the Safe-AI minimum {MIN_PREMIUM}")
        if spread is None: reasons.append("no live bid/ask")
        elif spread > MAX_SPREAD: reasons.append(f"spread {spread*100:.1f}% above {MAX_SPREAD*100:.0f}%")
    else: reasons.append("no ATM option quote")
    dte = (chain.expiry_ctx or {}).get("dte")
    if dte is not None and dte > MAX_DTE: reasons.append(f"dte {dte} above the Safe-AI limit {MAX_DTE} (the rule was only tested near expiry)")
    wall = walls.get("nearest_resistance") if side > 0 else walls.get("nearest_support")
    if wall is not None and sig.get("t1") is not None and chain.spot is not None:
        if (side > 0 and chain.spot < wall <= sig["t1"]) or (side < 0 and sig["t1"] <= wall < chain.spot): reasons.append(f"an OI wall at {wall} sits between entry and T1")
    v = qd.get("verdict")
    if reasons and any(("premium" in r or "spread" in r or "bid/ask" in r or "dte" in r or "chain qualification" in r or "no ATM" in r) for r in reasons): out["decision"] = "SKIP"
    elif v == "QUALIFIED" and not reasons: out["decision"] = "TAKE"
    elif v in ("QUALIFIED", "WATCH") or reasons: out["decision"] = "WATCH"
    else: out["decision"] = "SKIP"; reasons.append(f"chain verdict {v}")
    if v != "QUALIFIED": reasons.append(f"chain verdict {v}: " + "; ".join(map(str, (qd.get("blocking") or []) + (qd.get("watch_reasons") or [])))[:240])
    return out


def _seen() -> set:
    s = set()
    if os.path.exists(LOG):
        for l in open(LOG, encoding="utf-8"):
            try: r = json.loads(l); s.add((r["symbol"], r["signal"]["bar_time"]))
            except Exception: pass
    return s


def tick(syms=SYMS, now: float | None = None, log_path: str | None = None, get_chain=None, signal_fn=None) -> list[dict]:
    """One monitoring pass: for each symbol with a fresh ZHX signal that has no decision yet, decide and append. Never raises."""
    from ...optionchain import resolve
    now = now or time.time(); path = log_path or LOG; done = _seen() if log_path is None else set(); out = []
    for sym in syms:
        try:
            sig = (signal_fn or latest_signal)(sym, now=now)
            if not sig or sig.get("error") or (sym, sig["bar_time"]) in done: continue
            chain = (get_chain or (lambda s: resolve.get_chain(s, "AUTO", allow_network=False)))(sym)
            row = decide(sym, sig, chain); row["decided_ist"] = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M:%S"); row["mode"] = "SHADOW (nothing submitted)"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(row, default=str) + "\n")
            out.append(row)
        except Exception as e:
            out.append({"symbol": sym, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return out


def recent(n: int = 30) -> dict:
    rows = []
    if os.path.exists(LOG):
        for l in open(LOG, encoding="utf-8"):
            try: rows.append(json.loads(l))
            except Exception: pass
    c: dict = {}
    for r in rows: c[r.get("decision")] = c.get(r.get("decision"), 0) + 1
    return {"total": len(rows), "by_decision": c, "last": rows[-n:][::-1], "mode": "SHADOW: recommendations only, nothing is submitted to the ledger"}
