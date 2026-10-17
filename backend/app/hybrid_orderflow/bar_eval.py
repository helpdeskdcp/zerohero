"""Incremental, causal 5m evidence builder + scorer for ONE instrument -- the single code path used by the offline replay AND the live shadow runner.

Feed ticks in time order (`add_tick`); a bar is evaluated only after it is CLOSED (a later bucket's tick arrives, or `flush(now)` after the bucket ended).
Everything is computed from closed bars up to and including the one being evaluated; nothing looks forward. Missing buckets are simply absent
(never forward-filled). Prices may be float32-noisy (Dhan): footprint levels snap to the exchange tick grid."""
from __future__ import annotations

from app.engines.orderflow_engine import FootprintBar
from app.liquidity_sweep.sweep import detect_sweep

from . import scoring as S

BAR = 300
IST = 19800


class InstrumentEvaluator:
    def __init__(self, symbol: str, tick_size: float | None, policy: dict):
        self.sym, self.tick, self.P = symbol, tick_size, policy
        self.L = policy["smc"]["pivot_len"]
        self.min_bars = policy["data"]["min_session_bars_for_features"]
        self.cur_k = None
        self.cur = None
        self.late_ticks = 0
        self.day = None
        self.prior_hl = None
        self.last_sig = None                     # (direction, bucket k)
        self.bars_seen = 0
        self._reset_day()

    # ---------------------------------------------------------------- ticks
    def _reset_day(self):
        self.hist, self.ks, self.cvd = [], [], []
        self.sw_h = self.sw_l = None
        self.sdir = 0
        self.events = []
        self.cum = 0
        self.pv = self.vol = 0.0
        self.ema = None

    def add_tick(self, ltt: int, ltp: float, ltq: int, side: str, meth: str) -> list[dict]:
        k = (ltt + IST) // BAR
        out = []
        if self.cur_k is not None and k < self.cur_k:
            self.late_ticks += 1                 # older than the open bar: never reopen a closed bar
            return out
        if self.cur_k is not None and k > self.cur_k:
            out.append(self._close())
        if self.cur is None:
            self.cur_k = k
            self.cur = {"fp": FootprintBar(k * BAR - IST, BAR, ltp, tick_size=self.tick), "vol": 0, "pv": 0.0, "o": ltp, "h": ltp, "l": ltp, "c": ltp}
        b = self.cur
        q = max(int(ltq), 0)
        b["fp"].add({"ltp": ltp, "ltq": q, "side": side, "classification_method": meth})
        b["vol"] += q
        b["pv"] += ltp * q
        b["h"], b["l"], b["c"] = max(b["h"], ltp), min(b["l"], ltp), ltp
        return out

    def flush(self, now_epoch: float, grace_sec: float = 2.0) -> list[dict]:
        """Close the open bar once its bucket has ended (quiet instruments never receive the 'next' tick)."""
        if self.cur is not None and (self.cur_k + 1) * BAR - IST + grace_sec <= now_epoch:
            return [self._close()]
        return []

    # ---------------------------------------------------------------- bar close
    def _close(self) -> dict:
        k, v = self.cur_k, self.cur
        self.cur, self.cur_k = None, None
        day = k * BAR // 86400
        if self.day is not None and day != self.day:
            if self.hist:
                self.prior_hl = (max(b["h"] for b in self.hist), min(b["l"] for b in self.hist))
            self._reset_day()
        self.day = day
        res = {"sym": self.sym, "k": k, "scored": False, "status": None, "dir": None, "total": 0.0, "reasons": [], "components": [],
               "bar": {"o": v["o"], "h": v["h"], "l": v["l"], "c": v["c"], "vol": v["vol"]}}
        self.bars_seen += 1
        self.hist.append(v)
        self.ks.append(k)
        i = len(self.hist) - 1
        fp = v["fp"]
        self.cum += fp.buy_vol - fp.sell_vol
        self.cvd.append(self.cum)
        self.pv, self.vol = self.pv + v["pv"], self.vol + v["vol"]
        self.ema = v["c"] if self.ema is None else self.ema + (v["c"] - self.ema) * 2 / 21
        self._structure(i, v)
        if i + 1 < self.min_bars:
            return res                                           # warm-up (policy data.min_session_bars_for_features)
        P, hist = self.P, self.hist
        levels = []
        if self.prior_hl:
            levels += [{"price": self.prior_hl[0], "source": "PDH"}, {"price": self.prior_hl[1], "source": "PDL"}]
        if i >= 5:
            levels += [{"price": max(b["h"] for b in hist[:i - 3]), "source": "RANGE_HIGH"}, {"price": min(b["l"] for b in hist[:i - 3]), "source": "RANGE_LOW"}]
        win = [{"h": b["h"], "l": b["l"], "c": b["c"]} for b in hist[-8:]]
        sw = detect_sweep(win, levels, reclaim_window=P["smc"]["sweep"]["reaction_bars"])
        if sw:
            d = sw.to_dict()
            d["reclaim_bar_index"] = i - (len(win) - 1 - d["reclaim_bar_index"])
            sw = d
        tot = fp.buy_vol + fp.sell_vol
        cf = tot / v["vol"] if v["vol"] else None
        dpct = (fp.buy_vol - fp.sell_vol) / tot if tot else None
        slope = self.cvd[-1] - self.cvd[-4] if len(self.cvd) >= 4 else None
        vwap = self.pv / self.vol if self.vol else None
        trend = S.BULL if v["c"] > self.ema else S.BEAR
        vols = [b["vol"] for b in hist[-13:-1]]
        rvol = v["vol"] / (sum(vols) / len(vols)) if len(vols) == 12 and sum(vols) else None
        kk = k // 3 - 1                                          # last COMPLETED 15m bucket (3 x 5m)
        h15 = [b for kx, b in zip(self.ks, hist) if kx // 3 == kk]
        htf = None
        if h15:
            htf = S.BULL if h15[-1]["c"] > h15[0]["o"] else S.BEAR if h15[-1]["c"] < h15[0]["o"] else None
        ev = [S.structure_evidence(self.events, i, P), S.sweep_evidence(sw, i, P), S.delta_cvd_evidence(dpct, slope, cf, S.INFERRED, P),
              S.imbalance_evidence(fp.stacked_imbalances(), P), S.vwap_evidence(v["c"], vwap, trend, P), S.rvol_evidence(rvol, P),
              S.htf_evidence(htf, True, P)]
        hh = ((k * BAR + BAR) % 86400) // 60
        ls = {"direction": self.last_sig[0], "age_sec": (k - self.last_sig[1]) * BAR} if self.last_sig else None
        r = S.score(ev, {"symbol": self.sym, "now_hhmm_ist": f"{hh // 60:02d}:{hh % 60:02d}", "kill_switch": False, "data_stale": False, "last_signal": ls}, P)
        if r["status"] == "SIGNAL":
            self.last_sig = (r["direction"], k)
        res.update({"scored": True, "status": r["status"], "dir": r["direction"], "total": r["total"], "reasons": r["reasons"],
                    "components": r["components"], "groups": r.get("groups")})
        return res

    def _structure(self, i: int, v: dict):
        """Close-confirmed BOS/CHoCH against confirmed pivots (LEN closed bars to the right). A wick beyond the level is recorded but NOT confirmed."""
        L, hist = self.L, self.hist
        j = i - L
        if j - L >= 0:
            hj, lj = hist[j]["h"], hist[j]["l"]
            if all(hj > hist[x]["h"] for x in range(j - L, j)) and all(hj >= hist[x]["h"] for x in range(j + 1, j + L + 1)):
                self.sw_h = hj
            if all(lj < hist[x]["l"] for x in range(j - L, j)) and all(lj <= hist[x]["l"] for x in range(j + 1, j + L + 1)):
                self.sw_l = lj
        prev_c = hist[i - 1]["c"] if i else None
        if prev_c is None:
            return
        if self.sw_h is not None:
            if v["c"] > self.sw_h and prev_c <= self.sw_h:
                self.events.append({"type": "BOS" if self.sdir != -1 else "CHOCH", "direction": S.BULL, "bar_index": i, "close_confirmed": True})
                self.sdir = 1
            elif v["h"] > self.sw_h and v["c"] <= self.sw_h:
                self.events.append({"type": "BOS", "direction": S.BULL, "bar_index": i, "close_confirmed": False})
        if self.sw_l is not None:
            if v["c"] < self.sw_l and prev_c >= self.sw_l:
                self.events.append({"type": "BOS" if self.sdir != 1 else "CHOCH", "direction": S.BEAR, "bar_index": i, "close_confirmed": True})
                self.sdir = -1
            elif v["l"] < self.sw_l and v["c"] >= self.sw_l:
                self.events.append({"type": "BOS", "direction": S.BEAR, "bar_index": i, "close_confirmed": False})
