"""Edge scan with multiple-testing control. PURE numpy, deterministic, READ-ONLY research.

Pre-registered hypothesis space (families A-G below) is generated mechanically from a day x slot OHLC matrix; nothing is tuned. Each hypothesis is a set of
EVENTS (day, outcome in bps in the signal direction). Test = one-sided cluster-robust (by day) t-test that the mean NET return (gross - cost) is > 0.
Pipeline: S1 discovery (BH-FDR q1) -> S2 validation on a later period, only S1 survivors (BH q2) -> S3 untouched holdout, only S2 survivors (BH q3).
`placebo()` re-runs the whole pipeline with each hypothesis' events replaced by a random same-size sample of its opportunity pool (conditioning destroyed,
drift/time structure kept) to measure how many false survivors the procedure itself lets through."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

NSLOT = 75
SLOT0 = 555                                   # 09:15 IST in minutes


# ------------------------------------------------------------------ statistics
def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def cluster_t(days: np.ndarray, net: np.ndarray) -> tuple[float, float, int, int]:
    """(mean net, one-sided p for mean>0, n events, n distinct days). Cluster-robust by day: events within a day are not independent."""
    n = len(net)
    if n < 2:
        return float("nan"), 1.0, n, 0
    ud, inv = np.unique(days, return_inverse=True)
    if len(ud) < 3:
        return float(net.mean()), 1.0, n, len(ud)
    mu = float(net.mean())
    s = np.bincount(inv, weights=net - mu, minlength=len(ud))               # per-day sum of residuals
    var = float((s ** 2).sum()) / (n ** 2) * len(ud) / (len(ud) - 1)
    if var <= 0:
        return mu, 0.0 if mu > 0 else 1.0, n, len(ud)
    return mu, norm_sf(mu / math.sqrt(var)), n, len(ud)


def bh(pvals: list[float], q: float) -> list[bool]:
    """Benjamini-Hochberg FDR control: True where the hypothesis is rejected."""
    m = len(pvals)
    if m == 0:
        return []
    order = np.argsort(pvals)
    thr = [(i + 1) / m * q for i in range(m)]
    k = -1
    for rank, idx in enumerate(order):
        if pvals[idx] <= thr[rank]:
            k = rank
    out = [False] * m
    for rank, idx in enumerate(order):
        if rank <= k:
            out[idx] = True
    return out


# ------------------------------------------------------------------ data matrices
@dataclass
class Mats:
    day: np.ndarray                  # (D,) day ordinal
    wd: np.ndarray                   # (D,) weekday 0=Mon
    O: np.ndarray                    # (D, NSLOT) NaN where missing
    H: np.ndarray
    L: np.ndarray
    C: np.ndarray

    @property
    def D(self) -> int:
        return len(self.day)


def build_mats(t, o, h, l, c, days, mod, min_bars: int = 60) -> Mats:
    slot = (mod - SLOT0) // 5
    ok = (slot >= 0) & (slot < NSLOT)
    ud = np.unique(days[ok])
    pos = {d: i for i, d in enumerate(ud)}
    D = len(ud)
    mats = [np.full((D, NSLOT), np.nan) for _ in range(4)]
    for arr, src in zip(mats, (o, h, l, c)):
        r = np.array([pos[d] for d in days[ok]])
        arr[r, slot[ok]] = src[ok]
    O, H, L, C = mats
    keep = np.isfinite(C).sum(1) >= min_bars                                   # drop half days / outages
    wd = ((ud + 3) % 7).astype(int)                                            # ordinal 719163 = 1970-01-01 (Thu): (n+3)%7 with Mon=0 only if offset below
    wd = (((ud - 719163) + 3) % 7).astype(int)                                 # 1970-01-01 was a Thursday -> (days+3)%7 gives Mon=0
    return Mats(ud[keep], wd[keep], O[keep], H[keep], L[keep], C[keep])


def _last_valid(a: np.ndarray) -> np.ndarray:
    idx = np.where(np.isfinite(a), np.arange(a.shape[1]), -1).max(1)
    return a[np.arange(a.shape[0]), idx]


# ------------------------------------------------------------------ hypotheses
@dataclass
class Hyp:
    name: str
    family: str
    day: np.ndarray                  # event day ordinals
    val: np.ndarray                  # event outcome bps, ALREADY signed in the signal direction (gross)
    pool_day: np.ndarray             # every opportunity of the same outcome (for the placebo)
    pool_val: np.ndarray             # unsigned outcome of those opportunities
    sign: int
    meta: dict = field(default_factory=dict)


def _mk(name, fam, ev_mask, ret, day_of, sign):
    """ev_mask / ret / day_of are flat arrays over the same opportunity pool."""
    fin = np.isfinite(ret)
    pool_day, pool_val = day_of[fin], ret[fin]
    m = ev_mask & fin
    return Hyp(name, fam, day_of[m], sign * ret[m], pool_day, pool_val, sign)


def generate(M: Mats) -> list[Hyp]:
    D = M.D
    dayc = np.repeat(M.day[:, None], NSLOT, 1)
    out: list[Hyp] = []
    lastC = _last_valid(M.C)
    firstO = M.O[:, 0]
    prevC = np.r_[np.nan, lastC[:-1]]
    # ---- A: slot drift (every day, fixed slot, fixed direction)
    for h in (3, 6, 12):
        ret = np.full((D, NSLOT), np.nan)
        for s in range(0, NSLOT - h - 1):
            ret[:, s] = (M.C[:, s + h] / M.O[:, s + 1] - 1) * 1e4
        for s in range(0, NSLOT - h - 1):
            for sign, side in ((1, "L"), (-1, "S")):
                out.append(_mk(f"A slot{s:02d} h{h} {side}", "A_slot", np.ones(D, bool), ret[:, s], M.day, sign))
    # ---- B: opening gap follow/fade, three exits
    gap = (firstO / prevC - 1) * 1e4
    exits = {"1030": 14, "1230": 38, "close": None}
    for ek, es in exits.items():
        ret = ((lastC if es is None else M.C[:, es]) / M.O[:, 1] - 1) * 1e4
        for thr in (10, 25, 50):
            for gdir, gm in (("up", gap > thr), ("dn", gap < -thr)):
                for act, sign in (("follow", 1 if gdir == "up" else -1), ("fade", -1 if gdir == "up" else 1)):
                    out.append(_mk(f"B gap{thr}{gdir} {act} ->{ek}", "B_gap", gm, ret, M.day, sign))
    # ---- C: first-30-minute move -> later
    r30 = (M.C[:, 5] / M.O[:, 0] - 1) * 1e4
    for ek, es in (("1pm-ish +60m", 17), ("close", None)):
        ret = ((lastC if es is None else M.C[:, es]) / M.O[:, 6] - 1) * 1e4
        for thr in (5, 10, 20):
            for gdir, gm in (("up", r30 > thr), ("dn", r30 < -thr)):
                for act, sign in (("follow", 1 if gdir == "up" else -1), ("fade", -1 if gdir == "up" else 1)):
                    out.append(_mk(f"C r30{thr}{gdir} {act} ->{ek}", "C_first30", gm, ret, M.day, sign))
    # ---- D: extreme 6-bar moves (z vs trailing 30-bar vol), follow/fade
    r1 = np.diff(M.C, axis=1, prepend=np.nan)
    r1 = r1 / np.roll(M.C, 1, axis=1)
    r1[:, 0] = np.nan
    sd = np.full((D, NSLOT), np.nan)
    for s in range(30, NSLOT):
        w = r1[:, s - 29:s + 1]
        sd[:, s] = np.nanstd(w, axis=1)
    r6 = np.full((D, NSLOT), np.nan)
    r6[:, 6:] = M.C[:, 6:] / M.C[:, :-6] - 1
    z = r6 / (sd * math.sqrt(6))
    for h in (3, 6, 12):
        ret = np.full((D, NSLOT), np.nan)
        for s in range(0, NSLOT - h - 1):
            ret[:, s] = (M.C[:, s + h] / M.O[:, s + 1] - 1) * 1e4
        for k in (2.0, 2.5, 3.0):
            for zdir, zm in (("up", z > k), ("dn", z < -k)):
                for act, sign in (("follow", 1 if zdir == "up" else -1), ("fade", -1 if zdir == "up" else 1)):
                    out.append(_mk(f"D z{k}{zdir} {act} h{h}", "D_extreme", zm.ravel(), ret.ravel(), dayc.ravel(), sign))
    # ---- E: weekday effects (overnight close->open, intraday open->close)
    ovn = (firstO / prevC - 1) * 1e4
    intra = (lastC / firstO - 1) * 1e4
    for wd in range(5):
        for leg, ret in (("overnight", ovn), ("intraday", intra)):
            for sign, side in ((1, "L"), (-1, "S")):
                out.append(_mk(f"E wd{wd} {leg} {side}", "E_weekday", M.wd == wd, ret, M.day, sign))
    # ---- F: prior-day return / close location -> next day open->close
    pday = np.r_[np.nan, ((lastC / firstO - 1) * 1e4)[:-1]]
    hi, lo = np.nanmax(M.H, axis=1), np.nanmin(M.L, axis=1)
    loc = (lastC - lo) / np.where(hi > lo, hi - lo, np.nan)
    ploc = np.r_[np.nan, loc[:-1]]
    for thr in (30, 60):
        for pdir, pm in (("up", pday > thr), ("dn", pday < -thr)):
            for act, sign in (("follow", 1 if pdir == "up" else -1), ("fade", -1 if pdir == "up" else 1)):
                out.append(_mk(f"F prevday{thr}{pdir} {act}", "F_prevday", pm, intra, M.day, sign))
    for pdir, pm in (("top", ploc >= 0.8), ("bot", ploc <= 0.2)):
        for act, sign in (("follow", 1 if pdir == "top" else -1), ("fade", -1 if pdir == "top" else 1)):
            out.append(_mk(f"F prevclose-{pdir} {act}", "F_prevday", pm, intra, M.day, sign))
    # ---- G: last-hour move -> overnight
    lh = (lastC / M.C[:, 63] - 1) * 1e4
    for gdir, gm in (("up", lh > 10), ("dn", lh < -10)):
        for act, sign in (("follow", 1 if gdir == "up" else -1), ("fade", -1 if gdir == "up" else 1)):
            nxt = np.r_[ovn[1:], np.nan]                                      # overnight AFTER today's last hour
            out.append(_mk(f"G lasthour{gdir} {act} overnight", "G_lasthour", gm, nxt, M.day, sign))
    return out


# ------------------------------------------------------------------ pipeline
@dataclass
class Periods:
    train: tuple[int, int]
    valid: tuple[int, int]
    oos: tuple[int, int]


def _stat(h: Hyp, per: tuple[int, int], cost: float, val=None, day=None):
    day = h.day if day is None else day
    val = h.val if val is None else val
    m = (day >= per[0]) & (day <= per[1])
    return cluster_t(day[m], val[m] - cost)


def pipeline(hyps: list[Hyp], per: Periods, cost: float, q=(0.10, 0.05, 0.05), min_events=300, min_days=150, placebo_rng=None) -> dict:
    def ev(h):
        if placebo_rng is None:
            return h.day, h.val
        idx = placebo_rng.integers(0, len(h.pool_day), size=len(h.day))
        return h.pool_day[idx], h.sign * h.pool_val[idx]
    cache = {id(h): ev(h) for h in hyps}
    s1 = []
    for h in hyps:
        d, v = cache[id(h)]
        mu, p, n, nd = _stat(h, per.train, cost, v, d)
        if n >= min_events and nd >= min_days:
            s1.append((h, mu, p))
    rej = bh([p for _, _, p in s1], q[0])
    s1s = [(h, mu, p) for (h, mu, p), r in zip(s1, rej) if r and mu > 0]
    s2 = []
    for h, mu0, p0 in s1s:
        d, v = cache[id(h)]
        mu, p, n, nd = _stat(h, per.valid, cost, v, d)
        s2.append((h, mu, p, n))
    rej2 = bh([p for _, _, p, _ in s2], q[1])
    s2s = [(h, mu, p, n) for (h, mu, p, n), r in zip(s2, rej2) if r and mu > 0]
    s3 = []
    for h, _, _, _ in s2s:
        d, v = cache[id(h)]
        mu, p, n, nd = _stat(h, per.oos, cost, v, d)
        s3.append((h, mu, p, n, nd))
    rej3 = bh([p for *_, p, _, _ in [(a, b, c, e, f) for a, b, c, e, f in s3]], q[2]) if s3 else []
    s3s = [x for x, r in zip(s3, rej3) if r and x[1] > 0]
    return {"tested": len(hyps), "eligible": len(s1), "s1": s1s, "s2": s2s, "s3": s3s, "s3_all": s3}


def placebo(hyps, per, cost, runs=20, seed=11, **kw) -> list[tuple[int, int, int]]:
    rng = np.random.default_rng(seed)
    res = []
    for _ in range(runs):
        r = pipeline(hyps, per, cost, placebo_rng=rng, **kw)
        res.append((len(r["s1"]), len(r["s2"]), len(r["s3"])))
    return res


# ------------------------------------------------------------------ daily-bar family (continuous futures with unadjusted ROLL GAPS)
def generate_daily(day, wd, O, H, L, C, tag="") -> list[Hyp]:
    """Pre-registered daily hypotheses on a continuous-futures series whose contract rolls leave unadjusted overnight gaps. Therefore ONLY gap-free quantities
    are used: the outcome is always the NEXT session's open->close return, and every signal is built from open->close returns and high/low ranges of earlier
    sessions (never close-to-close). Dropping roll days by size would condition on the outcome magnitude, so nothing is dropped. All arrays are per session."""
    n = len(day)
    r_oc = (C / O - 1) * 1e4                                         # gap-free session return
    nxt = np.r_[r_oc[1:], np.nan]                                    # outcome: next session open->close
    rng_ = (H - L) / O * 1e4
    atr = np.full(n, np.nan)
    for i in range(20, n):
        atr[i] = np.nanmean(rng_[i - 20:i])                          # mean range of the 20 earlier sessions (no look-ahead)
    loc = (C - L) / np.where(H > L, H - L, np.nan)
    out: list[Hyp] = []

    def add(name, fam, mask, sign):
        out.append(_mk(f"{tag}{name}", fam, mask, nxt, day, sign))
    # A: prior session's open->close
    for thr in (0, 50, 100):
        for pdir, pm in (("up", r_oc > thr), ("dn", r_oc < -thr)):
            for act, sign in (("follow", 1 if pdir == "up" else -1), ("fade", -1 if pdir == "up" else 1)):
                add(f"A prev-oc{thr}{pdir} {act}", "A_prev", pm, sign)
    # B: cumulative gap-free return over the last N sessions
    for N in (2, 3, 5, 10):
        cum = np.full(n, np.nan)
        for i in range(N - 1, n):
            cum[i] = r_oc[i - N + 1:i + 1].sum()
        for pdir, pm in (("up", cum > 0), ("dn", cum < 0)):
            for act, sign in (("follow", 1 if pdir == "up" else -1), ("fade", -1 if pdir == "up" else 1)):
                add(f"B cum{N}d-{pdir} {act}", "B_cum", pm, sign)
    # C: close location in the prior session's range
    for pdir, pm in (("top", loc >= 0.8), ("bot", loc <= 0.2)):
        for act, sign in (("follow", 1 if pdir == "top" else -1), ("fade", -1 if pdir == "top" else 1)):
            add(f"C close-{pdir} {act}", "C_loc", pm, sign)
    # D: range expansion / compression relative to the 20-session mean range
    ratio = rng_ / atr
    sg = np.sign(r_oc)
    fin = np.isfinite(nxt)
    for lab, pm in (("expand>1.5", ratio > 1.5), ("compress<0.6", ratio < 0.6)):
        for act, k in (("follow", 1), ("fade", -1)):
            m = pm & fin & (sg != 0)
            out.append(Hyp(f"{tag}D {lab} {act}", "D_range", day[m], k * sg[m] * nxt[m], day[fin], nxt[fin], 1))
    # E: weekday (next session's weekday), long / short
    nwd = np.r_[wd[1:], -1]
    for w in range(5):
        for sign, side in ((1, "L"), (-1, "S")):
            add(f"E next-wd{w} {side}", "E_weekday", nwd == w, sign)
    return out
