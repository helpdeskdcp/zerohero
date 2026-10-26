"""Paper-contest consumer of the Winners Scalper (Pine) NG signals. Lives on the contest side on purpose: the signal generator (app/winners_scalper) has no contest import (information barrier); this module READS its `signals`
table, turns each NEW signal into a long ATM option paper signal for the participant ZEROHERO_PINE_AI (BUY -> ATM CALL, SELL -> ATM PUT, never a short), with the entry at the current ask and stop/targets from the ZeroHero risk
module (1R/2R/3R), and writes the outcome (SUBMITTED / SKIPPED + exact reason + the full contract details) back to that table. PAPER ONLY: no broker or order import.
Kill switch: WINNERS_PINE_SUBMIT=0 (record only). Every skip reason is explicit; a row is CLAIMED (PENDING) before anything is written to the ledger, so a crash can never cause a second submission."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import contracts as CT, submit as SU
from ..winners_scalper import live as WL

_log = logging.getLogger("arena.pine_submitter")
ROOT = WL.ROOT
IST = WL.IST
PARTICIPANT = "ZEROHERO_PINE_AI"
MAX_QUOTE_AGE_S = 180
MAX_SIGNAL_AGE_S = WL.MAX_SIGNAL_AGE_S


def _epoch(ts) -> float:
    """Quote timestamps come back as ISO strings ('2026-10-23T18:04:59.939004Z') or epoch numbers."""
    if isinstance(ts, (int, float)): return float(ts)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def _tg(text: str):
    if os.environ.get("WINNERS_PINE_TG", "1") in ("0", "false", "no"): return
    try:
        from ..connectors import telegram
        telegram._send(text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception as e: _log.debug("telegram failed (cosmetic): %r", e)


# ---------------------------------------------------------------- authoritative expiry list (instrument master, refreshed 07:00 by cron)
_LISTED: dict = {}
MASTER = os.path.join(ROOT, "data", "instrument_master.json")


def listed_option_expiries(sym: str, path: str | None = None) -> set[str] | None:
    """ISO expiries of the exchange-listed OPTFUT contracts of `sym` from the broker instrument master; None when the master cannot be read (the caller then skips the signal)."""
    p = path or MASTER; key = (sym, p, int(os.path.getmtime(p)) if os.path.exists(p) else 0)
    if key in _LISTED: return _LISTED[key]
    try:
        m = json.load(open(p, encoding="utf-8")); rows = m if isinstance(m, list) else m.get("data", [])
        out = {WL._norm_expiry(r["expiry"]) for r in rows if str(r.get("name", "")).upper() == sym and r.get("exch_seg") == "MCX" and r.get("instrumenttype") == "OPTFUT" and r.get("expiry")}
    except Exception as e:
        _log.warning("instrument master unreadable: %r", e); return None
    _LISTED.clear(); _LISTED[key] = out or None; return _LISTED[key]


# ---------------------------------------------------------------- arena submission
def _contest():
    try: return json.load(open(os.path.join(ROOT, "data", "arena", "contest.json")))
    except Exception: return {}


def parity_atm(sym: str, expiry: str, ot: str, now: float, path: str | None = None):
    """The option-implied ATM strike of ONE expiry: among the captured strikes that have a fresh CE and PE quote, the one where call and put prices are closest (put-call parity: C - P = F - K, so |C - P| is smallest at K ~ F).
    Needed because the arena's `chain.atm` uses the FRONT future's price, which is wrong for an option expiry whose underlying is a later future (NG Nov options: front future 240, Nov-future forward 285 -> the 'ATM' 240 was a deep-ITM
    call at Rs 49 and an Rs 3.85 put). -> (strike, quote of `ot` at that strike, implied forward) or (None, reason, None)."""
    ks = (CT.captured_fresh(sym).get("expiries", {}).get(expiry) or {}).get("strikes") or []
    best = None
    for k in ks:
        qc, qp = CT.latest_quote(sym, expiry, float(k), "CE", path), CT.latest_quote(sym, expiry, float(k), "PE", path)
        if not qc or not qp or not qc.get("ltp") or not qp.get("ltp"): continue
        if now - qc["t"] > MAX_QUOTE_AGE_S or now - qp["t"] > MAX_QUOTE_AGE_S: continue
        d = abs(float(qc["ltp"]) - float(qp["ltp"]))
        if best is None or d < best[0]: best = (d, float(k), qc, qp)
    if best is None: return None, f"no strike of {expiry} has a fresh CE and PE quote (max {MAX_QUOTE_AGE_S}s)", None
    d, k, qc, qp = best; tot = float(qc["ltp"]) + float(qp["ltp"])
    if d > 0.25 * tot: return None, f"captured strikes do not bracket the forward of {expiry} (best strike {k:g}: call {qc['ltp']}, put {qp['ltp']})", None
    q = dict(qc if ot == "CE" else qp); q["ts"] = datetime.fromtimestamp(q["t"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return k, q, k + float(qc["ltp"]) - float(qp["ltp"])


def submit_to_arena(sym: str, side: str, strong: bool, bar_ts, fut_close: float, stop: float, now: float | None = None, path: str | None = None) -> dict:
    """-> {"status": SUBMITTED|SKIPPED, "detail": ..., "arena_id": ..., plus ledger fields}. Never raises. BUY -> long ATM CALL, SELL -> long ATM PUT (never a short)."""
    try:
        from .. import market_calendar as mc, sl_detector
        now = now or time.time()
        if os.environ.get("WINNERS_PINE_SUBMIT", "1") in ("0", "false", "no"): return dict(status="SKIPPED", detail="WINNERS_PINE_SUBMIT=0")
        if not mc.is_trading("MCX"): return dict(status="SKIPPED", detail="MCX not trading")
        ct = _contest(); today = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
        if ct.get("end") and today > ct["end"]: return dict(status="SKIPPED", detail="arena ended")
        practice = bool(ct.get("start") and today < ct["start"])
        ot = "CE" if side == "BUY" else "PE"; cap = CT.captured_fresh(sym)
        if cap.get("day") != today: return dict(status="SKIPPED", detail=f"STALE_CAPTURE_DAY: option capture is for {cap.get('day')}, today is {today}")
        exps = sorted(e for e in cap["expiries"])
        if not exps: return dict(status="SKIPPED", detail="no captured option expiries")
        live = [e for e in exps if e >= today]
        if not live: return dict(status="SKIPPED", detail=f"EXPIRED_CONTRACT: every captured expiry {exps} is before {today}")
        expiry = ([e for e in live if e > today] or live)[0]
        listed = listed_option_expiries(sym)
        if not listed: return dict(status="SKIPPED", detail="EXPIRY_UNVERIFIED: the broker instrument master could not be read, so the option expiry cannot be confirmed")
        if expiry not in listed: return dict(status="SKIPPED", detail=f"EXPIRY_NOT_LISTED: captured expiry {expiry} is not an exchange-listed {sym} option expiry (listed: {sorted(listed)[:4]})")
        atm, q, fwd = parity_atm(sym, expiry, ot, now)
        if atm is None: return dict(status="SKIPPED", detail=f"NO_PARITY_ATM: {q}")
        if not q.get("ask"): return dict(status="SKIPPED", detail=f"NO_ASK: {expiry} {atm:g} {ot} has a last price but no ask")
        qage = now - _epoch(q["ts"])
        if qage > MAX_QUOTE_AGE_S: return dict(status="SKIPPED", detail=f"STALE_QUOTE: {expiry} {atm:g} {ot} quote is {qage:.0f}s old (max {MAX_QUOTE_AGE_S}s)")
        if float(q["ask"]) <= 0 or (q.get("bid") and float(q["bid"]) > float(q["ask"])): return dict(status="SKIPPED", detail=f"INVALID_QUOTE: bid {q.get('bid')} ask {q.get('ask')}")
        entry = round(round(float(q["ask"]) / 0.05 + 1e-9) * 0.05, 2); plan = sl_detector.plan_for(sym, expiry, float(atm), ot, entry, now)
        if entry < 1.0 or not (0 < plan["sl"] < entry) or not all(t > entry for t in plan["targets"]):
            return dict(status="SKIPPED", detail=f"degenerate option plan (entry {entry}, sl {plan['sl']}, targets {plan['targets']}): premium too low to define a stop")
        body = dict(participant=PARTICIPANT, underlying=sym, expiry=expiry, strike=float(atm), option_type=ot, entry_mode="NEAR", entry=entry, sl=plan["sl"], targets=plan["targets"], valid_sec=900, lots=1,
                    note=f"Winners Scalper (Pine) {side}{' STRONG' if strong else ''} on {sym} fut 15m bar {bar_ts}: fut close {fut_close:.2f}, Pine stop {stop:.2f}. Stop/targets: ZeroHero risk module 1R/2R/3R.")
        chk = CT.check(sym, expiry, float(atm), ot, entry, "NEAR", sl=plan["sl"])
        if chk.get("problems"): return dict(status="SKIPPED", detail="unscorable: " + "; ".join(chk["problems"])[:300])
        rec = SU.submit(body, practice=practice)
        try:                                                                                                                  # dense (~3-4 s) quotes for this contract, exactly as the arena API and the Telegram ingest do: without it NG option quotes were 37 min apart -> DATA_GAP (H00093)
            from ..histcap import priority
            priority.request_watch(sym, expiry, float(atm), ot)
        except Exception as e: _log.warning("pine submitter: priority watch not registered (%r)", e)
        return dict(status="SUBMITTED", detail=f"{expiry} {atm:g} {ot} (parity ATM, implied fwd {fwd:.1f}) entry {entry} sl {plan['sl']} targets {plan['targets']}{' (practice)' if practice else ''}", arena_id=rec.get("id"), opt_expiry=expiry, strike=float(atm),
                    opt_type=ot, entry=entry, opt_sl=plan["sl"], opt_targets=json.dumps(plan["targets"]), quote_ts=str(q["ts"]), quote_age_s=round(qage, 1), risk_per_unit=round(entry - plan["sl"], 2), valid_until=now + 900)
    except Exception as e:
        return dict(status="SKIPPED", detail=f"error {e!r}"[:300])




_LEDGER = ("opt_expiry", "strike", "opt_type", "entry", "opt_sl", "opt_targets", "quote_ts", "quote_age_s", "risk_per_unit", "valid_until")


def drain(db_path: str | None = None, submit=submit_to_arena, send=_tg, now: float | None = None) -> list[dict]:
    """Handle every NEW signal once: claim it (NEW -> PENDING), re-check its age, submit, store the outcome, send the Telegram card."""
    out = []; c = WL._conn(db_path)
    try:
        for (rid,) in c.execute("SELECT id FROM signals WHERE status='NEW' ORDER BY id").fetchall():
            if c.execute("UPDATE signals SET status='PENDING', detail='claimed' WHERE id=? AND status='NEW'", (rid,)).rowcount != 1: continue
            c.commit(); sym, bar_ts, side, strong, close, stop = c.execute("SELECT symbol, bar_ts, side, strong, close, stop FROM signals WHERE id=?", (rid,)).fetchone(); t = time.time() if now is None else now
            age = t - (pd.Timestamp(bar_ts) + pd.Timedelta(minutes=WL.BAR_MIN)).timestamp()
            res = dict(status="SKIPPED", detail=f"STALE_SIGNAL: consumed {age / 60:.0f} min after the bar closed (max {MAX_SIGNAL_AGE_S // 60} min)") if age > MAX_SIGNAL_AGE_S else submit(sym, side, bool(strong), pd.Timestamp(bar_ts), float(close), float(stop), t)
            sets = ["status=?", "detail=?", "ledger_id=?"] + [f"{k}=?" for k in _LEDGER if k in res]; vals = [res["status"], res["detail"], res.get("arena_id")] + [res[k] for k in _LEDGER if k in res]
            c.execute(f"UPDATE signals SET {', '.join(sets)} WHERE id=?", (*vals, rid)); c.commit()
            out.append(dict(id=rid, symbol=sym, bar_ts=bar_ts, side=side, **res))
            send(f"WINNERS SCALPER (Pine) {sym} 15m {side}{' STRONG' if strong else ''}\nfut close {float(close):.2f}  Pine stop {float(stop):.2f}\nContest ({PARTICIPANT}): {res['status']} - {res['detail']}\nPAPER - no live order")
    finally: c.close()
    return out


class Runner:
    """1-second loop: hand every NEW Pine signal to the paper contest (milliseconds after the generator recorded it)."""

    def __init__(self, period: float = 1.0): self.period, self._t, self._stop = period, None, threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            try:
                if os.path.exists(WL.DB): drain()
            except Exception as e: _log.warning("pine submitter tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if WL.enabled() and self._t is None: self._t = threading.Thread(target=self._loop, name="pine-submitter", daemon=True); self._t.start()
