"""Autonomous scalper self-reporting.

`session_report(day)` — per-symbol rollup of a trading day (trades, W/L/FLAT,
net points, avg R, exit-reason and decision/regime distribution, ZTH lottery
legs, and why entries were refused). `self_check()` — one-glance operational
readiness. Both are read-only over the live store.
"""
from datetime import datetime, timedelta, timezone

from .. import db

_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_today() -> str:
    return datetime.now(_IST).date().isoformat()


def _hist(pairs):
    """[(key, n), ...] rows -> {key: n} with None keys labelled."""
    return {(k if k not in (None, "") else "(none)"): int(n) for k, n in pairs}


def session_report(day: str | None = None) -> dict:
    """Per-symbol rollup for an IST trading day (default: today IST)."""
    day = day or _ist_today()
    lo, hi = day + " 00:00:00", day + " 23:59:59"
    ist = "'+5 hours','30 minutes'"

    with db.db() as conn:
        def q(sql, *p):
            return conn.execute(sql, p).fetchall()

        trades = [dict(r) for r in q(
            f"SELECT underlying, option_type, strike, "
            f"  COALESCE(strategy,'') strategy, status, result, "
            f"  ROUND(pnl,2) pnl, exit_reason, ROUND(mfe,2) mfe, ROUND(mae,2) mae, "
            f"  substr(opened_ts,12,5) op, substr(closed_ts,12,5) cl, "
            f"  CAST((julianday(COALESCE(closed_ts,'now'))-julianday(opened_ts))*86400 AS INT) held_s, "
            f"  ROUND(entry,2) entry, ROUND(exit_price,2) exit_price, ROUND(risk_ref,2) risk_ref "
            f"FROM ai_paper_trades "
            f"WHERE COALESCE(strategy,'') LIKE 'AUTOSCALP%' AND date(opened_ts,{ist}) = ? "
            f"ORDER BY opened_ts", day)]

        dec = q(
            f"SELECT symbol, decision, COUNT(*) n FROM live_market_snapshots "
            f"WHERE date(ts,{ist}) = ? GROUP BY symbol, decision", day)
        reg = q(
            f"SELECT symbol, regime, COUNT(*) n FROM live_market_snapshots "
            f"WHERE date(ts,{ist}) = ? GROUP BY symbol, regime", day)
        blk = q(
            f"SELECT symbol, reason, COUNT(*) n FROM live_market_snapshots "
            f"WHERE date(ts,{ist}) = ? AND reason LIKE 'BLOCKED[%' GROUP BY symbol, reason", day)

    # a symbol is "in the session" if it traded, was blocked, or produced at
    # least one BUY-lean decision — this drops stale snapshot-only noise.
    buy_syms = {r[0] for r in dec if str(r[1]).startswith("BUY")}
    symbols = sorted({t["underlying"] for t in trades} | buy_syms | {r[0] for r in blk})
    per_symbol, zth = {}, []
    tot = {"trades": 0, "wins": 0, "losses": 0, "flat": 0, "net_points": 0.0}

    for sym in symbols:
        core = [t for t in trades if t["underlying"] == sym and t["strategy"] == "AUTOSCALP"]
        closed = [t for t in core if t["status"] != "OPEN"]
        wins = sum(t["result"] == "WIN" for t in closed)
        losses = sum(t["result"] == "LOSS" for t in closed)
        flat = sum(t["result"] not in ("WIN", "LOSS") for t in closed)
        net = round(sum((t["pnl"] or 0) for t in closed), 2)
        rs = [(t["pnl"] or 0) / t["risk_ref"] for t in closed
              if t.get("risk_ref")]
        er = {}
        for t in closed:
            er[t["exit_reason"] or "(none)"] = er.get(t["exit_reason"] or "(none)", 0) + 1
        per_symbol[sym] = {
            "closed": len(closed), "open": sum(t["status"] == "OPEN" for t in core),
            "wins": wins, "losses": losses, "flat": flat,
            "win_rate": round(wins / len(closed), 3) if closed else None,
            "net_points": net,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "exit_reasons": er,
            "decisions": _hist((d[1], d[2]) for d in dec if d[0] == sym),
            "regimes": _hist((r[1], r[2]) for r in reg if r[0] == sym),
            "entry_blocks": _hist((b[1].split("]")[0].replace("BLOCKED[", ""), b[2])
                                  for b in blk if b[0] == sym),
            "trades": [{k: t[k] for k in ("op", "cl", "option_type", "strike", "entry",
                                          "exit_price", "result", "pnl", "exit_reason",
                                          "held_s", "mfe", "mae")} for t in core],
        }

        tot["trades"] += len(closed); tot["wins"] += wins
        tot["losses"] += losses; tot["flat"] += flat
        tot["net_points"] = round(tot["net_points"] + net, 2)

        for t in trades:
            if t["underlying"] == sym and t["strategy"] == "AUTOSCALP-ZTH":
                zth.append({"symbol": sym, **{k: t[k] for k in (
                    "op", "cl", "option_type", "strike", "entry", "exit_price",
                    "result", "pnl", "exit_reason", "held_s")}})

    return {
        "day_ist": day, "generated": datetime.now(timezone.utc).isoformat(),
        "totals": tot, "per_symbol": per_symbol, "zero_to_hero": zth,
        "note": "PAPER — no live orders. avg_r is per-trade pnl / risk_ref.",
    }


