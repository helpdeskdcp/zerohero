"""
High-Conviction Runner (HCR) -- READ-ONLY research strategy.

Few signals, 60/40 scale-out (+1R locker + +3R runner), on the one NIFTY
conditioner that replicated across both datasets and all chronological splits in
this session's research: an abnormal 5-minute spike on an already-WIDE-range
(high-volatility) day. See:
  scripts/high_conviction_runner_research.py   (the backtest)
  backend/IMBALANCE_NEXT_CANDLE_1R3_MTF_OI.md  (findings + honest verdict)

RANGE-SHAPE proxy on the NIFTY cash index -- NO volume / L2 / aggressor / OI.
NOT VALIDATED. Nothing PROVEN. This module only reads data and computes; it
never places an order or emits a live trading signal.
"""
from __future__ import annotations

import csv as _csv
import os
import sqlite3
import statistics as st
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(os.path.dirname(_HERE))
_CSV = os.path.join(_BACKEND, "data", "high_conviction_runner_events.csv")
_UDB = os.path.join(_BACKEND, "data", "historical", "upstox", "upstox_research.db")
_HDB = os.path.join(_BACKEND, "data", "market_history.db")

# ---- strategy params (mirror scripts/high_conviction_runner_research.py) ----
DAY_WIDE_MULT = 1.3
SPIKE_X = 3.0
SPIKE_BEFORE = "14:00"
SKIP_DOW = {"Thu", "Fri"}
LEG_A_FRAC, LEG_B_FRAC = 0.60, 0.40
LEG_A_TGT_R, LEG_B_TGT_R = 1.0, 3.0
MAX_MIN = 25
ROLL = 20
DAY_MED_WIN = 20

VERDICT = ("RANGE-SHAPE PROXY, NOT VALIDATED -- NIFTY cash index, no volume/L2/"
           "aggressor/OI. Cross-vendor replicated, no OOS decay, PF>2, but the "
           "real close-green rate is ~63%, not 80-100%. Candidate to paper-watch "
           "only. No production promotion. Nothing PROVEN.")


# ======================================================= backtest summary (from CSV)
def _agg(rows):
    tr = [r for r in rows if r["outcome"] == "TRIGGERED"]
    n = len(tr)
    if not n:
        return {"signals": len(rows), "triggered": 0}
    bl = [float(r["blended_R"]) for r in tr]
    green = [x for x in bl if x > 0]
    loss = [x for x in bl if x < 0]
    a_hit = sum(1 for r in tr if str(r["legA_target_hit"]).lower() in ("1", "true"))
    b_hit = sum(1 for r in tr if str(r["legB_target_hit"]).lower() in ("1", "true"))
    mcl = cur = 0
    for r in sorted(tr, key=lambda r: r["session"]):
        v = float(r["blended_R"])
        if v < 0:
            cur += 1; mcl = max(mcl, cur)
        elif v > 0:
            cur = 0
    return {
        "signals": len(rows), "triggered": n,
        "close_green_pct": round(100 * len(green) / n, 1),
        "legA_1R_hit_pct": round(100 * a_hit / n, 1),
        "legB_3R_hit_pct": round(100 * b_hit / n, 1),
        "blended_expectancy_R": round(st.fmean(bl), 3),
        "blended_profit_factor": round(sum(green) / -sum(loss), 2) if loss else None,
        "median_blended_R": round(st.median(bl), 3),
        "worst_blended_R": round(min(bl), 3),
        "max_consec_losers": mcl,
        "sessions": len({r["session"] for r in rows}),
    }


