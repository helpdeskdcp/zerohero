"""Deterministic event-driven replay of the Pine signals. The Pine script only DRAWS levels, so every execution assumption lives in BacktestParams and is stated in the report.
Rules (all conservative): a signal is known at its bar's close; NEXT_OPEN fills at the next bar's open (SIGNAL_CLOSE = Pine's own entry:=close is available only as a labelled optimistic variant);
stops are stop orders (a gap through fills at the open, i.e. worse), targets are limit orders filled AT the target (a gap beyond is not credited); if a candle touches the stop and any target the STOP is assumed first;
intraday positions are flat at the last bar of the session day; one position at a time unless position_policy=REPLACE. No look-ahead: nothing executes at a price that was only known after the signal."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import BacktestParams, SignalParams


def _legs_plan(model: str, sp: SignalParams) -> list[tuple[str, float]]:
    return {"THIRDS": [("T1", 1 / 3), ("T2", 1 / 3), ("T3", 1 / 3)], "T1": [("T1", 1.0)], "T2": [("T2", 1.0)], "T3": [("T3", 1.0)]}[model]


def run_backtest(bars: pd.DataFrame, sig: pd.DataFrame, bp: BacktestParams, sp: SignalParams) -> dict:
    bp.validate(); n = len(bars)
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    loc = bars.index.tz_convert("Asia/Kolkata"); day = np.asarray(loc.date); last_of_day = np.append(day[1:] != day[:-1], True)
    buy, sell = sig.buy_signal.to_numpy(), sig.sell_signal.to_numpy(); has_lv = sig.new_levels.to_numpy()
    stop_a, t1_a, t2_a, t3_a, atr_a = (sig[k].to_numpy(float) for k in ("stop", "t1", "t2", "t3", "atr"))
    plan = _legs_plan(bp.exit_model, sp); trades, rej = [], []
    pos = None; pending = None; bars_in_pos = 0

    def close_leg(pos, price, frac, reason, i):
        pos["legs"].append((float(price), float(frac), reason, bars.index[i])); pos["open_frac"] -= frac

    def finish(pos, i):
        d = pos["dir"]; gross = sum(f * d * (px - pos["entry"]) for px, f, _, _ in pos["legs"]); net = gross - bp.cost_points_roundtrip
        units = pos["units"]; reasons = "+".join(f"{r}@{px:.2f}x{f:.2f}" for px, f, r, _ in pos["legs"])
        trades.append({"signal_time": bars.index[pos["sig_i"]], "entry_time": bars.index[pos["entry_i"]], "exit_time": bars.index[i], "dir": "LONG" if d == 1 else "SHORT", "entry": pos["entry"], "stop": pos["stop"],
                       "t1": pos["t"]["T1"], "t2": pos["t"]["T2"], "t3": pos["t"]["T3"], "risk_signal": pos["R"], "units": units, "legs": reasons, "gross_points": gross, "net_points": net,
                       "gross_R": gross / pos["R"], "net_R": net / pos["R"], "pnl_rs": net * units, "bars_held": i - pos["entry_i"] + 1, "mfe_points": pos["mfe"], "mae_points": pos["mae"], "exit_reason": pos["legs"][-1][2]})

    for i in range(n):
        if pending is not None and pending["entry_i"] == i:
            pos = pending; pos["entry"] = o[i]; pending = None
            dd = pos["dir"]                                                          # a gap that already violates the stop, or is already past T1, is not a tradable entry: refused and counted
            if (dd == 1 and o[i] <= pos["stop"]) or (dd == -1 and o[i] >= pos["stop"]): rej.append({"signal_time": bars.index[pos["sig_i"]], "dir": "LONG" if dd == 1 else "SHORT", "reason": "entry_gap_through_stop"}); pos = None
            elif (dd == 1 and o[i] >= pos["t"]["T1"]) or (dd == -1 and o[i] <= pos["t"]["T1"]): rej.append({"signal_time": bars.index[pos["sig_i"]], "dir": "LONG" if dd == 1 else "SHORT", "reason": "entry_gap_beyond_T1"}); pos = None
        if pos is not None and pos["entry_i"] <= i:
            d, st = pos["dir"], pos["stop"]; bars_in_pos += 1
            pos["mfe"] = max(pos["mfe"], d * ((h[i] if d == 1 else l[i]) - pos["entry"])); pos["mae"] = min(pos["mae"], d * ((l[i] if d == 1 else h[i]) - pos["entry"]))
            stop_hit = (l[i] <= st) if d == 1 else (h[i] >= st)
            if stop_hit:                                                             # STOP_FIRST: stop wins over any target touched in the same candle
                px = min(st, o[i]) if d == 1 else max(st, o[i]); close_leg(pos, px, pos["open_frac"], "STOP" if px == st else "GAP_STOP", i)
            else:
                for name, frac in plan:
                    if pos["done"].get(name): continue
                    tp = pos["t"][name]
                    if (h[i] >= tp) if d == 1 else (l[i] <= tp): close_leg(pos, tp, frac, name, i); pos["done"][name] = True
            if pos["open_frac"] > 1e-9 and bp.session_exit and last_of_day[i]: close_leg(pos, c[i], pos["open_frac"], "SESSION_END", i)
            if pos["open_frac"] <= 1e-9: finish(pos, i); pos = None
        if buy[i] or sell[i]:
            d = 1 if buy[i] else -1; why = None
            if not has_lv[i] or np.isnan(stop_a[i]): why = "no_levels_atr_warmup"
            elif pos is not None and bp.position_policy == "IGNORE_WHILE_OPEN": why = "position_open"
            elif pending is not None: why = "entry_pending"
            elif bp.entry_mode == "NEXT_OPEN" and (i + 1 >= n or day[i + 1] != day[i]): why = "no_next_bar_in_session"
            if why: rej.append({"signal_time": bars.index[i], "dir": "LONG" if d == 1 else "SHORT", "reason": why}); continue
            if pos is not None and bp.position_policy == "REPLACE":
                close_leg(pos, c[i], pos["open_frac"], "REPLACED", i); finish(pos, i); pos = None
            R = abs(c[i] - stop_a[i]); units = max(1, int(math.floor(bp.capital * bp.risk_pct / 100.0 / R)))
            new = {"dir": d, "stop": stop_a[i], "t": {"T1": t1_a[i], "T2": t2_a[i], "T3": t3_a[i]}, "R": R, "units": units, "sig_i": i, "entry_i": i + 1, "entry": np.nan, "legs": [], "open_frac": 1.0, "done": {}, "mfe": 0.0, "mae": 0.0, "mode": bp.entry_mode}
            if bp.entry_mode == "NEXT_OPEN": pending = new
            else: new["entry"] = c[i]; pos = new                                      # Pine's own entry := close of the signal bar; first evaluated bar is the next one (entry_i = i+1)
    if pos is not None:                                                            # data ended inside a trade
        close_leg(pos, c[-1], pos["open_frac"], "DATA_END", n - 1); finish(pos, n - 1)
    tr = pd.DataFrame(trades); rj = pd.DataFrame(rej)
    return {"trades": tr, "rejections": rj, "equity": equity_curve(tr, bp), "summary": metrics(tr, rj, bp, n, bars_in_pos, int(buy.sum() + sell.sum()))}


def equity_curve(tr: pd.DataFrame, bp: BacktestParams) -> pd.DataFrame:
    if tr.empty: return pd.DataFrame(columns=["time", "equity", "drawdown"])
    t = tr.sort_values("exit_time"); eq = bp.capital + t.pnl_rs.cumsum().to_numpy(); peak = np.maximum.accumulate(np.maximum(eq, bp.capital))
    return pd.DataFrame({"time": t.exit_time.to_numpy(), "equity": eq, "drawdown": eq - peak})


def _streak(flags: np.ndarray, val: bool) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f == val else 0; best = max(best, cur)
    return best


def metrics(tr: pd.DataFrame, rj: pd.DataFrame, bp: BacktestParams, n_bars: int, bars_in_pos: int, n_signals: int, seed: int = 7) -> dict:
    s = {"signals": n_signals, "trades": int(len(tr)), "rejected": int(len(rj)), "rejection_reasons": rj.reason.value_counts().to_dict() if len(rj) else {}, "bars": n_bars}
    if tr.empty:
        s.update(verdict="NO_TRADES"); return s
    net = tr.net_points.to_numpy(); gross = tr.gross_points.to_numpy(); r = tr.net_R.to_numpy(); w = net > 0
    gp, gl = net[net > 0].sum(), -net[net < 0].sum(); eq = equity_curve(tr, bp)
    rng = np.random.default_rng(seed); boot = [r[rng.integers(0, len(r), len(r))].mean() for _ in range(2000)]
    s.update(gross_points=float(gross.sum()), net_points=float(net.sum()), net_pnl_rs=float(tr.pnl_rs.sum()), win_rate=float(w.mean()), profit_factor=float(gp / gl) if gl > 0 else None,
             expectancy_points=float(net.mean()), expectancy_R=float(r.mean()), expectancy_R_ci95=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
             avg_win_points=float(net[w].mean()) if w.any() else None, avg_loss_points=float(net[~w].mean()) if (~w).any() else None,
             max_drawdown_rs=float(-eq.drawdown.min()), max_drawdown_pct=float(-eq.drawdown.min() / bp.capital * 100), max_consecutive_wins=_streak(w, True), max_consecutive_losses=_streak(w, False),
             avg_bars_held=float(tr.bars_held.mean()), exposure=float(bars_in_pos / n_bars) if n_bars else None, long_trades=int((tr.dir == "LONG").sum()), short_trades=int((tr.dir == "SHORT").sum()),
             net_points_long=float(tr.loc[tr.dir == "LONG", "net_points"].sum()), net_points_short=float(tr.loc[tr.dir == "SHORT", "net_points"].sum()),
             verdict="INSUFFICIENT_SAMPLE (n<30)" if len(tr) < 30 else "n>=30: read the interval, not the point estimate")
    return s
