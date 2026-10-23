"""Option-premium replay of the Smart U-Turn signals (spec: data/research/smart_uturn/SPEC_OPTION_REPLAY_2026-10-23.md). Research only, no orders.
BUY -> long ATM CE, SELL -> long ATM PE of the nearest expiry with date AFTER the signal date. Stop/targets are the Pine INDEX levels, evaluated on index 1-minute bars; the option is entered/exited at the
OPEN of real option 1-minute bars (entry: the minute after the signal bar closes; exits: one minute after the trigger minute) with slippage, brokerage and statutory costs. No bid/ask exists in the data:
slippage stands in for the spread. A missing quote makes the whole trade a counted rejection (never a substitute contract or price)."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

IST = "Asia/Kolkata"


@dataclass(frozen=True)
class OptionReplayConfig:
    slippage_pct: float = 1.5            # per side, % of option price
    brokerage_per_order: float = 20.0    # Rs per order (entry + every exit leg)
    sell_charges_pct: float = 0.15       # % of sell value (STT/exchange/GST stand-in)
    lot_size: int = 65                   # NIFTY, app/instrument_profiles (verified)
    max_strike_dist: float = 75.0
    quote_tolerance_min: int = 5
    exit_model: str = "THIRDS"           # THIRDS | T1 | T2 | T3

    def to_dict(self): return asdict(self)


class OptionStore:
    """Interface: expiries() -> sorted ISO dates; strikes(expiry, opt) -> sorted floats; bars(expiry, strike, opt, day) -> DataFrame(open,high,low,close,volume) indexed by tz-aware IST open time."""
    def expiries(self): raise NotImplementedError
    def strikes(self, expiry, opt): raise NotImplementedError
    def bars(self, expiry, strike, opt, day): raise NotImplementedError


class SqliteOptionStore(OptionStore):
    def __init__(self, path: str, table: str = "normalized_option_bars", underlying: str = "NIFTY", timeout: float = 120.0):
        self.c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout); self.t, self.u = table, underlying; self._exp = None; self._st: dict = {}

    def expiries(self):
        if self._exp is None: self._exp = sorted(r[0] for r in self.c.execute(f"select distinct expiry from {self.t} where underlying=?", (self.u,)))
        return self._exp

    def strikes(self, expiry, opt):
        k = (expiry, opt)
        if k not in self._st: self._st[k] = sorted(r[0] for r in self.c.execute(f"select distinct strike from {self.t} where underlying=? and expiry=? and option_type=?", (self.u, expiry, opt)))
        return self._st[k]

    def bars(self, expiry, strike, opt, day):
        d = pd.read_sql(f"select timestamp, open, high, low, close, volume from {self.t} where underlying=? and expiry=? and strike=? and option_type=? and timestamp>=? and timestamp<? order by timestamp",
                        self.c, params=(self.u, expiry, strike, opt, f"{day}T00:00:00", f"{day}T23:59:59"))
        if d.empty: return d
        d.index = pd.DatetimeIndex(pd.to_datetime(d.pop("timestamp"), utc=True)).tz_convert(IST); return d[~d.index.duplicated()]


def _first_bar(bars: pd.DataFrame, t: pd.Timestamp, tol_min: int):
    if bars.empty: return None
    i = bars.index.searchsorted(t)
    if i >= len(bars) or bars.index[i] > t + pd.Timedelta(minutes=tol_min): return None
    return bars.index[i], bars.iloc[i]


def replay_options(index_1m: pd.DataFrame, chart: pd.DataFrame, sig: pd.DataFrame, store: OptionStore, sp, cfg: OptionReplayConfig = OptionReplayConfig()) -> dict:
    s = cfg.slippage_pct / 100.0; plan = {"THIRDS": [("T1", 1 / 3), ("T2", 1 / 3), ("T3", 1 / 3)], "T1": [("T1", 1.0)], "T2": [("T2", 1.0)], "T3": [("T3", 1.0)]}[cfg.exit_model]
    idx_days = {d: g for d, g in index_1m.groupby(index_1m.index.date)}; expiries = store.expiries(); trades, rej = [], []; busy_until = None
    sigs = sig[(sig.buy_signal | sig.sell_signal) & sig.new_levels]
    for t, r in sigs.iterrows():
        i = chart.index.get_loc(t); ct = chart.close_time.iloc[i]; d = 1 if r.buy_signal else -1; opt = "CE" if d == 1 else "PE"
        def rej_(why): rej.append({"signal_time": t, "dir": "LONG" if d == 1 else "SHORT", "reason": why})
        if busy_until is not None and ct < busy_until: rej_("position_open"); continue
        day = t.date(); nxt = [e for e in expiries if e > day.isoformat()]
        if not nxt: rej_("no_later_expiry_in_data"); continue
        exp = nxt[0]; spot = float(chart.close.iloc[i]); ks = store.strikes(exp, opt)
        if not ks: rej_("contract_not_in_data"); continue
        k = min(ks, key=lambda x: abs(x - spot))
        if abs(k - spot) > cfg.max_strike_dist: rej_("contract_not_in_data"); continue
        ig = idx_days.get(day)
        if ig is None: rej_("no_index_bars"); continue
        ig = ig[ig.index >= ct]
        if ig.empty: rej_("no_next_bar_in_session"); continue
        st, t1, t2, t3 = r.stop, r.t1, r.t2, r.t3; o0 = float(ig.open.iloc[0])
        if (d == 1 and o0 <= st) or (d == -1 and o0 >= st): rej_("entry_gap_through_stop"); continue
        if (d == 1 and o0 >= t1) or (d == -1 and o0 <= t1): rej_("entry_gap_beyond_T1"); continue
        ob = store.bars(exp, k, opt, day.isoformat()); fb = _first_bar(ob, ig.index[0], cfg.quote_tolerance_min)
        if fb is None: rej_("no_option_quote"); continue
        entry_ts, entry_row = fb; entry_px = float(entry_row.open) * (1 + s)
        tg = {"T1": t1, "T2": t2, "T3": t3}; done = {}; remaining = 1.0; legs = []; ok = True; last_ts = ig.index[-1]
        def exit_at(trigger_ts, frac, reason):
            q = _first_bar(ob, trigger_ts + pd.Timedelta(minutes=1), cfg.quote_tolerance_min)
            if q is None: return False
            legs.append((reason, frac, float(q[1].open) * (1 - s), q[0])); return True
        for m, b in ig.iterrows():
            hi, lo, op = float(b.high), float(b.low), float(b.open)
            stop_hit = lo <= st if d == 1 else hi >= st
            if stop_hit:                                                           # stop first, also when targets were touched in the same minute
                ok = exit_at(m, remaining, "GAP_STOP" if (op <= st if d == 1 else op >= st) else "STOP"); remaining = 0.0; break
            for name, frac in plan:
                if done.get(name): continue
                if (hi >= tg[name]) if d == 1 else (lo <= tg[name]):
                    if not exit_at(m, frac, name): ok = False
                    done[name] = True; remaining -= frac
            if not ok or remaining <= 1e-9: break
        if ok and remaining > 1e-9:                                               # session end: close of the last option bar of the day
            lb = ob[ob.index <= last_ts]
            if lb.empty: ok = False
            else: legs.append(("SESSION_END", remaining, float(lb.close.iloc[-1]) * (1 - s), lb.index[-1])); remaining = 0.0
        if not ok or not legs: rej_("no_option_quote"); continue
        gross_ps = sum(f * (px - entry_px) for _, f, px, _ in legs)                # per share, slippage already inside the prices
        n_orders = 1 + len(legs); sell_val = sum(f * px for _, f, px, _ in legs) * cfg.lot_size
        net_rs = gross_ps * cfg.lot_size - cfg.brokerage_per_order * n_orders - cfg.sell_charges_pct / 100.0 * sell_val
        exit_ts = max(l[3] for l in legs); busy_until = exit_ts + pd.Timedelta(minutes=1)
        trades.append({"signal_time": t, "dir": "LONG" if d == 1 else "SHORT", "option": f"{exp} {k:g} {opt}", "dte": (pd.Timestamp(exp) - pd.Timestamp(day)).days, "entry_time": entry_ts, "exit_time": exit_ts,
                       "entry_px": entry_px, "legs": "+".join(f"{n}@{px:.2f}x{f:.2f}" for n, f, px, _ in legs), "gross_rs": gross_ps * cfg.lot_size, "net_rs": net_rs, "net_pct_of_premium": net_rs / (entry_px * cfg.lot_size) * 100,
                       "hold_min": (exit_ts - entry_ts).total_seconds() / 60.0, "last_reason": legs[-1][0]})
    tr = pd.DataFrame(trades); rj = pd.DataFrame(rej)
    return {"trades": tr, "rejections": rj, "summary": option_metrics(tr, rj, len(sigs), cfg)}


def option_metrics(tr: pd.DataFrame, rj: pd.DataFrame, n_signals: int, cfg: OptionReplayConfig, seed: int = 7) -> dict:
    s = {"signals": int(n_signals), "trades": int(len(tr)), "rejected": int(len(rj)), "rejection_reasons": rj.reason.value_counts().to_dict() if len(rj) else {}, "config": cfg.to_dict()}
    if tr.empty: s["verdict"] = "NO_TRADES"; return s
    x = tr.net_rs.to_numpy(); w = x > 0; gp, gl = x[w].sum(), -x[~w].sum(); rng = np.random.default_rng(seed); boot = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(2000)]
    eq = np.cumsum(x); dd = float((np.maximum.accumulate(np.maximum(eq, 0)) - eq).max())
    s.update(net_rs_total=float(x.sum()), gross_rs_total=float(tr.gross_rs.sum()), mean_net_rs=float(x.mean()), mean_net_rs_ci95=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))], win_rate=float(w.mean()),
             profit_factor=float(gp / gl) if gl > 0 else None, max_drawdown_rs=dd, mean_net_pct_of_premium=float(tr.net_pct_of_premium.mean()), avg_entry_premium=float(tr.entry_px.mean()), avg_hold_min=float(tr.hold_min.mean()),
             long_calls=int((tr.dir == "LONG").sum()), short_puts=int((tr.dir == "SHORT").sum()), net_rs_calls=float(tr.loc[tr.dir == "LONG", "net_rs"].sum()), net_rs_puts=float(tr.loc[tr.dir == "SHORT", "net_rs"].sum()),
             verdict="INSUFFICIENT_SAMPLE (n<30)" if len(tr) < 30 else "n>=30: read the interval, not the point estimate")
    return s
