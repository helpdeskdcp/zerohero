"""
P6 backtest runner — chronological, out-of-sample.

  TRAIN slice  -> replay with the prior (uncalibrated) -> collect
                  (signal_score, regime, signal_type, WIN/LOSS) + win/loss
                  magnitude stats
  fit calibration on TRAIN only
  TEST slice   -> replay with the FROZEN calibration -> spec-18 metrics +
                  calibration reliability curve (all out-of-sample)

No look-ahead: the harness feeds only closed past bars; calibration fitted on
TRAIN is frozen before TEST is touched. Per-leg option candles are resampled
lazily and cut to <= the decision timestamp.
"""
from __future__ import annotations

import json
from collections import defaultdict
from statistics import mean

from . import calibration
from . import oi_history_adapter as ad
from .replay import ReplayHarness, ReplayContext
from ..engines.scalp_strategy import decide_from_context

_LEG_TFS = ("1m", "3m", "5m", "15m", "30m")


class _LegCache:
    """Lazy 5m option-candle cache, cut to <= the decision ts (no look-ahead)."""

    def __init__(self, symbol, start, end):
        self.symbol, self.start, self.end = symbol, start, end
        self._c: dict = {}

    def fn_at(self, ts):
        def fn(strike, ot):
            key = (int(round(strike)), str(ot).upper())
            if key not in self._c:
                try:
                    self._c[key] = ad.resample_candles(
                        self.symbol, "5m", kind="option", strike=key[0], option_type=key[1],
                        start=self.start, end=self.end).get("candles") or []
                except Exception:
                    self._c[key] = []
            cut = [b for b in self._c[key] if b["t"] <= ts[:16]]
            return {"5m": cut, "3m": cut} if len(cut) >= 20 else None
        return fn


def _build_decide(calib, avg_win, avg_loss, cfg, leg_cache):
    def decide(state, ctx: ReplayContext):
        bt = {tf: ctx.candles(tf) for tf in _LEG_TFS}
        if len(bt.get("5m") or []) < 20:
            return None
        return decide_from_context(
            bt, state.get("chain"), atm=state.get("atm"), calib=calib,
            avg_win=avg_win, avg_loss=avg_loss,
            leg_bars_fn=leg_cache.fn_at(state["ts"]), config=cfg)
    return decide


def _drawdown(points_seq):
    peak = cum = 0.0
    mdd = 0.0
    for p in points_seq:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return round(mdd, 2)


def _max_consec_losses(trades):
    run = best = 0
    for t in trades:
        if t.outcome == "LOSS":
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _group_stats(rows):
    """rows: [(key, points, is_win)] -> {key: {n, win_rate, net_points, avg}}"""
    g = defaultdict(list)
    for k, pts, win in rows:
        g[k].append((pts, win))
    out = {}
    for k, vs in g.items():
        n = len(vs)
        w = sum(1 for _, win in vs if win)
        net = sum(p for p, _ in vs)
        out[k] = {"n": n, "win_rate": round(w / n, 3), "net_points": round(net, 2),
                  "avg_points": round(net / n, 2)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))


