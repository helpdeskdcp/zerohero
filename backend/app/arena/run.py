"""Run the arena: load both sides' signals, settle with the same engine, summarize, report. AI side = ZeroHero AUTOSCALP paper trades opened since the arena start, re-settled by
the arena rules (entry as signalled, SL, T1) - NOT their own simulated exits - so both sides are judged by identical rules."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from . import settle as S, spread_model as SM, submit as SU

IST = timezone(timedelta(hours=5, minutes=30))
CAPITAL = 50000.0


def ai_signals(chanakya_db: str, since_date: str, until_date: str | None = None) -> list[dict]:
    c = sqlite3.connect(f"file:{chanakya_db}?mode=ro", uri=True, timeout=30); c.row_factory = sqlite3.Row
    try:
        rows = c.execute("SELECT trade_id,underlying,expiry,strike,option_type,opened_ts,closed_ts,entry,stop_loss,target_1,target_2 FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND entry>0 "
                         "AND stop_loss>0 AND target_1>0 AND substr(opened_ts,1,10)>=? AND substr(opened_ts,1,10)<=? ORDER BY opened_ts", (since_date, until_date or "9999-12-31")).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        try: exp = SU.norm_expiry(r["expiry"])
        except Exception: continue
        t = datetime.fromisoformat(r["opened_ts"]).timestamp()
        ev = [{"t": datetime.fromisoformat(r["closed_ts"]).timestamp(), "action": "EXIT_NOW"}] if r["closed_ts"] else []         # the AI's own recorded exit decision (TIME/TRAIL/...), replayed as the same event type
        out.append({"id": r["trade_id"], "participant": "ZEROHERO_AI", "underlying": r["underlying"], "expiry": exp, "strike": r["strike"], "option_type": r["option_type"], "entry_mode": "NEAR",
                    "entry": r["entry"], "entry_hi": r["entry"], "sl": r["stop_loss"], "targets": [x for x in (r["target_1"], r["target_2"]) if x], "t": t, "events": ev})
    return out


def human_signals(path: str | None = None, start: str | None = None, end: str | None = None, include_practice: bool = False, exclude_ids: set | None = None) -> list[dict]:
    """Scoring set. With a window (start/end, IST dates) the WINDOW decides: anything outside it never counts, anything inside counts regardless of the `practice` flag stored at submission time
    (the flag records the mode when it was submitted; if the start date is moved earlier by agreement, signals already inside the new window count). `exclude_ids` = entries the parties agreed
    were tests (ledger rows are immutable, so they are excluded here, publicly, via ARENA_EXCLUDE_IDS). Without a window, practice entries are excluded unless include_practice."""
    def day(r): return datetime.fromtimestamp(r["server_ts"] + 19800, tz=timezone.utc).strftime("%Y-%m-%d")
    allrecs = SU.load(path); evs: dict[str, list] = {}
    pmap = SU.participant_map(allrecs)
    corr = {r["signal_id"]: r["effective_ts"] for r in allrecs if r.get("type") == "HUMAN_TS_CORRECTION"}         # earlier-only latency corrections for channel-ingested signals
    for r in allrecs:
        if r.get("type") == "HUMAN_EVENT": evs.setdefault(r["signal_id"], []).append({k: r[k] for k in ("action", "sl", "targets", "trail_points", "trail_pct") if k in r} | {"t": r["server_ts"]})
    rows = [r for r in allrecs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and (include_practice or (start is not None or end is not None) or not r.get("practice")) and r["id"] not in (exclude_ids or set()) and (not start or day(r) >= start) and (not end or day(r) <= end)]
    return [{"id": r["id"], "participant": pmap.get(r["id"], r["participant"]), "underlying": r["underlying"], "expiry": r["expiry"], "strike": r["strike"], "option_type": r["option_type"], "entry_mode": r["entry_mode"],
             "entry": r["entry"], "entry_hi": r["entry"], "sl": r["sl"], "targets": r["targets"], "t": min(r["server_ts"], corr.get(r["id"], r["server_ts"])), "events": evs.get(r["id"], []), **({"valid_sec": r["valid_sec"]} if r.get("valid_sec") else {})} for r in rows]


def run(sigs: list[dict], *, market_db: str | None = None, policy: str = "T1", capital: float = CAPITAL) -> dict:
    by_part: dict[str, list] = {}; spread_cache: dict[str, dict] = {}
    for s in sigs:
        day = datetime.fromtimestamp(s["t"] + S.IST_SEC, tz=timezone.utc).strftime("%Y-%m-%d")
        hs = spread_cache.get(s["underlying"]) or spread_cache.setdefault(s["underlying"], SM.half_spread(s["underlying"], [day], market_db))
        snaps = S.load_snaps(s["underlying"], s["expiry"], s["strike"], s["option_type"], day, market_db)
        r = S.settle(s, snaps, half_spread=hs["half_spread"], policy=policy); r["spread_source"] = hs["source"]
        by_part.setdefault(s["participant"], []).append(r)
    return {p: {"summary": S.summarize(rs, capital), "results": rs} for p, rs in by_part.items()} | {"_spread_models": spread_cache}
