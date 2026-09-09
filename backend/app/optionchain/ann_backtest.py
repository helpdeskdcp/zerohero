"""
Layer 4 harness -- does the ANN confirmation layer earn its place?

Replays the captured option-chain snapshots (market_history.db `option_greeks`
snap_keys + `quote_snapshots` OI + spot), builds an `OptionStructureState` at
each, derives the structure's directional bias, labels it by the forward spot
path, then runs a PURGED WALK-FORWARD:

  A = structure-only    -- every non-NEUTRAL structure signal
  B = structure + ANN   -- keep signals with ANN P(win) >= TRAIN-picked threshold

Verdict GO / NO-GO / INSUFFICIENT_SAMPLE. The ANN is kept ONLY if B beats A on
pooled OOS by a margin, without cherry-picking the sample away, and holds across
folds -- and never if A's own OOS expectancy is <= 0 (ANN cannot rescue a
non-positive base). Read-only; no order path.

    ./venv/bin/python -m app.optionchain.ann_backtest --underlying NIFTY
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from .chain import OptionChain, StrikeRow, OptionLeg, expiry_phase
from .structure import analyze as analyze_structure
from .qualify import structure_direction
from .ann_confirm import OcAnnConfirm, oc_feat, DEFAULT_ANN_CFG

_IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_CFG = {
    "horizon_min": 30,          # forward window for the label
    "move_pct": 0.50,           # +/- this % move = the bracket (R unit). Big enough
                                # that the label is a real directional call, not
                                # 15-min momentum autocorrelation inside the noise.
    "drop_unresolved": True,    # neither bracket touched in the window -> drop (don't guess)
    "min_dqs": 40.0,            # skip snapshots whose chain DQ is below this
    "wf_folds": 3,
    "purge_min": 15,            # drop TRAIN records within this of an OOS session start
    # verdict gates
    "min_oos": 200,             # pooled OOS directional signals
    "min_folds": 3,
    "min_fold_oos": 40,
    "min_lift_R": 0.05,         # B expectancy must beat A by >= this (in R)
    "min_keep_frac": 0.35,      # B must keep >= this fraction of A's OOS signals
    "min_pos_fold_frac": 0.6,   # B must beat A in >= this fraction of folds
    "ann": dict(DEFAULT_ANN_CFG),
}


# --------------------------------------------------------------------------- #
#  replay: build minimal chains from the capture                               #
# --------------------------------------------------------------------------- #
def _db(path=None):
    p = path or os.environ.get("CHANAKYA_HIST_DB_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "market_history.db")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _num(v):
    try:
        f = float(v)
        return f if f == f and math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _epoch(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _spot_series(con, u):
    rows = con.execute(
        "SELECT received_ts, ltp FROM quote_snapshots "
        "WHERE symbol=? AND kind IN ('INDEX','FUTURE') AND ltp IS NOT NULL "
        "ORDER BY received_ts", (u,)).fetchall()
    return [(_epoch(r["received_ts"]), _num(r["ltp"])) for r in rows
            if _epoch(r["received_ts"]) and _num(r["ltp"])]


def _spot_at(series, t):
    if not series:
        return None
    lo, hi = 0, len(series) - 1
    if t <= series[0][0]:
        return series[0][1]
    if t >= series[-1][0]:
        return series[-1][1]
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    return series[lo][1]


def _idx_after(series, t):
    lo, hi = 0, len(series)
    while lo < hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _label(series, t0, s0, direction, cfg):
    """+1 if the +move_pct bracket in `direction` is touched before the -bracket
    within horizon_min; -1 if the opposite; None if neither (unresolved)."""
    if not s0:
        return None
    up = s0 * (1 + cfg["move_pct"] / 100.0)
    dn = s0 * (1 - cfg["move_pct"] / 100.0)
    t_end = t0 + cfg["horizon_min"] * 60
    for et, sp in series[_idx_after(series, t0):]:
        if et > t_end:
            break
        if sp >= up:
            return 1 if direction == "LONG" else -1
        if sp <= dn:
            return -1 if direction == "LONG" else 1
    if cfg["drop_unresolved"]:
        return None
    sp_end = _spot_at(series, t_end)
    if sp_end is None:
        return None
    net = sp_end - s0
    return (1 if net > 0 else -1) if direction == "LONG" else (1 if net < 0 else -1)


def _oi_index(con, u):
    """{(strike, option_type): [(epoch, oi, oi_change), ...] sorted}. One pass."""
    idx: dict = {}
    for r in con.execute(
        "SELECT strike, option_type, received_ts, oi, oi_change FROM quote_snapshots "
        "WHERE symbol=? AND kind='OPTION' AND oi IS NOT NULL ORDER BY received_ts", (u,)):
        k = _num(r["strike"])
        e = _epoch(r["received_ts"])
        if k is None or e is None:
            continue
        idx.setdefault((k, r["option_type"]), []).append((e, _num(r["oi"]), _num(r["oi_change"])))
    return idx


def _oi_at(seq, t):
    if not seq:
        return (None, None)
    lo, hi = 0, len(seq)
    while lo < hi:
        mid = (lo + hi) // 2
        if seq[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return (seq[lo - 1][1], seq[lo - 1][2]) if lo else (None, None)


def _greek_snaps(con, u, limit=None):
    """{snap_key: [greek rows]} -- ONE scan (option_greeks has no snap_key index,
    so a per-snap WHERE snap_key=? is a full table scan each time)."""
    by_sk: dict = {}
    for r in con.execute(
        "SELECT snap_key, received_ts, expiry, strike, option_type, delta, gamma, "
        "theta, vega, iv, trade_volume FROM option_greeks WHERE underlying=? "
        "ORDER BY snap_key", (u,)):
        by_sk.setdefault(r["snap_key"], []).append(r)
    keys = sorted(by_sk)
    if limit:
        keys = keys[:limit]
    return [(k, by_sk[k]) for k in keys]


def build_records(underlying: str, cfg: dict, *, db_path=None, limit=None) -> list[dict]:
    u = str(underlying).upper()
    con = _db(db_path)
    try:
        spot = _spot_series(con, u)
        oi_idx = _oi_index(con, u)
        recs: list[dict] = []
        for sk, grows in _greek_snaps(con, u, limit):
            if len(grows) < 8:
                continue
            ts = max(r["received_ts"] for r in grows)
            t0 = _epoch(ts)
            exp = grows[0]["expiry"]
            rows_by_k: dict = {}
            for r in grows:
                k = _num(r["strike"])
                if k is None:
                    continue
                leg = OptionLeg(delta=_num(r["delta"]), gamma=_num(r["gamma"]),
                                theta=_num(r["theta"]), vega=_num(r["vega"]),
                                iv=_num(r["iv"]), volume=_num(r["trade_volume"]))
                oi, oich = _oi_at(oi_idx.get((k, r["option_type"]), []), t0)
                leg.oi, leg.oi_change = oi, oich
                row = rows_by_k.setdefault(k, StrikeRow(strike=k))
                if str(r["option_type"]).upper() == "CE":
                    row.ce = leg
                else:
                    row.pe = leg
            s0 = _spot_at(spot, t0)
            ch = OptionChain(underlying=u, expiry=exp, ts=ts, source="replay", spot=s0,
                             rows=list(rows_by_k.values()))
            ch.expiry_ctx = expiry_phase(exp, datetime.fromtimestamp(t0, _IST))
            ch.sort().compute_atm()
            st = analyze_structure(ch)
            q = (st.quality or {})
            if q.get("dqs") is not None and q["dqs"] < cfg["min_dqs"]:
                continue
            sd = structure_direction(st)
            bias = sd["bias"]
            if bias == "NEUTRAL":
                continue
            lab = _label(spot, t0, s0, bias, cfg)
            if lab is None:
                continue
            recs.append({
                "ts": ts, "session": datetime.fromtimestamp(t0, _IST).date().isoformat(),
                "direction": bias, "feat": oc_feat(st, bias, ts),
                "win": lab > 0, "r_multiple": float(lab),
            })
        return recs
    finally:
        con.close()


# --------------------------------------------------------------------------- #
#  purged walk-forward + A/B                                                    #
# --------------------------------------------------------------------------- #
def _metrics(recs: list[dict]) -> dict:
    n = len(recs)
    if not n:
        return {"n": 0, "win_pct": None, "expectancy_R": None}
    wins = sum(1 for r in recs if r["win"])
    return {"n": n, "win_pct": round(100.0 * wins / n, 2),
            "expectancy_R": round(sum(r["r_multiple"] for r in recs) / n, 4)}


def _brier(recs, prob_fn) -> float | None:
    if not recs:
        return None
    return round(sum((prob_fn(r) - (1.0 if r["win"] else 0.0)) ** 2 for r in recs) / len(recs), 4)


def walk_forward(recs: list[dict], cfg: dict) -> dict:
    sess = sorted({r["session"] for r in recs})
    k = min(cfg["wf_folds"], max(1, len(sess) - 1))
    if k < 1 or len(sess) < 2:
        return {"folds": [], "pooled_A": _metrics([]), "pooled_B": _metrics([]),
                "reason": "not enough sessions for a walk-forward"}
    # OOS blocks: the last k sessions, one per fold (expanding TRAIN)
    oos_sessions = sess[-k:]
    folds, poolA, poolB = [], [], []
    for i, oos_s in enumerate(oos_sessions):
        train_s = set(sess[: sess.index(oos_s)])
        if not train_s:
            continue
        oos_start = min(_epoch(r["ts"]) for r in recs if r["session"] == oos_s)
        purge = cfg["purge_min"] * 60
        train = [r for r in recs if r["session"] in train_s
                 and _epoch(r["ts"]) <= oos_start - purge]
        oos = [r for r in recs if r["session"] == oos_s]
        ann = OcAnnConfirm(cfg["ann"]).fit(train)
        oos_b = ann.filter(oos) if ann.status == "OK" else []
        a, b = _metrics(oos), _metrics(oos_b)
        base = ann.train_base if ann.train_base is not None else (
            (sum(1 for t in train if t["win"]) / len(train)) if train else 0.5)
        folds.append({
            "fold": i + 1, "oos_session": oos_s, "n_train": len(train),
            "ann": ann.info(), "A": a, "B": b,
            "keep_frac": round(b["n"] / a["n"], 3) if a["n"] else None,
            "brier_A": _brier(oos, lambda r: base),
            "brier_B": _brier(oos_b, lambda r: r.get("ann_p_win", 0.5)),
            "B_beats_A": bool(b["expectancy_R"] is not None and a["expectancy_R"] is not None
                              and b["expectancy_R"] > a["expectancy_R"]),
        })
        poolA += oos
        poolB += oos_b
    return {"folds": folds, "pooled_A": _metrics(poolA), "pooled_B": _metrics(poolB),
            "pooled_keep_frac": round(len(poolB) / len(poolA), 3) if poolA else None,
            "n_sessions": len(sess)}


def _verdict(wf: dict, cfg: dict) -> dict:
    A, B = wf["pooled_A"], wf["pooled_B"]
    folds = wf.get("folds") or []
    reasons: list[str] = []

    if len(folds) < cfg["min_folds"]:
        reasons.append(f"only {len(folds)} walk-forward folds (< {cfg['min_folds']})")
    if (A["n"] or 0) < cfg["min_oos"]:
        reasons.append(f"pooled OOS n={A['n']} (< {cfg['min_oos']})")
    if any((f["A"]["n"] or 0) < cfg["min_fold_oos"] for f in folds):
        reasons.append(f"a fold has OOS n < {cfg['min_fold_oos']}")
    if any(f["ann"]["status"] != "OK" for f in folds):
        reasons.append("a fold's ANN was INSUFFICIENT (min_train not met)")
    if reasons:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reasons": reasons}

    if (A["expectancy_R"] or -1) <= 0:
        return {"verdict": "NO-GO",
                "reasons": [f"structure-only OOS expectancy {A['expectancy_R']}R <= 0 "
                            f"-- the ANN cannot rescue a non-positive base"]}

    lift = round((B["expectancy_R"] or 0) - (A["expectancy_R"] or 0), 4)
    pos_folds = sum(1 for f in folds if f["B_beats_A"])
    checks = {
        "expectancy_lift_R": (lift, lift >= cfg["min_lift_R"]),
        "keep_frac": (wf["pooled_keep_frac"], (wf["pooled_keep_frac"] or 0) >= cfg["min_keep_frac"]),
        "win_pct_not_worse": ((B["win_pct"], A["win_pct"]),
                              (B["win_pct"] or 0) >= (A["win_pct"] or 0)),
        "folds_B_beats_A": (f"{pos_folds}/{len(folds)}",
                            pos_folds >= math.ceil(cfg["min_pos_fold_frac"] * len(folds))),
    }
    ok = all(v[1] for v in checks.values())
    return {"verdict": "GO" if ok else "NO-GO",
            "checks": {k: {"value": v[0], "pass": v[1]} for k, v in checks.items()},
            "reasons": [] if ok else [k for k, v in checks.items() if not v[1]]}


def run(underlying: str = "NIFTY", *, cfg: dict | None = None, db_path=None, limit=None) -> dict:
    c = {**DEFAULT_CFG, **(cfg or {})}
    c["ann"] = {**DEFAULT_ANN_CFG, **(c.get("ann") or {})}
    recs = build_records(underlying, c, db_path=db_path, limit=limit)
    wf = walk_forward(recs, c)
    out = {
        "underlying": str(underlying).upper(), "cfg": {k: c[k] for k in c if k != "ann"},
        "n_records": len(recs),
        "sessions": sorted({r["session"] for r in recs}),
        "overall_A": _metrics(recs),
        "walk_forward": wf,
        "verdict": _verdict(wf, c),
        "note": "research-only -- ANN is kept only on a demonstrated OOS improvement; "
                "not wired to any order path.",
    }
    return out


def _md(res: dict) -> str:
    v = res["verdict"]
    L = [f"# OPTION-CHAIN ANN CONFIRMATION -- {res['underlying']}", "",
         f"**VERDICT: {v['verdict']}**", ""]
    if v.get("reasons"):
        L += ["Reasons:"] + [f"- {r}" for r in v["reasons"]] + [""]
    L += [f"- records (labeled directional signals): {res['n_records']}",
          f"- sessions: {', '.join(res['sessions']) or '-'}",
          f"- structure-only (all records): {res['overall_A']}", ""]
    wf = res["walk_forward"]
    L += [f"pooled OOS A: {wf['pooled_A']}", f"pooled OOS B: {wf['pooled_B']}",
          f"pooled keep-fraction: {wf.get('pooled_keep_frac')}", "", "## folds", ""]
    for f in wf.get("folds", []):
        L.append(f"- fold {f['fold']} (OOS {f['oos_session']}, train n={f['n_train']}, "
                 f"ann={f['ann']['status']} thr={f['ann'].get('threshold')}): "
                 f"A {f['A']} | B {f['B']} | keep {f['keep_frac']} | "
                 f"Brier A {f['brier_A']} B {f['brier_B']} | B>A {f['B_beats_A']}")
    if v.get("checks"):
        L += ["", "## GO checks", ""]
        for k, d in v["checks"].items():
            L.append(f"- {k}: {d['value']}  ->  {'PASS' if d['pass'] else 'FAIL'}")
    L += ["", "_" + res["note"] + "_", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = run(a.underlying, limit=a.limit)
    txt = json.dumps(res, indent=2, default=str) if a.json else _md(res)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(txt)
        print(f"wrote {a.out}")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
