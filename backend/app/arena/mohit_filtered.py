"""ZeroHero FILTERED leaderboard for the INSTITUTION_TRADER ("Mohit") calls: a SEPARATE, READ-ONLY view next to the original board. Keyed by the original signal id so both reconcile. It never writes, never changes an outcome or the original ranking.
Inputs: the frozen-rule tags (research/hybrid_sr_mtf/forward/mohit_filter_tags.jsonl, appended nightly), the control tags, and the Arena's own settlement. Calls not yet tagged show PENDING_TAG.
Honesty rules built in: every figure carries its period label (IN_SAMPLE = the rule was read from these calls, NOT evidence; FORWARD = calls posted from 2026-11-02), only executable (settled) calls count as won / lost, data completeness is shown next to every rate, and no number is
called an actual result before the forward gate (>= 100 settled accepted calls, 90 % lower bound > 0) is met."""
from __future__ import annotations

import json
import os
import time

import numpy as np

from . import mohit_view as MV, run as R
from . import mohit_forward as FT

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TAGS = os.path.join(FT.DIR, "mohit_filter_tags.jsonl")
CONTROLS = os.path.join(FT.DIR, "mohit_filter_controls.jsonl")
SNAPSHOT = os.path.join(FT.DIR, FT.SNAPSHOT_NAME)
_C: dict = {}
TTL = 60.0


def _jl(path: str) -> list[dict]:
    out = []
    if os.path.exists(path):
        for l in open(path, encoding="utf-8"):
            try: out.append(json.loads(l))
            except Exception: pass
    return out


def _grp(items: list[tuple[dict, dict]]) -> dict:
    """items = [(tag, result)] -> executable-only statistics."""
    st = [(t, r) for t, r in items if r and r.get("status") == "SETTLED" and r.get("net_rs") is not None]
    g = FT._stats([r["net_rs"] for _, r in st], [t["post_ist"][:10] for t, _ in st]); g["calls"] = len(items); g["not_executable_or_no_data"] = len(items) - len(st)
    g["completeness"] = round(len(st) / len(items), 3) if items else None
    return g