def backtest_summary() -> dict:
    if not os.path.exists(_CSV):
        return {"available": False, "reason": "run scripts/high_conviction_runner_research.py first",
                "verdict": VERDICT}
    with open(_CSV, newline="") as f:
        rows = list(_csv.DictReader(f))
    out = {"available": True, "verdict": VERDICT, "params": {
        "day_wide_mult": DAY_WIDE_MULT, "spike_x": SPIKE_X, "spike_before": SPIKE_BEFORE,
        "skip_dow": sorted(SKIP_DOW), "leg_a": f"{int(LEG_A_FRAC*100)}% @ +{LEG_A_TGT_R:g}R",
        "leg_b": f"{int(LEG_B_FRAC*100)}% @ +{LEG_B_TGT_R:g}R (BE after leg A)",
        "max_lifetime_min": MAX_MIN}}
    for src in ("UPSTOX", "KAGGLE"):
        sub = [r for r in rows if r["src"] == src]
        if sub:
            yrs = len({r["session"][:4] for r in sub})
            m = _agg(sub)
            m["signals_per_month"] = round(len(sub) / max(1, yrs) / 12, 1)
            m["span"] = f"{min(r['session'] for r in sub)} .. {max(r['session'] for r in sub)}"
            out[src.lower()] = m
    return out


def recent_signals(limit: int = 25) -> list:
    """The most recent historical signals with their walked outcome (from the CSV)."""
    if not os.path.exists(_CSV):
        return []
    with open(_CSV, newline="") as f:
        rows = [r for r in _csv.DictReader(f) if r["src"] == "UPSTOX"]
    rows.sort(key=lambda r: (r["session"], r["spike_time"]))
    out = []
    for r in rows[-limit:][::-1]:
        out.append({
            "session": r["session"], "spike_time": r["spike_time"], "dow": r["dow"],
            "range_x": float(r["range_x"]), "outcome": r["outcome"],
            "side": r.get("side") or None,
            "blended_R": float(r["blended_R"]) if r.get("blended_R") not in (None, "") else None,
            "legA_R": float(r["legA_R"]) if r.get("legA_R") not in (None, "") else None,
            "legB_R": float(r["legB_R"]) if r.get("legB_R") not in (None, "") else None,
            "green": str(r.get("green")).lower() in ("1", "true"),
        })
    return out


# ======================================================= live scan (read-only)
def _hm(t):
    return t.strftime("%H:%M")


