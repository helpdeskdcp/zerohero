"""Forward test of the frozen Mohit-profile filter (RESEARCH, read-only). Frozen 2026-10-30 (see data/research/hybrid_sr_mtf/forward/RULE_FROZEN_2026-10-30.json):
ACCEPT a call only if the underlying is NIFTY / BANKNIFTY / SENSEX, it was posted between 09:15 and 10:15 IST, and the 15m bias at the post time is not against the call. Otherwise REJECT (with every failing reason).
Each ledger call is tagged ONCE (append-only file, immutable), using only data known at its post time. Outcomes are the Arena's own settlement; nothing here orders, scores or changes anything. Calls posted before FORWARD_FROM are tagged
IN_SAMPLE (the rule was read from them: not evidence); calls on/after it are FORWARD. Days excluded from learning (data/arena/learning_exclusions.json) are never tagged."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np

IST = timezone(timedelta(hours=5, minutes=30))
RULE_VERSION = "mohit-profile-v1-2026-10-30"
FORWARD_FROM = "2026-11-02"
INDEX_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX")
WINDOW = (9 * 60 + 15, 10 * 60 + 15)
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIR = os.path.join(BASE, "data", "research", "hybrid_sr_mtf", "forward")


def decide(und: str, minute_of_day: int, side: int, bias: int | None) -> dict:
    """side +1 = CE (bullish call idea), -1 = PE. bias None = unknown (no index candles at that time)."""
    reasons = []
    if und not in INDEX_UNDERLYINGS: reasons.append("NOT_NSE_BSE_INDEX")
    if not (WINDOW[0] <= minute_of_day <= WINDOW[1]): reasons.append("POSTED_OUTSIDE_0915_1015")
    if bias is None:
        if not reasons: return {"decision": "UNTAGGED_NO_INDEX_DATA", "reasons": ["no index candles at the post time"], "bias15": None}
    elif bias == -side: reasons.append("15M_BIAS_AGAINST")
    return {"decision": "REJECT" if reasons else "ACCEPT", "reasons": reasons, "bias15": bias}


def _bias_at(und: str, t: float, cache: dict):
    from ..research_engines.hybrid_sr_mtf import data as D, engine as E
    dt = datetime.fromtimestamp(t, IST); key = (und, dt.date())
    if key not in cache:
        start = (dt - timedelta(days=12)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"); end = (dt + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        d = D.load_index_1m(und, start, end)
        cache[key] = D.prepare(und, d) if len(d) > 300 else None
    prep = cache[key]
    if prep is None: return None
    tam = int(np.datetime64(dt.date(), "D").astype(np.int64)) * 1440 + dt.hour * 60 + dt.minute
    day_has = (prep.t1 // 1440 == tam // 1440).any()
    i15 = int(np.searchsorted(prep.tf15.end, tam, side="right")) - 1
    if not day_has or i15 < 55: return None
    return int(E._bias15(prep, i15))


def tag_new(signals: list[dict], tags_path: str, bias_fn=None, now: datetime | None = None) -> list[dict]:
    """Tag every signal not yet tagged; append-only. Returns the new records."""
    done = set()
    if os.path.exists(tags_path):
        for line in open(tags_path, encoding="utf-8"):
            try: done.add(json.loads(line)["id"])
            except Exception: pass
    cache: dict = {}; bias_fn = bias_fn or (lambda s, side: _bias_at(s["underlying"], s["t"], cache)); new = []; now = now or datetime.now(IST)
    for s in sorted(signals, key=lambda x: x["t"]):
        if s["id"] in done: continue
        dt = datetime.fromtimestamp(s["t"], IST); side = 1 if s["option_type"] == "CE" else -1
        bias = bias_fn(s, side) if s["underlying"] in INDEX_UNDERLYINGS and WINDOW[0] <= dt.hour * 60 + dt.minute <= WINDOW[1] else None
        r = decide(s["underlying"], dt.hour * 60 + dt.minute, side, bias)
        rec = {"id": s["id"], "rule": RULE_VERSION, "post_ist": dt.strftime("%Y-%m-%d %H:%M:%S"), "period": "FORWARD" if dt.strftime("%Y-%m-%d") >= FORWARD_FROM else "IN_SAMPLE",
               "underlying": s["underlying"], "option_type": s["option_type"], "tagged_at": now.isoformat(timespec="seconds"), **r}
        new.append(rec)
    if new:
        os.makedirs(os.path.dirname(tags_path), exist_ok=True)
        with open(tags_path, "a", encoding="utf-8") as f:
            for r in new: f.write(json.dumps(r) + "\n")
    return new


def control_decisions(und: str, minute_of_day: int) -> dict:
    """Controls tagged on the SAME calls so the full filter can be attributed (a filter must beat both): time-only (posted 09:15-10:15, any instrument) and index-only (NIFTY / BANKNIFTY / SENSEX, any time)."""
    return {"time_only": "ACCEPT" if WINDOW[0] <= minute_of_day <= WINDOW[1] else "REJECT", "index_only": "ACCEPT" if und in INDEX_UNDERLYINGS else "REJECT"}


def tag_controls(signals: list[dict], controls_path: str, now: datetime | None = None) -> list[dict]:
    """Append-only control tags (own file, the frozen rule's tag file is never touched)."""
    done = set()
    if os.path.exists(controls_path):
        for line in open(controls_path, encoding="utf-8"):
            try: done.add(json.loads(line)["id"])
            except Exception: pass
    now = now or datetime.now(IST); new = []
    for s in sorted(signals, key=lambda x: x["t"]):
        if s["id"] in done: continue
        dt = datetime.fromtimestamp(s["t"], IST)
        new.append({"id": s["id"], "post_ist": dt.strftime("%Y-%m-%d %H:%M:%S"), "period": "FORWARD" if dt.strftime("%Y-%m-%d") >= FORWARD_FROM else "IN_SAMPLE", "underlying": s["underlying"], **control_decisions(s["underlying"], dt.hour * 60 + dt.minute), "tagged_at": now.isoformat(timespec="seconds")})
    if new:
        os.makedirs(os.path.dirname(controls_path), exist_ok=True)
        with open(controls_path, "a", encoding="utf-8") as f:
            for r in new: f.write(json.dumps(r) + "\n")
    return new


SNAPSHOT_NAME = "mohit_results_snapshot.jsonl"
_KEEP = ("status", "net_rs", "gross_rs", "cost_rs", "fill", "exit", "exit_reason", "fill_t", "hold_sec", "lot", "lots")


def snapshot_results(results: dict, signals: list[dict], path: str, now: datetime | None = None) -> int:
    """Freeze the Arena outcome of a call once it is final (SETTLED always; any other status only after the call's own day): the Arena recomputes outcomes from the captured quotes, and the capture database keeps only
    ~5 days (older rows are archived), so an old outcome can no longer be recomputed. Append-only, one record per call id, never rewritten."""
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try: done.add(json.loads(line)["id"])
            except Exception: pass
    now = now or datetime.now(IST); today = now.strftime("%Y-%m-%d"); new = []
    for s in signals:
        r = results.get(s["id"])
        if s["id"] in done or not r: continue
        day = datetime.fromtimestamp(s["t"], IST).strftime("%Y-%m-%d")
        if r.get("status") == "SETTLED" or day < today: new.append({"id": s["id"], **{k: r.get(k) for k in _KEEP}, "frozen_at": now.isoformat(timespec="seconds"), "source": "arena_settlement"})
    if new:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in new: f.write(json.dumps(r) + "\n")
    return len(new)


def load_snapshot(path: str) -> dict:
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try: r = json.loads(line); out.setdefault(r["id"], r)
            except Exception: pass
    return out


def _stats(nets: list[float], days: list[str]) -> dict:
    if not nets: return {"settled": 0}
    a = np.array(nets); import pandas as pd
    s = pd.Series(a).groupby(np.array(days)); sums = s.sum().to_numpy(); cnt = s.count().to_numpy(); ci = [None, None]
    if len(sums) >= 4:
        rng = np.random.default_rng(1); bs = [sums[k].sum() / cnt[k].sum() for k in (rng.integers(0, len(sums), len(sums)) for _ in range(3000))]; ci = [round(float(np.percentile(bs, 5)), 1), round(float(np.percentile(bs, 95)), 1)]
    pos, neg = float(a[a > 0].sum()), float(-a[a < 0].sum())
    return {"settled": int(len(a)), "win_rate": round(float((a > 0).mean()), 3), "net_per_trade_rs": round(float(a.mean()), 1), "net_total_rs": round(float(a.sum()), 0), "profit_factor": round(pos / neg, 2) if neg > 0 else None, "ci90_net_per_trade": ci, "days": int(len(sums))}


def summarize(tags_path: str, results: dict[str, dict]) -> dict:
    """results = {signal id: settlement result}. Gate (frozen): >= 100 settled ACCEPT calls in the FORWARD period, 90 % day-cluster lower bound of net/trade > 0."""
    tags = [json.loads(l) for l in open(tags_path, encoding="utf-8")] if os.path.exists(tags_path) else []
    out = {"rule": RULE_VERSION, "forward_from": FORWARD_FROM, "tagged": len(tags), "groups": {}}
    for period in ("FORWARD", "IN_SAMPLE"):
        for dec in ("ACCEPT", "REJECT"):
            sel = [t for t in tags if t["period"] == period and t["decision"] == dec]; st = [(results[t["id"]], t) for t in sel if t["id"] in results and results[t["id"]].get("status") == "SETTLED"]
            g = _stats([r["net_rs"] for r, _ in st], [t["post_ist"][:10] for _, t in st]); g["calls"] = len(sel); g["not_settled"] = len(sel) - len(st); out["groups"][f"{period}/{dec}"] = g
        out["groups"][f"{period}/UNTAGGED"] = {"calls": sum(1 for t in tags if t["period"] == period and t["decision"].startswith("UNTAGGED"))}
    fa = out["groups"]["FORWARD/ACCEPT"]; lb = (fa.get("ci90_net_per_trade") or [None])[0]
    out["gate"] = {"needed_settled": 100, "have": fa.get("settled", 0), "lower_bound_gt_0": bool(lb is not None and lb > 0), "status": "PASSED_SIZE_AND_INTERVAL" if fa.get("settled", 0) >= 100 and lb is not None and lb > 0 else "NOT_YET" if fa.get("settled", 0) < 100 else "FAILED"}
    return out


def run(tags_path: str | None = None, out_dir: str | None = None) -> dict:
    from . import mohit_view as MV, run as R
    tags_path = tags_path or os.path.join(DIR, "mohit_filter_tags.jsonl"); out_dir = out_dir or DIR
    sigs = MV._signals()                                              # learning-excluded days are already left out here
    new = tag_new(sigs, tags_path)
    tag_controls(sigs, os.path.join(os.path.dirname(tags_path), "mohit_filter_controls.jsonl"))
    res = {}
    try:
        for part in R.run(sigs, market_db=os.path.join(BASE, "data", "market_history.db")).values():
            if isinstance(part, dict):
                for x in part.get("results", []): res[x["id"]] = x
    except Exception as e:
        res = {}; new_err = f"{type(e).__name__}: {e}"
    if res: snapshot_results(res, sigs, os.path.join(os.path.dirname(tags_path), SNAPSHOT_NAME))
    summ = summarize(tags_path, res); summ["new_tags"] = len(new); summ["updated"] = datetime.now(IST).isoformat(timespec="seconds")
    os.makedirs(out_dir, exist_ok=True)
    json.dump(summ, open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    return summ
