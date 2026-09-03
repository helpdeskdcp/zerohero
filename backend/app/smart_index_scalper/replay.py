"""
SmartScalperReplay — strict-causal historical replay / backtest (slice 5/6,
spec sections 26 & 27). REQUIRED before any profitability statement.

For every captured (instrument, session) in market_history.db it walks the
session on a fixed step and, at each timestamp T:

    historical_context.build_context(sym, T)      # only rows visible at T
        -> MathematicalConfluenceEngine.evaluate  # same engine as live
        -> oi_matrix / eligibility / selection score
        -> option_selector.select                 # per profile (ATM band differs)
        -> state_machine.pre_entry_state           # per profile thresholds
    on ENTRY_CONFIRMED: open a SIMULATED position (in-memory only) and mark it
    bar-by-bar against the REAL historical option LTP for the picked strike,
    running state_machine.in_trade_state for the exit.

Anti-look-ahead discipline (reused from expiry_zero_to_hero):
  - context only ever sees candles that have CLOSED and quotes with
    received_ts <= T;
  - the engine's swing detector needs `n` bars each side, so the last bars are
    never treated as confirmed pivots;
  - option fills use the mark at T or later, never an earlier/median price.

NO ORDER PATH. Nothing here writes to ai_paper_trades or the broker — the
simulated fills live only in the returned dict. `live_trading` stays false.
"""
from __future__ import annotations

from datetime import timedelta

from ..mathematical_confluence import MathematicalConfluenceEngine
from ..mathematical_confluence.oi_confluence import oi_matrix as _oi_matrix
from . import eligibility as _elig
from . import historical_context as _hc
from . import option_selector as _optsel
from . import replay_metrics as _rm
from . import replay_price_action as _pa
from . import selection_score as _ss
from . import state_machine as _sm
from .profiles import get_profile
from .universe import resolve_universe

ENGINE_NAME = "SMART_SCALPER_REPLAY"
_ALL_PROFILES = ("CONSERVATIVE", "BALANCED", "AGGRESSIVE")


