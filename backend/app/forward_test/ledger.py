"""Forward-test evaluation + hash-chained ledger. Population: ai_paper_trades, strategy AUTOSCALP only, opened on/after the epoch start, status CLOSED. Outcome = unit-free
premium return minus MODELLED costs (same model as scripts/zerohero_claim_audit_v2.py). Frozen manifest = sha256 of the rule-bearing source files; a change starts a NEW epoch
(counted from the change date) and is written into the ledger - results never silently span a rule change."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import subprocess
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FROZEN_FILES = ["app/autoscalp/safeguards.py", "app/engines/scalp_strategy.py", "app/engines/option_engine.py", "app/engines/risk_engine.py", "app/signal_gate/final_signal_gate.py",
                "app/engines/state_classifier.py", "app/engines/sr_engine.py", "app/engines/regime_mtf.py"]      # rules_engine/policy*.yaml are SHADOW/candidate files, not part of the frozen AUTOSCALP
MIN_N, MIN_DAYS, ALPHA = 100, 30, 0.05
IDX = {"NIFTY", "BANKNIFTY", "SENSEX"}; MCX = {"NATURALGAS": "NATURALGAS_OPTION", "CRUDEOIL": "CRUDEOIL_OPTION"}


def file_hashes(root: str = ROOT, files=FROZEN_FILES) -> dict:
    out = {}
    for f in files:
        p = os.path.join(root, f)
        out[f] = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
    return out


def git_head(root: str = ROOT) -> str | None:
    try: return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception: return None


def load_trades(db_path: str, since_date: str) -> list[dict]:
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30); c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT trade_id,opened_ts,closed_ts,market,underlying,option_type,direction,strategy,entry,exit_price,stop_loss,target_1,pnl FROM ai_paper_trades WHERE status='CLOSED' AND strategy='AUTOSCALP' "
                                           "AND entry>0 AND exit_price IS NOT NULL AND substr(opened_ts,1,10)>=? ORDER BY opened_ts", (since_date,))]
    finally:
        c.close()


def modelled_net_pct(r: dict, lot_of=None) -> dict | None:
    from ..institutional_edge.costs import estimate_cost, estimate_index_option_cost
    from ..instrument_profiles import get_instrument_profile
    u, e, x = r["underlying"], r["entry"], r["exit_price"]
    try: lot = (lot_of or (lambda s: get_instrument_profile(s).lot_size.value))(u)
    except Exception: lot = None
    if not lot: return None
    if u in IDX: c = estimate_index_option_cost(e, x, lot, lots=1, slippage_points=0.75)["total_cost_points"]
    elif u in MCX:
        c = estimate_cost("MCX", MCX[u], slippage_points=0.5).total_cost_points
        if c is None: return None
    else: return None
    return {"gross_pct": (x - e) / e * 100.0, "cost_pct": c / e * 100.0, "net_pct": (x - e - c) / e * 100.0}


def wilson(k: int, n: int, z: float = 1.96):
    p = k / n; d = 1 + z * z / n; cc = p + z * z / (2 * n); h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return ((cc - h) / d, (cc + h) / d)


def evaluate(trades: list[dict], lot_of=None) -> dict:
    rows = []
    for t in trades:
        m = modelled_net_pct(t, lot_of)
        if m: rows.append({**t, **m})
    n = len(rows); res = {"n": n, "skipped_no_cost_model": len(trades) - n, "days": 0, "verdict": "ACCUMULATING"}
    if n == 0: return res
    net = [r["net_pct"] for r in rows]; by = {}
    for r in rows: by.setdefault(str(r["closed_ts"])[:10], []).append(r["net_pct"])
    dm = [sum(v) / len(v) for v in by.values()]; nd = len(dm); k = sum(1 for x in net if x > 0)
    w = [x for x in net if x > 0]; l = [-x for x in net if x <= 0]; be = (sum(l) / len(l)) / (sum(w) / len(w) + sum(l) / len(l)) if w and l else (0.0 if w else 1.0)       # no losses -> nothing to break even on; no wins -> impossible
    lo, hi = wilson(k, n); mean = sum(net) / n; p = None
    if nd >= 3:
        mm = sum(dm) / nd; sd = math.sqrt(sum((x - mm) ** 2 for x in dm) / (nd - 1))
        if sd > 0: t = mm / (sd / math.sqrt(nd)); p = 0.5 * math.erfc(t / math.sqrt(2))            # normal approx of the one-sided p (nd is small early on: conservative reading advised)
    res.update(days=nd, win_rate=k / n, wilson95=[lo, hi], breakeven_win_rate=be, mean_gross_pct=sum(r["gross_pct"] for r in rows) / n, mean_cost_pct=sum(r["cost_pct"] for r in rows) / n,
               mean_net_pct=mean, p_day_clustered_one_sided=p)
    if n >= MIN_N and nd >= MIN_DAYS:
        ok = mean > 0 and p is not None and p <= ALPHA and be is not None and lo > be
        res["verdict"] = "PASS_FOR_NEXT_STAGE" if ok else "FAIL"
    else: res["verdict"] = "ACCUMULATING"
    return res


def _canon(o) -> str: return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def last_hash(path: str) -> str:
    if not os.path.exists(path): return "GENESIS"
    h = "GENESIS"
    for line in open(path, encoding="utf-8"):
        if line.strip(): h = json.loads(line)["hash"]
    return h


def append(path: str, record: dict) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    prev = last_hash(path); body = {**record, "prev_hash": prev}; body["hash"] = hashlib.sha256((prev + _canon(record)).encode()).hexdigest()
    with open(path, "a", encoding="utf-8") as f: f.write(json.dumps(body, sort_keys=True, default=str) + "\n")
    return body


def verify_chain(path: str) -> tuple[bool, int, str | None]:
    prev = "GENESIS"; n = 0
    for i, line in enumerate(open(path, encoding="utf-8")):
        if not line.strip(): continue
        rec = json.loads(line); h = rec.pop("hash"); p = rec.pop("prev_hash")
        if p != prev or hashlib.sha256((prev + _canon(rec)).encode()).hexdigest() != h: return False, n, f"break at line {i + 1}"
        prev = h; n += 1
    return True, n, None


def now_iso() -> str: return datetime.now(timezone.utc).isoformat()


def candidate_v2(trades: list[dict], lot_of=None, capital: float = 200000.0) -> dict:
    """CANDIDATE V2 (pre-registered, evaluated alongside the frozen engine, never replaces it): the SAME AUTOSCALP trades filtered by the unified rules-engine policy v1.0.0
    (max 6 trades/day, 3 consecutive losses, 15-min cooldown after a loss, RR >= 1.3, premium >= 8, session windows, duplicates, concurrency). A filter can only remove trades,
    so V2 is by construction a subset of the frozen engine's trades; the sequential state uses only the trades V2 allows (path dependence ignored, as in the replay)."""
    from ..rules_engine.engine import Engine
    from ..rules_engine.replay import replay
    rows = [t for t in trades if t.get("pnl") is not None]
    rep = replay(rows, Engine.load(), capital=capital)
    allowed = {r["trade_id"] for r in rep["rows"] if r["allow"]}
    res = evaluate([t for t in trades if t["trade_id"] in allowed], lot_of); res["candidate"] = "V2_unified_policy_v1.0.0"; res["blocked"] = len(rows) - len(allowed)
    return res