def self_check(runner) -> dict:
    """One-glance operational readiness for the autonomous engine."""
    st = runner.status()
    feed = st.get("feed") or {}
    age = feed.get("last_msg_age_sec")
    aggs = getattr(runner, "_aggs", {}) or {}
    bars_ready = {}
    for s, a in aggs.items():
        try:
            n5 = len((a.snapshot(now_epoch=runner._now()).get("5m") or []))
        except Exception:
            n5 = 0
        bars_ready[s] = {"bars_5m": n5, "ready": n5 >= 20, "last_price": a.last_price}

    # which of THIS engine's exchanges are open right now
    segments, market_open = {}, False
    try:
        from .. import market_calendar
        from .runner import _sym_meta
        syms = list((st.get("config") or {}).get("symbols") or aggs.keys())
        for ex in {_sym_meta(s)["exchange"] for s in syms}:
            segments[ex] = market_calendar.segment_status(ex)
        market_open = any(v == "OPEN" for v in segments.values())
    except Exception:
        pass

    checks = {
        "armed": bool(st.get("armed")),
        "running": bool(st.get("running")),
        "is_leader": bool(st.get("is_leader")),
        "feed_connected": bool(feed.get("connected")),
        "feed_fresh": age is not None and age <= 12,
        "no_last_error": st.get("last_error") in (None, ""),
        "all_aggs_seeded": all(v["ready"] for v in bars_ready.values()) if bars_ready else False,
        "live_trading_disabled": st.get("live_trading") is False,
    }
    # `ok` = "operationally healthy and could trade if armed". `armed` is an
    # operator choice, not a failure, so it never gates `ok`. `feed_connected`
    # / `feed_fresh` are expected to be false when every market this engine
    # trades is closed (the WS feed goes quiet), so they only gate `ok` while a
    # relevant exchange is OPEN. Everything else is always required.
    gating = {k: v for k, v in checks.items() if k != "armed"}
    if not market_open:
        gating.pop("feed_fresh", None)
        gating.pop("feed_connected", None)

    # non-gating config smells worth surfacing on the operator dashboard
    cfg = st.get("config") or {}
    warnings = []
    if not (cfg.get("symbols") or []):
        warnings.append("watchlist is empty")
    if (cfg.get("safeguards") or {}).get("allow_weekend"):
        warnings.append("safeguards.allow_weekend is ON — market-hours suspension disabled")
    if cfg.get("live_trading") or st.get("live_trading") is not False:
        warnings.append("live_trading is not explicitly disabled")

    return {
        "ok": all(gating.values()),
        "armed": bool(st.get("armed")),
        "market_open": market_open,
        "segments": segments,
        "config_warnings": warnings,
        "checks": checks,
        "feed_age_sec": age,
        "bars_ready": bars_ready,
        "open_positions": st.get("open_positions"),
        "calibration": st.get("calibration"),
        "entry_blocks": st.get("entry_blocks"),
        "safeguards": st.get("safeguards"),
        "last_tick_ts": st.get("last_tick_ts"),
        "generated": datetime.now(timezone.utc).isoformat(),
    }
