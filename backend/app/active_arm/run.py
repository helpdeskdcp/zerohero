"""Settle ACTIVE-arm signals with the arena engine and apply the pre-registered selection (max 10/day, one open per underlying). Read-only on market data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..arena import run as AR, settle as S, spread_model as SM, submit as SU
from . import shadow as SH

IST = timezone(timedelta(hours=5, minutes=30))
MAX_PER_DAY = 10
NAME = "ZEROHERO_AI_ACTIVE"


def to_signal(r: dict) -> dict | None:
    try: exp = SU.norm_expiry(str(r["expiry"]))
    except Exception: return None
    ev = [{"t": r["ts"] + float(r["max_hold_sec"] or 1500), "action": "EXIT_NOW"}]                  # the engine's own time exit, as a timed event
    return {"id": f"ARM{r['id']:06d}", "participant": NAME, "underlying": r["symbol"], "expiry": exp, "strike": r["strike"], "option_type": r["decision"].split("_")[1], "entry_mode": "NEAR",
            "entry": r["entry"], "entry_hi": r["entry"], "sl": r["stop_loss"], "targets": [x for x in (r["target_1"], r["target_2"]) if x], "t": r["ts"], "events": ev,
            "group": "ACTIVE_ONLY" if r["frozen_blocked"] else "COMMON"}


def day_of(t: float) -> str: return datetime.fromtimestamp(t + 19800, tz=timezone.utc).strftime("%Y-%m-%d")


def select_and_settle(rows: list[dict], market_db: str | None = None) -> list[dict]:
    """Sequential and causal: a signal is taken only if the day's count < MAX_PER_DAY and the previous taken trade on that underlying has closed before this signal."""
    taken, busy, per_day, spread = [], {}, {}, {}
    for r in sorted(rows, key=lambda x: x["ts"]):
        s = to_signal(r)
        if not s: continue
        d = day_of(s["t"])
        if per_day.get(d, 0) >= MAX_PER_DAY or busy.get(s["underlying"], 0.0) > s["t"]: continue
        hs = spread.get(s["underlying"]) or spread.setdefault(s["underlying"], SM.half_spread(s["underlying"], [d], market_db))
        res = S.settle(s, S.load_snaps(s["underlying"], s["expiry"], s["strike"], s["option_type"], d, market_db), half_spread=hs["half_spread"])
        res["group"] = s["group"]; res["spread_source"] = hs["source"]
        per_day[d] = per_day.get(d, 0) + 1
        busy[s["underlying"]] = (res.get("fill_t", s["t"]) + res.get("hold_sec", 1500.0)) if res.get("status") == "SETTLED" else s["t"] + float(r["max_hold_sec"] or 1500)
        taken.append(res)
    return taken


def summarize(results: list[dict]) -> dict:
    out = {"selected": len(results), "all": S.summarize(results), "groups": {}}
    for g in ("COMMON", "ACTIVE_ONLY"):
        rs = [r for r in results if r.get("group") == g]; out["groups"][g] = S.summarize(rs)
    days = {day_of(r["t"]) for r in results}; out["days"] = len(days); out["trades_per_day"] = (len(results) / len(days)) if days else 0.0
    n = out["all"].get("settled", 0)
    out["verdict"] = "ACCUMULATING"
    if n >= 100 and len({day_of(r["fill_t"]) for r in results if r.get("status") == "SETTLED"}) >= 30:
        from ..arena import compare as CMP
        dm = CMP.daily_net(results); import math
        v = list(dm.values()); m = sum(v) / len(v); sd = math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1)) if len(v) > 1 else 0.0
        p = 0.5 * math.erfc((m / (sd / math.sqrt(len(v)))) / math.sqrt(2)) if sd > 0 else 1.0
        out["p_day_clustered_one_sided"] = p; out["verdict"] = "PASS_FOR_NEXT_STAGE" if (out["all"]["net_rs"] > 0 and p <= 0.05) else "FAIL"
    return out


def run(db_path: str | None = None, market_db: str | None = None, since_ts: float = 0.0, until_ts: float = 1e18) -> dict:
    res = select_and_settle(SH.load(db_path, since_ts, until_ts), market_db)
    return {"results": res, "summary": summarize(res)}


def arena_signals(start: str, end: str, db_path: str | None = None) -> list[dict]:
    """Selected arm signals (not yet settled) as arena signals, for the labelled third participant."""
    lo = datetime.fromisoformat(start + "T00:00:00+05:30").timestamp(); hi = datetime.fromisoformat(end + "T23:59:59+05:30").timestamp()
    return [s for s in (to_signal(r) for r in SH.load(db_path, lo, hi)) if s]