def _metrics(res, calib, label):
    trades = [t for t in res.trades if t.status == "CLOSED"]
    by_id = {s["signal_id"]: s for s in res.signals}
    wins = [t for t in trades if t.outcome == "WIN"]
    losses = [t for t in trades if t.outcome == "LOSS"]
    gw = sum(t.points for t in wins)
    gl = -sum(t.points for t in losses)
    pts_seq = [t.points for t in sorted(trades, key=lambda t: t.entry_ts)]

    reg_rows, tod_rows, typ_rows, dir_rows, exp_rows = [], [], [], [], []
    rel_pairs = []
    fb_total = fb_loss = 0
    for t in trades:
        row = by_id.get(t.signal_id, {})
        win = t.outcome == "WIN"
        reg_rows.append((row.get("regime") or "?", t.points, win))
        tod_rows.append((row.get("tod_bucket") or "?", t.points, win))
        typ_rows.append((row.get("signal_type") or "?", t.points, win))
        dir_rows.append((row.get("direction") or "?", t.points, win))
        exp_rows.append(((row.get("opt_expiry") or "?"), t.points, win))
        if row.get("probability") is not None:
            rel_pairs.append((row["probability"], win))
        try:
            cs = json.loads(row.get("component_scores") or "{}")
        except Exception:
            cs = {}
        if cs.get("false_risk", 1.0) < 1.0:      # had >=1 false-breakout flag
            fb_total += 1
            fb_loss += (not win)

    n = len(trades)
    return {
        "label": label,
        "decisions": res.decisions, "entries": res.entries,
        "buy_ce": sum(1 for s in res.signals if s.get("decision") == "BUY_CE"),
        "buy_pe": sum(1 for s in res.signals if s.get("decision") == "BUY_PE"),
        "watch": sum(1 for s in res.signals if s.get("decision") == "WATCH"),
        "closed": n,
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 3) if n else None,
        "loss_rate": round(len(losses) / n, 3) if n else None,
        "net_points": round(sum(t.points for t in trades), 2),
        "expectancy_points": round(sum(t.points for t in trades) / n, 3) if n else None,
        "profit_factor": round(gw / gl, 3) if gl else None,
        "avg_win": round(gw / len(wins), 2) if wins else None,
        "avg_loss": round(-gl / len(losses), 2) if losses else None,
        "avg_r": round(mean(t.r_multiple for t in trades), 3) if n else None,
        "max_drawdown_points": _drawdown(pts_seq),
        "max_consecutive_losses": _max_consec_losses(sorted(trades, key=lambda t: t.entry_ts)),
        "exit_reasons": res.counts,
        "false_breakout_trades": fb_total,
        "false_breakout_loss_rate": round(fb_loss / fb_total, 3) if fb_total else None,
        "by_regime": _group_stats(reg_rows),
        "by_time_of_day": _group_stats(tod_rows),
        "by_signal_type": _group_stats(typ_rows),
        "by_direction": _group_stats(dir_rows),
        "by_expiry": _group_stats(exp_rows),
        "calibration_reliability": calibration.reliability_curve(rel_pairs),
    }


def run_backtest(symbol: str, *, train: tuple, test: tuple,
                 config: dict | None = None, persist: bool = False) -> dict:
    """train / test = (start_date, end_date) inclusive, disjoint & chronological."""
    cfg = config or {}
    sym = symbol.upper()

    # ---- TRAIN: prior replay -> calibration samples + magnitude stats ----
    tr_cache = _LegCache(sym, train[0], train[1])
    res_tr = ReplayHarness(sym, train[0], train[1], source="BACKTEST",
                           persist=persist, max_concurrent=cfg.get("max_concurrent", 1)
                           ).run(_build_decide(None, None, None, cfg, tr_cache))
    tr_closed = [t for t in res_tr.trades if t.status == "CLOSED"]
    by_id_tr = {s["signal_id"]: s for s in res_tr.signals}
    samples = []
    for t in tr_closed:
        row = by_id_tr.get(t.signal_id, {})
        if row.get("signal_score") is None:
            continue
        samples.append({"score": row["signal_score"], "regime": row.get("regime"),
                        "signal_type": row.get("signal_type"), "win": t.outcome == "WIN"})
    calib = calibration.fit(samples, version=f"bt-{sym}-{train[0]}_{train[1]}")
    wins = [t.points for t in tr_closed if t.outcome == "WIN"]
    losses = [-t.points for t in tr_closed if t.outcome == "LOSS"]
    avg_win = round(mean(wins), 3) if wins else None
    avg_loss = round(mean(losses), 3) if losses else None

    # ---- TEST: frozen calibration, out-of-sample ----
    te_cache = _LegCache(sym, test[0], test[1])
    res_te = ReplayHarness(sym, test[0], test[1], source="BACKTEST",
                           persist=persist, max_concurrent=cfg.get("max_concurrent", 1)
                           ).run(_build_decide(calib, avg_win, avg_loss, cfg, te_cache))

    return {
        "symbol": sym,
        "manifest": res_te.manifest,
        "train": {"range": list(train), "closed_trades": len(tr_closed),
                  "calibration_samples": len(samples), "avg_win": avg_win,
                  "avg_loss": avg_loss, "calibration": calib,
                  "in_sample": _metrics(res_tr, None, "TRAIN (in-sample, uncalibrated)")},
        "test": {"range": list(test),
                 "out_of_sample": _metrics(res_te, calib, "TEST (out-of-sample, calibrated)")},
        "disclaimer": "Backtest on synthetic-from-tick candles + degraded-greeks archive. "
                      "NOT a profitability claim. Requires live forward validation.",
    }
