"""FORWARD OBSERVATION of the Safe-AI U-turn rule on MCX NATURALGAS futures (RECORD ONLY: no order, no Arena submission, no change to Safe AI). Started 2026-11-03 after the research run of 2026-11-03
(NATURALGAS 28-40 trades, +6.6..+14.2 bp net per trade but every 90 % interval includes zero; CRUDEOIL negative).
Pre-registered now, before the first forward day: the UNCHANGED detector (arena.safe_ai.signals.detect), thrust levels 0.6 % / 1.0 % / 1.5 % (all three recorded, none selected), window 09:35-22:30 IST, one trade a day per level, entry = open of the
bar after the signal, SL = extreme -/+ 0.2 x 5-minute range proxy, T1/T2/T3 = 0.8/1.6/2.4 R (legs 40/30/30), time exit 23:15, futures round-trip cost 4 bp (net_edge.FUT_COST_BP). Each completed session is replayed CAUSALLY (the detector only sees ticks up to the
signal minute) once, after the session ended, and appended to an immutable log. Decision rule (fixed now): no verdict before 100 trades per level (about a year at ~0.45 trades a day); an interim look at 40 forward trades is descriptive only.
Nothing here reads the forward outcome to change a parameter."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .safe_ai import signals as SG
from ..research_engines.hybrid_sr_mtf import sim as SIM
from ..autoscalp.net_edge import FUT_COST_BP

IST = timezone(timedelta(hours=5, minutes=30))
SYMBOL = "NATURALGAS"
THRUSTS = (0.006, 0.010, 0.015)
FORWARD_FROM = "2026-11-03"
RULE_ID = "mcx-ng-uturn-fwd-v1-2026-11-03"
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(BASE, "data", "research", "safe_ai", "mcx_forward")
DB = os.path.join(BASE, "data", "market_history.db")
WIN_FROM, WIN_TO, EXIT_MIN, SESSION_END_MIN = 9 * 60 + 35, 22 * 60 + 30, 23 * 60 + 15, 23 * 60 + 55


def load_day(day: str, db: str | None = None, symbol: str = SYMBOL) -> pd.DataFrame:
    """1-minute FUTURE candles of one IST session day; the contract with the most bars that day (the liquid front contract)."""
    c = sqlite3.connect(f"file:{db or DB}?mode=ro", uri=True, timeout=60)
    try: d = pd.read_sql_query("SELECT bar_start, expiry, o, h, l, c FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' AND session_date_ist=? ORDER BY bar_start", c, params=(symbol, day))
    finally: c.close()
    if d.empty: return d
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).dt.tz_convert(IST); d = d[d.expiry == d.expiry.value_counts().idxmax()]
    return d.drop_duplicates("ts").set_index("ts").sort_index().rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["open", "high", "low", "close"]].astype(float)


def _range5(h, l, i) -> float:
    k = max(1, i - 70); return float(np.mean(h[k:i + 1] - l[k:i + 1])) * np.sqrt(5)


def replay_day(d: pd.DataFrame, thrust: float) -> dict | None:
    """Causal replay of one finished session. Returns the (single) trade or None."""
    if len(d) < 240: return None
    o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close")); t = np.asarray((d.index - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds(), float)
    mins = (d.index.hour * 60 + d.index.minute).to_numpy(); days = np.zeros(len(d), int); p = SG.Params(thrust=thrust, t_from=(9, 35), t_to=(22, 30))
    ticks = [(t[i] + 60.0, float(c[i])) for i in range(len(d))]
    for k in range(20, len(d) - 2):
        if mins[k] + 1 < WIN_FROM or mins[k] + 1 > WIN_TO: continue
        dd = SG.detect(ticks[:k + 1], ticks[k][0], p)                                            # sees ONLY the ticks up to the end of minute k
        if dd is None: continue
        side = 1 if dd["side"] == "CE" else -1; ext = dd["features"]["extreme"]; a = _range5(h, l, k); entry = float(o[k + 1]); sl = ext - side * 0.2 * a; risk = abs(entry - sl)
        if (entry - sl) * side <= 0 or risk <= 0 or risk > 4 * a: return None
        r = SIM.simulate(o, h, l, c, mins, days, k + 1, side, entry, sl, entry + side * 0.8 * risk, entry + side * 1.6 * risk, entry + side * 2.4 * risk, EXIT_MIN)
        gross = r["pts"] / entry * 1e4
        return {"side": "CE-idea (long)" if side > 0 else "PE-idea (short)", "signal_ist": d.index[k].strftime("%H:%M"), "entry_ist": d.index[k + 1].strftime("%H:%M"), "entry": entry, "sl": round(sl, 3), "t1": round(entry + side * 0.8 * risk, 3),
                "gross_bp": round(gross, 2), "net_bp": round(gross - FUT_COST_BP[SYMBOL], 2), "R": round(r["R"], 3), "exit_reason": r["reason"], "risk_bp": round(risk / entry * 1e4, 1), "reason": dd.get("reason"), "mfe_pts": round(r["mfe"], 3), "mae_pts": round(r["mae"], 3)}
    return None


def _read(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")] if os.path.exists(path) else []


def process(now: datetime | None = None, db: str | None = None, dir_: str | None = None, days: list[str] | None = None) -> dict:
    """Replay every completed session from FORWARD_FROM that is not recorded yet (append-only). A session is complete after 23:58 IST of its day or on any later day."""
    now = now or datetime.now(IST); dir_ = dir_ or DIR; os.makedirs(dir_, exist_ok=True); done_p = os.path.join(dir_, "days_done.jsonl"); log_p = os.path.join(dir_, "ng_forward_trades.jsonl")
    done = {r["day"] for r in _read(done_p)}; added = []
    cand = days or [(datetime.fromisoformat(FORWARD_FROM) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((now.date() - datetime.fromisoformat(FORWARD_FROM).date()).days + 1)]
    for day in cand:
        if day in done or day < FORWARD_FROM: continue
        complete = day < now.strftime("%Y-%m-%d") or (day == now.strftime("%Y-%m-%d") and now.hour * 60 + now.minute >= SESSION_END_MIN + 3)
        if not complete: continue
        d = load_day(day, db)
        n = 0
        if len(d) >= 240:
            for th in THRUSTS:
                tr = replay_day(d, th)
                if tr:
                    rec = {"rule": RULE_ID, "day": day, "thrust": th, **tr, "recorded_at": now.isoformat(timespec="seconds")}
                    with open(log_p, "a", encoding="utf-8") as f: f.write(json.dumps(rec) + "\n")
                    added.append(rec); n += 1
        with open(done_p, "a", encoding="utf-8") as f: f.write(json.dumps({"day": day, "bars": int(len(d)), "trades": n, "recorded_at": now.isoformat(timespec="seconds")}) + "\n")
    return {"added": added, "summary": summarize(dir_)}


def summarize(dir_: str | None = None) -> dict:
    dir_ = dir_ or DIR; tr = _read(os.path.join(dir_, "ng_forward_trades.jsonl")); days = _read(os.path.join(dir_, "days_done.jsonl")); out = {"rule": RULE_ID, "forward_from": FORWARD_FROM, "sessions_observed": len([d for d in days if d["bars"] >= 240]), "levels": {}}
    for th in THRUSTS:
        x = [r for r in tr if r["thrust"] == th]; net = np.array([r["net_bp"] for r in x], float)
        g = {"trades": len(x), "needed_for_verdict": 100, "status": "NOT_YET" if len(x) < 100 else "REVIEW"}
        if len(x):
            g.update({"win_rate_net": round(float((net > 0).mean()), 3), "gross_bp_per_trade": round(float(np.mean([r["gross_bp"] for r in x])), 1), "net_bp_per_trade": round(float(net.mean()), 1), "net_bp_total": round(float(net.sum()), 1)})
            by_day = pd.Series(net).groupby([r["day"] for r in x]); s, n_ = by_day.sum().to_numpy(), by_day.count().to_numpy()
            if len(s) >= 8:
                rng = np.random.default_rng(1); bs = [s[k].sum() / n_[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(3000))]; g["ci90_net_bp"] = [round(float(np.percentile(bs, 5)), 1), round(float(np.percentile(bs, 95)), 1)]
        out["levels"][f"thrust_{th}"] = g
    out["updated"] = datetime.now(IST).isoformat(timespec="seconds"); json.dump(out, open(os.path.join(dir_, "summary.json"), "w"), indent=1)
    return out