def _load_5m():
    """{date: [bars]} NIFTY 5m -- Upstox history + fresh market_history rows on top."""
    days: dict = {}
    if os.path.exists(_UDB):
        con = sqlite3.connect(f"file:{_UDB}?mode=ro", uri=True)
        for ts, o, h, l, c in con.execute(
                "SELECT timestamp,open,high,low,close FROM normalized_bars "
                "WHERE source='upstox' AND symbol='NIFTY' AND timeframe='5m' ORDER BY timestamp"):
            if None in (o, h, l, c):
                continue
            t = datetime.fromisoformat(ts).astimezone(_IST)
            if "09:15" <= _hm(t) <= "15:30":
                days.setdefault(t.date().isoformat(), []).append(
                    {"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
        con.close()
    if os.path.exists(_HDB):
        con = sqlite3.connect(f"file:{_HDB}?mode=ro", uri=True)
        for bs, o, h, l, c, d in con.execute(
                "SELECT bar_start,o,h,l,c,session_date_ist FROM market_candles "
                "WHERE symbol='NIFTY' AND tf='5m' AND kind='INDEX' ORDER BY bar_start"):
            if None in (o, h, l, c):
                continue
            t = datetime.fromisoformat(bs.replace("Z", "+00:00")).astimezone(_IST)
            if "09:15" <= _hm(t) <= "15:30":
                lst = days.setdefault(d, [])
                if not any(abs((x["t"] - t).total_seconds()) < 1 for x in lst):
                    lst.append({"t": t, "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
        con.close()
    for d in days:
        days[d].sort(key=lambda b: b["t"])
    return days


def _dayrange(bars):
    return max(b["h"] for b in bars) - min(b["l"] for b in bars)


def _range_x(bars, i):
    if i < ROLL:
        return None
    prev = [b["h"] - b["l"] for b in bars[i - ROLL:i]]
    m = st.median(prev)
    return (bars[i]["h"] - bars[i]["l"]) / m if m > 0 else None


def _walk_scaleout(bars, n1_idx):
    n1 = bars[n1_idx]
    hi, lo = n1["h"], n1["l"]
    R = hi - lo
    if R <= 0:
        return {"outcome": "BAD_R"}
    t0 = n1["t"] + timedelta(minutes=5)
    seg = [b for b in bars if t0 < b["t"] <= t0 + timedelta(minutes=MAX_MIN)]
    if not seg:
        return {"outcome": "PENDING"}
    side = entry = slp = None
    k0 = None
    for k, b in enumerate(seg):
        up, dn = b["h"] >= hi, b["l"] <= lo
        if up and dn:
            side = "LONG" if b["c"] >= b["o"] else "SHORT"
        elif up:
            side = "LONG"
        elif dn:
            side = "SHORT"
        if side:
            k0 = k
            entry = hi if side == "LONG" else lo
            slp = lo if side == "LONG" else hi
            break
    if side is None:
        return {"outcome": "NO_TRIGGER"}
    a_t = entry + LEG_A_TGT_R * R if side == "LONG" else entry - LEG_A_TGT_R * R
    b_t = entry + LEG_B_TGT_R * R if side == "LONG" else entry - LEG_B_TGT_R * R
    a_R = b_R = None
    be = False
    last_c = None
    for b in seg[k0:]:
        last_c = b["c"]
        hi_hit = (b["h"] >= a_t) if side == "LONG" else (b["l"] <= a_t)
        b_hit = (b["h"] >= b_t) if side == "LONG" else (b["l"] <= b_t)
        sl_hit = (b["l"] <= slp) if side == "LONG" else (b["h"] >= slp)
        be_hit = (b["l"] <= entry) if side == "LONG" else (b["h"] >= entry)
        if a_R is None and sl_hit:
            a_R = b_R = -1.0
            break
        if a_R is None and hi_hit:
            a_R = LEG_A_TGT_R
            be = True
        elif a_R is not None and be and b_R is None and be_hit:
            b_R = 0.0
            break
        if a_R is not None and b_R is None and b_hit:
            b_R = LEG_B_TGT_R
            break
    if a_R is None:
        a_R = round(((last_c - entry) if side == "LONG" else (entry - last_c)) / R, 3)
    if b_R is None:
        b_R = round(((last_c - entry) if side == "LONG" else (entry - last_c)) / R, 3)
    blended = round(LEG_A_FRAC * a_R + LEG_B_FRAC * b_R, 3)
    return {"outcome": "TRIGGERED", "side": side, "entry": round(entry, 2),
            "sl": round(slp, 2), "R_pts": round(R, 2),
            "legA_R": round(a_R, 3), "legB_R": round(b_R, 3), "blended_R": blended,
            "green": blended > 0}


def live_scan(n_days: int = 12) -> dict:
    """Qualifying HCR setups in the last `n_days` NIFTY 5m sessions we have data
    for (incl. today, if histcap has captured it). READ-ONLY, no order."""
    days = _load_5m()
    ds = sorted(days)
    if not ds:
        return {"available": False, "reason": "no NIFTY 5m data"}
    recent = ds[-n_days:]
    sigs = []
    for d in recent:
        bars = days[d]
        if len(bars) < ROLL + 6:
            continue
        k = ds.index(d)
        prior = [_dayrange(days[x]) for x in ds[max(0, k - DAY_MED_WIN):k] if len(days[x]) >= 6]
        med = st.median(prior) if prior else None
        # day-so-far range (a live day is partial -- this is the running range)
        day_wide = bool(med and _dayrange(bars) >= DAY_WIDE_MULT * med)
        dow = datetime.fromisoformat(d).strftime("%a")
        if dow in SKIP_DOW:
            continue
        for i in range(ROLL, len(bars) - 1):
            if _hm(bars[i]["t"]) >= SPIKE_BEFORE:
                break
            rx = _range_x(bars, i)
            if rx is None or rx < SPIKE_X:
                continue
            w = _walk_scaleout(bars, i + 1)
            sigs.append({
                "session": d, "dow": dow, "spike_time": _hm(bars[i]["t"]),
                "range_x": round(rx, 2), "day_wide": day_wide,
                "day_range": round(_dayrange(bars), 1),
                "day_range_median": round(med, 1) if med else None,
                **w,
            })
    # only the ones that passed the wide-day gate are actual HCR signals;
    # the rest are shown as "spike, day not wide -> skipped" for transparency
    fired = [s for s in sigs if s["day_wide"]]
    skipped = [s for s in sigs if not s["day_wide"]]
    return {
        "available": True, "verdict": VERDICT,
        "sessions_scanned": recent, "latest_session": ds[-1],
        "fired": fired[::-1], "skipped_not_wide": skipped[::-1],
        "n_fired": len(fired), "n_skipped": len(skipped),
    }


def forward_test_record(session: str | None = None) -> dict:
    """One forward-test observation for a single NIFTY 5m session: did HCR fire,
    why / why not, and (if the session is complete) the walked outcome. READ-ONLY.
    `session` = 'YYYY-MM-DD' IST, or None for the latest session we have data for."""
    days = _load_5m()
    ds = sorted(days)
    if not ds:
        return {"available": False, "reason": "no NIFTY 5m data"}
    d = session or ds[-1]
    if d not in days:
        return {"available": False, "reason": f"no data for {d}", "have": ds[-5:]}
    bars = days[d]
    k = ds.index(d)
    prior = [_dayrange(days[x]) for x in ds[max(0, k - DAY_MED_WIN):k] if len(days[x]) >= 6]
    med = st.median(prior) if prior else None
    dr = _dayrange(bars)
    dow = datetime.fromisoformat(d).strftime("%a")
    day_wide = bool(med and dr >= DAY_WIDE_MULT * med)
    spikes = []
    for i in range(ROLL, len(bars)):
        if _hm(bars[i]["t"]) >= SPIKE_BEFORE:
            break
        rx = _range_x(bars, i)
        if rx is None:
            continue
        if rx >= SPIKE_X:
            spikes.append((i, _hm(bars[i]["t"]), round(rx, 2)))
    max_rx = max((round(_range_x(bars, i) or 0, 2)
                  for i in range(ROLL, len(bars)) if _hm(bars[i]["t"]) < SPIKE_BEFORE), default=None)
    fired = day_wide and dow not in SKIP_DOW and bool(spikes)
    out = {
        "available": True, "session": d, "dow": dow,
        "bars": len(bars), "first": _hm(bars[0]["t"]), "last": _hm(bars[-1]["t"]),
        "session_complete": _hm(bars[-1]["t"]) >= "15:15",
        "day_range": round(dr, 1), "day_range_median_20d": round(med, 1) if med else None,
        "wide_threshold": round(DAY_WIDE_MULT * med, 1) if med else None,
        "day_wide": day_wide, "dow_ok": dow not in SKIP_DOW,
        "max_range_x_before_1400": max_rx,
        "qualifying_spikes": [{"time": t, "range_x": r} for _, t, r in spikes],
        "hcr_fired": fired,
    }
    if fired:
        trades = []
        for (i, t, r) in spikes:
            w = _walk_scaleout(bars, i + 1)
            trades.append({"spike_time": t, "range_x": r, **w})
        out["trades"] = trades
        done = [x for x in trades if x.get("outcome") == "TRIGGERED"]
        if done:
            bl = [x["blended_R"] for x in done]
            out["day_blended_R"] = round(sum(bl), 3)
            out["day_close_green"] = sum(bl) > 0
    else:
        out["reason_no_fire"] = (
            ("day not wide" if not day_wide else "")
            + ("; " if (not day_wide and dow in SKIP_DOW) else "")
            + (f"DoW {dow} skipped" if dow in SKIP_DOW else "")
            + ("; " if ((not day_wide or dow in SKIP_DOW) and not spikes) else "")
            + (f"no 5m range_x ≥ {SPIKE_X} before {SPIKE_BEFORE} (max {max_rx})" if not spikes else "")
        ).strip("; ")
    return out
