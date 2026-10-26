"""Causal replay of the 15m-trend + 5m-entry sub-engine on the HISTORICAL index bars (NIFTY 1m). The contribution/breadth/sector components are NOT replayed
(official weights are unavailable, and historical constituent weights would be needed anyway). At each 5m close the adapter only exposes bars closed by then.
Out-of-sample: sessions are split chronologically; no parameter is tuned on either half, so the split just reports stability."""
from __future__ import annotations

from . import strategy as ST, indicators as I
from .historical import HistoricalAdapter
from .paper import PaperBook, PaperConfig


def replay(sessions: list[str], *, scfg=None, pcfg=None, path=None, symbol="NIFTY", start_hhmm=(9, 30), end_hhmm=(15, 0)) -> dict:
    """sessions: ['YYYY-MM-DD', ...] ascending. Returns trades + per-session and summary stats. Signal at bar close t_end, filled at the NEXT 5m bar open."""
    from datetime import datetime
    scfg = scfg or ST.StrategyConfig(); book = PaperBook(pcfg or PaperConfig(), "HISTORICAL", persist=False); dd = ST.Deduper(scfg.cooldown_sec)
    pend = None; per_day = {}
    for day in sessions:
        base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=I.IST); t0 = base.replace(hour=start_hhmm[0], minute=start_hhmm[1]).timestamp(); t1 = base.replace(hour=end_hhmm[0], minute=end_hhmm[1]).timestamp()
        t = t0
        while t <= t1:
            ad = HistoricalAdapter(t, path); c5 = ad.candles(symbol, "5m", 80)
            if len(c5) < 2 or c5[-1]["t"] + 300 > t + 1e-6 or session_gap(c5, t): t += 300; ad._c.close(); continue
            bar = c5[-1]
            if pend and book.pos is None:                                             # fill at the open of the bar that just closed (signal came one bar earlier)
                book.open(pend["side"], bar["o"], pend["stop"], bar["t"], day)
            pend = None
            if book.pos:
                tr = book.on_bar(bar, t)
                if not tr and book.pos:
                    c15 = ad.candles(symbol, "15m", 80); tr15 = ST.trend15(c15, t, scfg)
                    if ST.invalidated(book.pos["side"], book.pos["stop"], bar["c"], tr15.get("state", "")): book.on_bar(bar, t, force_reason="INVALIDATED")
            if book.pos is None:
                c15 = ad.candles(symbol, "15m", 80); tr15 = ST.trend15(c15, t, scfg); e = ST.entry5(c5, tr15, t, scfg)
                if e.get("signal") and dd.allow(e["signal"], e["bar_t"], t): pend = {"side": e["signal"], "stop": e["invalidation"]}
            ad._c.close(); t += 300
        if book.pos: book.on_bar({"c": book.pos["entry_px"], "h": 0, "l": 0}, t1, force_reason="SESSION_END"); pend = None
        per_day[day] = sum(x["net"] for x in book.closed if x["day"] == day) if book.closed else 0.0
    return {"trades": book.closed, "summary": book.summary(), "per_day": per_day}


def session_gap(c5, t) -> bool:
    """True if the newest closed bar is not the one that closes at t (data hole / holiday): then we do not evaluate."""
    return abs((c5[-1]["t"] + 300) - t) > 1e-6


def oos_split(res: dict, frac: float = 0.6) -> dict:
    days = sorted(res["per_day"]); k = int(len(days) * frac); a, b = set(days[:k]), set(days[k:])
    def agg(ds):
        tr = [t for t in res["trades"] if t["day"] in ds]; n = len(tr); net = sum(t["net"] for t in tr)
        return {"sessions": len(ds), "trades": n, "net": round(net, 2), "mean_net": round(net / n, 3) if n else None, "win_rate": round(sum(t["net"] > 0 for t in tr) / n, 3) if n else None}
    return {"in_sample": agg(a), "out_of_sample": agg(b), "verdict_note": "no parameters were tuned on either half; OOS is reported for stability, not as proof of edge"}