def build(excl_path: str | None = None, tags_path: str = TAGS, controls_path: str = CONTROLS, results: dict | None = None, signals: list[dict] | None = None, snapshot_path: str = SNAPSHOT) -> dict:
    sigs = signals if signals is not None else MV._signals(excl_path)
    if results is None:
        results = {}
        frozen = FT.load_snapshot(snapshot_path); todo = [s for s in sigs if s["id"] not in frozen]                     # speed: calls with a frozen outcome are not settled again
        try:
            for part in (R.run(todo, market_db=os.path.join(BASE, "data", "market_history.db")).values() if todo else []):
                if isinstance(part, dict):
                    for x in part.get("results", []): results[x["id"]] = x
        except Exception:
            results = {}
    for k, v in FT.load_snapshot(snapshot_path).items(): results[k] = v                                                  # a frozen outcome wins over a recomputation (old quotes are archived)
    tags = {t["id"]: t for t in _jl(tags_path)}; ctl = {t["id"]: t for t in _jl(controls_path)}
    rows = []; by = {"IN_SAMPLE": {"ACCEPT": [], "REJECT": [], "PENDING": []}, "FORWARD": {"ACCEPT": [], "REJECT": [], "PENDING": []}}
    for s in sorted(sigs, key=lambda x: x["t"]):
        t = tags.get(s["id"]); r = results.get(s["id"]); period = (t or {}).get("period") or ("FORWARD" if time.strftime("%Y-%m-%d", time.localtime(s["t"] + 19800 - time.timezone)) >= FT.FORWARD_FROM else "IN_SAMPLE")
        dec = (t or {}).get("decision", "PENDING_TAG"); ok = r is not None and r.get("status") == "SETTLED" and r.get("net_rs") is not None; net = r.get("net_rs") if ok else None
        cls = "UNVERIFIED" if not ok else ("PENDING_TAG" if t is None else ("TRUE_ACCEPT" if dec == "ACCEPT" and net > 0 else "FALSE_ACCEPT" if dec == "ACCEPT" else "FALSE_REJECT" if dec == "REJECT" and net > 0 else "TRUE_REJECT" if dec == "REJECT" else "UNTAGGED"))
        rows.append({"id": s["id"], "post_ist": (t or {}).get("post_ist") or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(s["t"] + 19800)), "underlying": s["underlying"], "strike": s["strike"], "option_type": s["option_type"], "period": period, "decision": dec,
                     "reasons": (t or {}).get("reasons", []), "status": (r or {}).get("status", "NO_RESULT"), "net_rs": round(net, 1) if net is not None else None, "verified_class": cls})
        tt = {"post_ist": rows[-1]["post_ist"]}; bucket = "ACCEPT" if dec == "ACCEPT" else "REJECT" if dec == "REJECT" else "PENDING"
        by[period][bucket].append((tt, r))
    out = {"original": _grp([({"post_ist": r_["post_ist"]}, results.get(r_["id"])) for r_ in rows]), "periods": {}, "controls": {}, "rows": rows,
           "labels": {"IN_SAMPLE": "the frozen rule was read from these calls: descriptive only, NOT evidence", "FORWARD": "calls posted from " + FT.FORWARD_FROM + ": the only evidence that counts"}, "rule": FT.RULE_VERSION}
    out["original"]["calls_total"] = len(rows)
    for per in ("IN_SAMPLE", "FORWARD"):
        a, rj, pe = (_grp(by[per][k]) for k in ("ACCEPT", "REJECT", "PENDING"))
        cl = lambda c: sum(1 for x in rows if x["period"] == per and x["verified_class"] == c)
        fa, fr = cl("FALSE_ACCEPT"), cl("FALSE_REJECT"); ta, tr = cl("TRUE_ACCEPT"), cl("TRUE_REJECT")
        money = lambda c: round(sum(x["net_rs"] for x in rows if x["period"] == per and x["verified_class"] == c), 0)
        ver = ta + tr + fa + fr
        out["periods"][per] = {"calls_received": len(by[per]["ACCEPT"]) + len(by[per]["REJECT"]) + len(by[per]["PENDING"]), "accept": a["calls"], "reject": rj["calls"], "pending_tag": pe["calls"], "accept_stats": a, "reject_stats": rj,
                               "true_accept": ta, "false_accept": fa, "true_reject": tr, "false_reject": fr, "profit_filtered_out_rs": money("FALSE_REJECT"), "losses_avoided_rs": money("TRUE_REJECT"), "loss_taken_by_false_accepts_rs": money("FALSE_ACCEPT"),
                               "filter_accuracy": round((ta + tr) / ver, 3) if ver else None, "verified_calls": ver, "label": out["labels"][per]}
    fwd = out["periods"]["FORWARD"]["accept_stats"]; lb = (fwd.get("ci90_net_per_trade") or [None])[0]
    out["gate"] = {"needed_settled_forward_accepts": 100, "have": fwd.get("settled", 0), "lower_bound_gt_0": bool(lb is not None and lb > 0),
                   "status": "PASSED_SIZE_AND_INTERVAL" if fwd.get("settled", 0) >= 100 and lb is not None and lb > 0 else "NOT_YET" if fwd.get("settled", 0) < 100 else "FAILED",
                   "note": "Until the gate passes the filtered figures are labelled IN_SAMPLE / interim and must not be shown as actual performance."}
    for name in ("time_only", "index_only"):                                                                       # controls: same calls, simpler filters
        out["controls"][name] = {per: _grp([({"post_ist": x["post_ist"]}, results.get(x["id"])) for x in rows if x["period"] == per and (ctl.get(x["id"]) or {}).get(name) == "ACCEPT"]) for per in ("IN_SAMPLE", "FORWARD")}
    for per in ("IN_SAMPLE", "FORWARD"):                                                                           # random control: 200 random subsets of the same size drawn from this period's settled calls
        pool = np.array([results[x["id"]]["net_rs"] for x in rows if x["period"] == per and results.get(x["id"]) and results[x["id"]].get("status") == "SETTLED"], float); k = out["periods"][per]["accept_stats"].get("settled", 0)
        if k >= 3 and len(pool) > k:
            rng = np.random.default_rng(3); m = [rng.choice(pool, k, replace=False).mean() for _ in range(200)]
            out["controls"].setdefault("random_subset", {})[per] = {"settled": k, "mean_net_p5_p95": [round(float(np.percentile(m, 5)), 0), round(float(np.percentile(m, 95)), 0)]}
    return out


def cached(**kw) -> dict:
    hit = _C.get("v")
    if hit and time.time() - hit[0] < TTL and not kw: return hit[1]
    v = build(**kw)
    if not kw: _C["v"] = (time.time(), v)
    return v