class SmartScalperReplay:
    def __init__(self, *, engine: MathematicalConfluenceEngine | None = None,
                 filters: dict | None = None, selection_weights: dict | None = None):
        self.engine = engine or MathematicalConfluenceEngine()
        self.filters = filters
        self.selection_weights = selection_weights

    # ------------------------------------------------------------------ sessions
    def available_sessions(self, symbols=None) -> list[dict]:
        return _hc.available_sessions(symbols)

    # ------------------------------------------------------------------ run
    def run(self, symbols=None, *, step_min: int = 3, profiles=None,
            warmup_min: int = 30, max_hold_min: int = 25,
            profile_overrides: dict | None = None,
            min_sessions: int = _rm.MIN_SESSIONS, min_trades: int = _rm.MIN_TRADES) -> dict:
        # `profile_overrides` is a calibration knob (spec section 26: sweep the
        # thresholds). It is NOT applied by default; when set, the result is
        # stamped so a swept run is never mistaken for a stock-profile run.
        profs = [get_profile(p, overrides=profile_overrides) for p in (profiles or _ALL_PROFILES)]
        universe = resolve_universe(symbols)
        sessions = [s for s in _hc.available_sessions(universe)]
        trades: list[dict] = []
        session_keys: set = set()
        errors: list[str] = []

        for s in sessions:
            key = f"{s['symbol']}/{s['session_date']}"
            try:
                sd = _hc.SessionData(s["symbol"], s["session_date"], s["expiry"])
                sp = sd.span()
                if not sp:
                    continue
                st, en = sp[0] + timedelta(minutes=warmup_min), sp[1]
                if en <= st:
                    continue
                session_keys.add(key)
                for prof in profs:
                    trades.extend(self._replay_one(sd, prof, st, en, step_min, max_hold_min))
            except Exception as e:                                # pragma: no cover
                errors.append(f"{key}: {type(e).__name__}: {e}")

        summary = _rm.summarize(trades, session_keys=session_keys,
                                min_sessions=min_sessions, min_trades=min_trades)
        return {
            "engine": ENGINE_NAME,
            "status": summary["status"],
            "params": {"step_min": step_min, "warmup_min": warmup_min,
                       "max_hold_min": max_hold_min, "profiles": [p["name"] for p in profs],
                       "universe": universe,
                       "profile_overrides": profile_overrides or None,
                       "gate_mode": "STOCK_PROFILE" if not profile_overrides
                       else "DIAGNOSTIC_SWEEP (overrides applied — not a stock-profile result)"},
            "coverage": {
                "sessions_available": len(sessions),
                "sessions_replayed": sorted(session_keys),
                "instruments": sorted({s["symbol"] for s in sessions}),
                "date_range": [min((s["session_date"] for s in sessions), default=None),
                               max((s["session_date"] for s in sessions), default=None)],
            },
            "metrics": {k: summary[k] for k in
                        ("overall", "by_profile", "by_instrument", "by_market_regime")},
            "calibration": summary["calibration"],
            "sample": summary["sample"],
            "trades": trades[:500],
            "errors": errors,
            "live_trading": False,
            "data_source": _hc.SOURCE,
            "note": summary["note"],
        }

    # ------------------------------------------------------------------ internals
    def _replay_one(self, sd, profile: dict, start, end,
                    step_min: int, max_hold_min: int) -> list[dict]:
        sym, sdate = sd.symbol, sd.session_date
        out: list[dict] = []
        pos: dict | None = None
        t = start
        step = timedelta(minutes=step_min)
        while t <= end:
            ctx = sd.context_at(t)
            eng = self._evaluate(sym, ctx)
            mark = _mark(ctx, pos["strike"], pos["side"]) if pos else None

            if pos is not None:
                pos["ticks"] += 1
                if mark is not None:
                    pos["mfe"] = max(pos["mfe"], mark - pos["entry"])
                    pos["mae"] = min(pos["mae"], mark - pos["entry"])
                d = _sm.in_trade_state(
                    position={**pos, "risk_ref": pos["risk_ref"]},
                    mark=mark,
                    engine_out={"status": eng.get("status"), "direction": eng.get("direction"),
                                "signal_type": eng.get("signal_type"),
                                "confidence": eng.get("confidence")},
                    profile=profile)
                hold = (t - pos["entry_ts"]).total_seconds() / 60.0
                reason = None
                if mark is not None and mark <= pos["stop_loss"]:
                    reason = "STOP"
                elif mark is not None and pos.get("target_2") and mark >= pos["target_2"]:
                    reason = "TARGET_2"
                elif d["action"] == "CLOSE" and mark is not None:
                    reason = "SM_" + d["state"]
                elif hold >= max_hold_min:
                    reason = "MAX_HOLD"
                if reason and mark is not None:
                    out.append(_close(pos, t, mark, reason))
                    pos = None
                elif d["action"] == "PROTECT" and mark is not None:
                    new_sl = round(pos["entry"] + 0.3 * pos["risk_ref"], 2)
                    pos["stop_loss"] = max(pos["stop_loss"], new_sl)

            if pos is None:
                row = self._scan_row(sym, ctx, eng, profile)
                dec = _sm.pre_entry_state(row, profile)
                if dec["action"] == "OPEN_PAPER":
                    pos = _open(sym, sdate, profile, row, ctx, eng, t)
            t += step

        if pos is not None:
            m = _mark(sd.context_at(end), pos["strike"], pos["side"])
            if m is not None:
                out.append(_close(pos, end, m, "SESSION_END"))
        return out

    def _evaluate(self, sym: str, ctx: dict) -> dict:
        pd = ctx.get("prev_day") or {}
        pa = _pa.derive(ctx.get("bars") or [], pdh=pd.get("high"), pdl=pd.get("low"),
                        day_high=ctx.get("day_high"), day_low=ctx.get("day_low"))
        return self.engine.evaluate(
            instrument=sym, timestamp=ctx.get("as_of", ""),
            prev_day=pd, today_open=ctx.get("today_open"),
            current_price=ctx.get("spot"),
            day_high=ctx.get("day_high"), day_low=ctx.get("day_low"),
            current_volume=ctx.get("current_volume"), avg_volume=ctx.get("avg_volume"),
            bars=ctx.get("bars"), chain=ctx.get("chain"), mom_3m=ctx.get("mom_3m"),
            breakout_state=pa["breakout_state"], retest_state=pa["retest_state"],
            reversal_candidate=pa["reversal_candidate"], candle_signals=pa["candle_signals"])

    def _scan_row(self, sym: str, ctx: dict, eng: dict, profile: dict) -> dict:
        oim = (_oi_matrix(ctx.get("chain") or [], ctx.get("spot"))
               if ctx.get("chain") and ctx.get("spot") else {"status": "DATA_INSUFFICIENT"})
        elig = _elig.evaluate_eligibility(ctx=ctx, engine_out=eng, oi_matrix=oim,
                                          filters=self.filters)
        comp = _ss.component_scores(ctx=ctx, engine_out=eng, oi_matrix=oim,
                                    liquidity_norm=0.5)   # single-instrument replay: neutral
        sel = _ss.index_selection_score(comp, self.selection_weights)

        selected_option = None
        if elig["eligible"] and eng.get("direction") in ("CE", "PE") \
                and eng.get("signal_type") in ("BUY_CE", "BUY_PE"):
            tgt, spot = eng.get("target_1"), ctx.get("spot")
            move = abs(tgt - spot) if (tgt is not None and spot is not None) else None
            from ..autoscalp.runner import _sym_meta
            step = float(_sym_meta(sym).get("strike_step", 50.0))
            selected_option = _optsel.select(
                direction=eng["direction"], spot=spot, chain=ctx.get("chain") or [],
                atm=ctx.get("atm"), strike_step=step, expected_move_pts=move,
                allowed_option_distance=int(profile.get("allowed_option_distance", 2)))

        return {
            "index": sym, "status": eng.get("status"),
            "eligible": elig["eligible"], "eligibility": elig,
            "direction": eng.get("direction"), "signal_type": eng.get("signal_type"),
            "confidence": eng.get("confidence"),
            "index_selection_score": sel["index_selection_score"],
            "confluence_score": eng.get("confluence_score"),
            "risk_reward": eng.get("risk_reward"),
            "reason_codes": eng.get("reason_codes"),
            "no_trade_reason": eng.get("no_trade_reason"),
            "spot": ctx.get("spot"),
            "stop_loss": eng.get("stop_loss"), "target_1": eng.get("target_1"),
            "target_2": eng.get("target_2"),
            "market_regime": eng.get("market_regime"),
            "nearest_support": (eng.get("nearest_support") or {}).get("center"),
            "nearest_resistance": (eng.get("nearest_resistance") or {}).get("center"),
            "selected_option": selected_option,
        }


