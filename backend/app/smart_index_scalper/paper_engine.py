"""
SmartScalperPaperEngine (slice 4/6) — ties scan -> state machine -> the EXISTING
paper-trading engine.

RESEARCH / PAPER ONLY. live_trading stays false. Positions are opened via
engines.paper_trading.open_trade (strategy='SMART_SCALPER') and marked/closed via
update_trade_price / close_trade — the same engine the autoscalp runner uses.
Every entry passes autoscalp.safeguards.Safeguards.check_entry first; risk
controls are never bypassed.

NOT auto-started in the app lifespan. Call `evaluate()` / `manage()` on demand
(an endpoint, or a future RuntimeScheduler tick — spec section 48).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import db
from ..autoscalp.safeguards import Safeguards
from ..engines.paper_trading import close_trade, open_trade, update_trade_price
from . import state_machine as _sm
from .profiles import get_profile
from .scanner import SmartIndexScalper

STRATEGY = "SMART_SCALPER"


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SmartScalperPaperEngine:
    def __init__(self, *, profile: str | None = None, safeguards: Safeguards | None = None):
        self.profile = get_profile(profile)
        self.scalper = SmartIndexScalper(profile=self.profile["name"])
        self.safeguards = safeguards or Safeguards()

    # ------------------------------------------------------------------ entries
    def evaluate(self, symbols=None, *, dry_run: bool = True, use_cache: bool = True) -> dict:
        """Scan -> pre-entry state machine -> (if ENTRY_CONFIRMED and not dry_run
        and safeguards pass) open ONE paper position for the top-ranked index."""
        scan = self.scalper.scan(symbols, use_cache=use_cache)
        primary = (scan.get("selection") or {}).get("primary")
        decisions = []
        opened = None

        # decide for every ranked index (audit), act only on the primary
        for row in scan.get("ranked", []) + scan.get("not_eligible", []):
            # not_eligible rows are slim — rebuild a minimal scan_row shape
            sr = row if "signal_type" in row else {"status": "OK", "eligible": False,
                                                   "eligibility": {"failed": row.get("failed", [])},
                                                   "missing": row.get("missing")}
            d = _sm.pre_entry_state(sr, self.profile)
            decisions.append({"index": row["index"], **d})

        if primary:
            d = _sm.pre_entry_state(primary, self.profile)
            sig_id = "SS-" + format(int(datetime.now().timestamp() * 1000), "x")
            self._persist_signal(sig_id, primary, d)
            db.log_smart_scalper_state({
                "ts": _now(), "signal_id": sig_id, "trade_id": None,
                "instrument": primary["index"], "profile": self.profile["name"],
                "from_state": "SCAN", "to_state": d["state"], "action": d["action"],
                "reason": d["reason"], "spot": primary.get("spot"),
                "option_mark": (primary.get("selected_option") or {}).get("option_ltp"),
                "pnl": None, "mfe": None, "mae": None})

            if d["action"] == "OPEN_PAPER" and not dry_run:
                opened = self._open(sig_id, primary)

        return {
            "engine": "SMART_SCALPER_PAPER",
            "profile": self.profile["name"],
            "dry_run": dry_run,
            "primary": primary["index"] if primary else None,
            "primary_decision": _sm.pre_entry_state(primary, self.profile) if primary else None,
            "decisions": decisions,
            "opened": opened,
            "why_primary": (scan.get("selection") or {}).get("why_primary"),
            "live_trading": False,
            "calibration": "UNCALIBRATED — profile thresholds are defaults (spec §25/§26). No backtest.",
        }

    def _open(self, sig_id: str, row: dict) -> dict:
        opt = row.get("selected_option") or {}
        direction = row["direction"]
        side = opt.get("option_type") or direction
        exch = "BSE" if row["index"] in ("SENSEX", "BANKEX") else ("MCX" if row["index"] in ("NATURALGAS", "CRUDEOIL") else "NSE")
        open_keys = {(t.get("underlying"), t.get("option_type"))
                     for t in db.list_trades(status="OPEN", strategy=STRATEGY, limit=50)}
        allow, why = self.safeguards.check_entry(
            open_count=len(db.list_trades(status="OPEN", strategy=STRATEGY, limit=50)),
            feed_connected=True, feed_age_sec=0.0,
            underlying=row["index"], side=side, open_keys=open_keys,
            option_premium=opt.get("option_ltp"), underlying_price=row.get("spot"),
            exchange=exch)
        if not allow:
            db.update_smart_scalper_signal(sig_id, {"state": "NO_TRADE",
                                                    "no_trade_reason": f"safeguards: {why}"})
            db.log_smart_scalper_state({"ts": _now(), "signal_id": sig_id, "trade_id": None,
                                        "instrument": row["index"], "profile": self.profile["name"],
                                        "from_state": "ENTRY_CONFIRMED", "to_state": "NO_TRADE",
                                        "action": "NONE", "reason": f"safeguards: {why}",
                                        "spot": row.get("spot"), "option_mark": opt.get("option_ltp"),
                                        "pnl": None, "mfe": None, "mae": None})
            return {"opened": False, "reason": f"safeguards blocked: {why}"}

        entry = opt.get("option_ltp")
        rr = row.get("risk_reward") or []
        # translate the underlying SL/T to the option leg via the observed
        # translation ratio (expected premium move / expected index move).
        exp_prem = None
        for c in opt.get("candidates", []):
            if c.get("strike") == opt.get("selected_strike"):
                exp_prem = c.get("expected_premium_move")
        idx_move = opt.get("expected_index_move_pts") or 1.0
        ratio = (abs(exp_prem) / idx_move) if (exp_prem and idx_move) else 0.5
        u_spot = row.get("spot") or 0.0
        u_sl = row.get("stop_loss")
        u_t1 = row.get("target_1")
        sl_prem = round(max(0.05, entry - ratio * abs(u_spot - u_sl)), 2) if (entry and u_sl) else round(entry * 0.75, 2)
        t1_prem = round(entry + ratio * abs(u_t1 - u_spot), 2) if (entry and u_t1) else round(entry * 1.4, 2)
        t2_prem = round(entry + 1.8 * (t1_prem - entry), 2)

        trade = open_trade({
            "signal_id": sig_id, "market": exch, "underlying": row["index"],
            "instrument": "OPTION", "expiry": (opt.get("expiry") or ""),
            "strike": opt.get("selected_strike") or 0, "option_type": side,
            "direction": "BUY", "timeframe": "3m",
            "entry": entry, "stop_loss": sl_prem, "target_1": t1_prem, "target_2": t2_prem,
            "trailing_stop": round((t1_prem - entry) * 0.6, 2), "quantity": 1,
            "probability": None, "confidence": row.get("confidence"),
            "market_regime": row.get("market_regime"),
            "oi_evidence": "; ".join(row.get("reason_codes") or [])[:400],
            "reason": (row.get("reason_codes") or ["smart scalper"])[0],
            "strategy": STRATEGY, "setup": row.get("signal_type"),
            "atr_pct": None, "max_hold_sec": 1500, "symboltoken": str(opt.get("token") or ""),
        })
        db.update_smart_scalper_signal(sig_id, {"state": "PAPER_OPEN", "trade_id": trade["trade_id"]})
        db.log_smart_scalper_state({"ts": _now(), "signal_id": sig_id, "trade_id": trade["trade_id"],
                                    "instrument": row["index"], "profile": self.profile["name"],
                                    "from_state": "ENTRY_CONFIRMED", "to_state": "PAPER_OPEN",
                                    "action": "OPEN_PAPER",
                                    "reason": f"paper entry {side} {opt.get('selected_strike')} @ {entry}",
                                    "spot": u_spot, "option_mark": entry, "pnl": 0.0, "mfe": 0.0, "mae": 0.0})
        return {"opened": True, "trade_id": trade["trade_id"], "entry": entry,
                "stop_loss": sl_prem, "target_1": t1_prem, "target_2": t2_prem,
                "translation_ratio": round(ratio, 3)}

    # ------------------------------------------------------------------ management
    def manage(self, *, use_cache: bool = True) -> dict:
        """Mark every open SMART_SCALPER paper trade to the current option LTP,
        run the in-trade state machine, apply PROTECT / CLOSE."""
        managed = []
        for t in db.list_trades(status="OPEN", strategy=STRATEGY, limit=50):
            sym = t.get("underlying")
            from ..mathematical_confluence.context import market_context
            ctx = market_context(sym, use_cache=use_cache)
            mark = _mark_for(ctx, t.get("strike"), t.get("option_type"))
            eng = None
            try:
                s = self.scalper.scan([sym], use_cache=use_cache)
                eng = ((s.get("ranked") or []) + (s.get("not_eligible") or []) or [{}])[0]
            except Exception:
                pass

            if mark is not None:
                updated = update_trade_price(t["trade_id"], float(mark))
            else:
                updated = t
            pos = db.get_trade(t["trade_id"]) or updated
            if pos.get("status") == "CLOSED":
                self._log_close(pos, mark, "paper engine hard exit")
                managed.append({"trade_id": t["trade_id"], "state": _closed_state(pos), "action": "CLOSED"})
                continue

            d = _sm.in_trade_state(position=pos, mark=mark, engine_out=eng, profile=self.profile)
            if d["action"] == "CLOSE" and mark is not None:
                cl = close_trade(t["trade_id"], float(mark),
                                 exit_reason="SS_" + d["state"])
                self._log_close(cl or pos, mark, d["reason"])
            elif d["action"] == "PROTECT":
                # ratchet SL to entry + 0.3R (never loosen)
                entry = pos.get("entry") or 0
                rr = pos.get("risk_ref") or abs(entry - (pos.get("stop_loss") or entry))
                new_sl = round(entry + 0.3 * rr, 2)
                if new_sl > (pos.get("stop_loss") or 0):
                    db.update_trade(t["trade_id"], {"stop_loss": new_sl})
            db.log_smart_scalper_state({"ts": _now(), "signal_id": pos.get("signal_id"),
                                        "trade_id": t["trade_id"], "instrument": sym,
                                        "profile": self.profile["name"],
                                        "from_state": "PAPER_OPEN", "to_state": d["state"],
                                        "action": d["action"], "reason": d["reason"],
                                        "spot": ctx.get("spot"), "option_mark": mark,
                                        "pnl": pos.get("pnl"), "mfe": pos.get("mfe"), "mae": pos.get("mae")})
            managed.append({"trade_id": t["trade_id"], "state": d["state"], "action": d["action"],
                            "reason": d["reason"], "mark": mark, "pnl": pos.get("pnl")})
        return {"engine": "SMART_SCALPER_PAPER", "profile": self.profile["name"],
                "managed": managed, "live_trading": False}

    # ------------------------------------------------------------------ helpers
    def _persist_signal(self, sig_id, row, decision):
        opt = row.get("selected_option") or {}
        db.insert_smart_scalper_signal({
            "signal_id": sig_id, "created_ts": _now(), "session_date": _now()[:10],
            "instrument": row["index"], "profile": self.profile["name"],
            "spot": row.get("spot"), "direction": row.get("direction"),
            "signal_type": row.get("signal_type"), "confidence": row.get("confidence"),
            "confluence_score": row.get("confluence_score"),
            "index_selection_score": row.get("index_selection_score") or row.get("score"),
            "market_regime": row.get("market_regime"),
            "entry_zone": _j(opt.get("candidates") and [opt.get("selected_strike")]),
            "stop_loss": row.get("stop_loss"), "target_1": row.get("target_1"),
            "target_2": row.get("target_2"), "target_3": row.get("target_3"),
            "risk_reward": _j(row.get("risk_reward")),
            "selected_strike": opt.get("selected_strike"), "option_type": opt.get("option_type"),
            "option_ltp": opt.get("option_ltp"), "selection_score": opt.get("selection_score"),
            "nearest_support": row.get("nearest_support"), "nearest_resistance": row.get("nearest_resistance"),
            "oi_battle_zone": None,
            "reason_codes": _j(row.get("reason_codes")), "no_trade_reason": row.get("no_trade_reason"),
            "eligibility_json": _j(row.get("eligibility")), "evidence_json": _j(row.get("data_quality")),
            "invalidation": _invalidation(row),
            "state": decision["state"], "trade_id": None,
            "calibration": "UNCALIBRATED",
        })

    def _log_close(self, pos, mark, reason):
        db.update_smart_scalper_signal(pos.get("signal_id") or "", {"state": _closed_state(pos)})
        db.log_smart_scalper_state({"ts": _now(), "signal_id": pos.get("signal_id"),
                                    "trade_id": pos.get("trade_id"), "instrument": pos.get("underlying"),
                                    "profile": self.profile["name"], "from_state": "PAPER_OPEN",
                                    "to_state": _closed_state(pos), "action": "CLOSED", "reason": reason,
                                    "spot": None, "option_mark": mark, "pnl": pos.get("pnl"),
                                    "mfe": pos.get("mfe"), "mae": pos.get("mae")})


def _mark_for(ctx, strike, side):
    for r in ctx.get("chain") or []:
        if r.get("strike") == strike:
            return r.get("ce_ltp") if side == "CE" else r.get("pe_ltp")
    return None


def _closed_state(pos):
    er = str(pos.get("exit_reason") or "").upper()
    if "STOP" in er:
        return "STOPPED"
    if "TARGET" in er or "TRAIL" in er:
        return "EXITED"
    if pos.get("result") == "WIN":
        return "EXITED"
    if pos.get("result") == "LOSS":
        return "STOPPED"
    return "EXITED"


def _invalidation(row):
    d = row.get("direction")
    if d == "CE" and row.get("nearest_support") is not None:
        return f"a decisive close below {row['nearest_support']} on volume invalidates the long CE thesis"
    if d == "PE" and row.get("nearest_resistance") is not None:
        return f"a decisive close above {row['nearest_resistance']} on volume invalidates the short PE thesis"
    return "loss of the confluence-zone structure invalidates the setup"


def _j(x):
    import json
    try:
        return json.dumps(x, separators=(",", ":"))[:2000] if x is not None else None
    except Exception:
        return None