# --------------------------------------------------------------------- helpers
def _mark(ctx: dict, strike, side) -> float | None:
    for r in ctx.get("chain") or []:
        if r.get("strike") == strike:
            return r.get("ce_ltp") if side == "CE" else r.get("pe_ltp")
    return None


def _translation_ratio(opt: dict) -> float:
    exp_prem = None
    for c in opt.get("candidates", []):
        if c.get("strike") == opt.get("selected_strike"):
            exp_prem = c.get("expected_premium_move")
    idx_move = opt.get("expected_index_move_pts") or 1.0
    return (abs(exp_prem) / idx_move) if (exp_prem and idx_move) else 0.5


def _open(sym, sdate, profile, row, ctx, eng, t) -> dict:
    opt = row["selected_option"]
    entry = opt.get("option_ltp")
    ratio = _translation_ratio(opt)
    spot = ctx.get("spot") or 0.0
    u_sl, u_t1 = eng.get("stop_loss"), eng.get("target_1")
    sl = round(max(0.05, entry - ratio * abs(spot - u_sl)), 2) if (entry and u_sl) else round(entry * 0.75, 2)
    t1 = round(entry + ratio * abs(u_t1 - spot), 2) if (entry and u_t1) else round(entry * 1.4, 2)
    t2 = round(entry + 1.8 * (t1 - entry), 2)
    return {
        "symbol": sym, "session": sdate, "profile": profile["name"],
        "direction": row["direction"], "side": opt.get("option_type"),
        "strike": opt.get("selected_strike"),
        "entry_ts": t, "entry": entry, "stop_loss": sl, "target_1": t1, "target_2": t2,
        "option_type": opt.get("option_type"),
        "risk_ref": max(1e-6, entry - sl),
        "mfe": 0.0, "mae": 0.0, "ticks": 0,
        "confidence": row.get("confidence"),
        "index_selection_score": row.get("index_selection_score"),
        "market_regime": eng.get("market_regime"),
        "translation_ratio": round(ratio, 3),
        "entry_spot": spot,
    }


def _close(pos: dict, t, mark: float, reason: str) -> dict:
    pnl = round(mark - pos["entry"], 3)
    risk = pos["risk_ref"] or 1e-6
    return {
        "symbol": pos["symbol"], "session": pos["session"], "profile": pos["profile"],
        "direction": pos["direction"], "option_type": pos["option_type"], "strike": pos["strike"],
        "entry_ts": _hc._iso(pos["entry_ts"]), "exit_ts": _hc._iso(t),
        "entry": pos["entry"], "exit": round(mark, 3),
        "stop_loss": pos["stop_loss"], "target_1": pos["target_1"], "target_2": pos["target_2"],
        "pnl": pnl, "r_multiple": round(pnl / risk, 3),
        "mfe": round(pos["mfe"], 3), "mae": round(pos["mae"], 3),
        "hold_min": round((t - pos["entry_ts"]).total_seconds() / 60.0, 1),
        "ticks": pos["ticks"],
        "exit_reason": reason,
        "confidence": pos["confidence"],
        "index_selection_score": pos["index_selection_score"],
        "market_regime": pos["market_regime"],
        "translation_ratio": pos["translation_ratio"],
        "sim": True,
    }
