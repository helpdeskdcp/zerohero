"""Phase 4: where does the +0.02..0.04 R 'edge' on a random walk come from?
Design: ONE near-continuous true path (600 steps per 1-minute bar, Gaussian, martingale). The engine only ever sees OHLC bars built from a
SAMPLE of that path (stride s => 600/s samples per bar; s=10 ~ one value per second). The very same trades are then re-evaluated on the true
path (stops/targets hit the first moment the true path touches them). On a martingale the true-path expectancy of any such rule is 0, so
(bar-level R) - (true-path R) isolates the effect of building bars from sampled data. No parameters are fitted.
Usage: python scripts/hybrid_mp_bias_audit.py [seeds] [sessions]"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import Params, fixed_params, run  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
STEPS, BARS = 600, 375
ROW = BARS * STEPS + 1                                  # path samples per session (inclusive end)


def make_world(seed, sessions, bar_sd=3.5, gap_sd=40.0):
    r = np.random.default_rng(seed)
    P = np.empty((sessions, ROW))
    px = 22000.0
    for d in range(sessions):
        px += r.normal(0, gap_sd)
        P[d] = px + np.r_[0.0, np.cumsum(r.normal(0, bar_sd / np.sqrt(STEPS), ROW - 1))]
        px = P[d, -1]
    t, d, cur = [], 0, dt.datetime(2026, 1, 5, 9, 15, tzinfo=IST)
    days = []
    while len(days) < sessions:
        if cur.weekday() < 5:
            days.append(int(cur.timestamp()))
        cur += dt.timedelta(days=1)
    t = np.concatenate([np.array(days)[i] + np.arange(BARS) * 60 for i in range(sessions)]).astype(np.int64)
    return P, t


def observed_bars(P, t, stride):
    D = P.shape[0]
    o = np.empty(D * BARS); h = np.empty(D * BARS); l = np.empty(D * BARS); c = np.empty(D * BARS)
    for d in range(D):
        for_m = P[d, :BARS * STEPS + 1]
        idx = np.arange(0, BARS * STEPS + 1, stride)
        S = for_m[idx]                                  # sampled path of the session
        per = STEPS // stride
        # bar m covers sampled indices [m*per, (m+1)*per] inclusive
        starts = np.arange(BARS) * per
        win = np.lib.stride_tricks.sliding_window_view(S, per + 1)[starts]
        o[d * BARS:(d + 1) * BARS] = win[:, 0]; c[d * BARS:(d + 1) * BARS] = win[:, -1]
        h[d * BARS:(d + 1) * BARS] = win.max(axis=1); l[d * BARS:(d + 1) * BARS] = win.min(axis=1)
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": np.zeros(len(t))}


def eval_true(flat, tr, chart, mode, real):
    """re-evaluate engine trades on the true path. mode: PINE | SCALE_BE. real: next-open fill + gap fills + EOD flat (else as-plotted)."""
    out = []
    for t in tr:
        k = t.sig_i
        d, m = divmod(k, BARS)
        side, R = t.side, t.risk
        base = chart["c"][k]
        sl = base - side * R
        tg = [base + side * R * j for j in (1, 2, 3, 4)]
        if real:
            start = d * ROW + m * STEPS                       # open of the NEXT bar == this bar's end sample
            start = d * ROW + (m + 1) * STEPS
            if m == BARS - 1:
                continue                                      # engine skips entries on the last bar when eod_flat
            entry = flat[start]
            if (side == 1 and entry <= sl) or (side == -1 and entry >= sl):
                continue
            end = d * ROW + BARS * STEPS                      # EOD: last sample of the entry day
        else:
            start = d * ROW + (m + 1) * STEPS
            entry = base
            end = flat.size - 1
        seg = side * flat[start + 1:end + 1]
        if seg.size == 0:                                   # trade opened on the very last sample of the data: nothing to evaluate
            out.append(float('nan'))
            continue
        sl_x, tg_x, en_x = side * sl, [side * x for x in tg], side * entry
        if mode == "PINE":
            i_sl = np.argmax(seg <= sl_x) if (seg <= sl_x).any() else -1
            i_t = np.argmax(seg >= tg_x[3]) if (seg >= tg_x[3]).any() else -1
            cand = [(i, kind) for i, kind in ((i_sl, "sl"), (i_t, "t4")) if i >= 0]
            if not cand:
                px_x = seg[-1]
            else:
                i, kind = min(cand)
                px_x = sl_x if kind == "sl" else tg_x[3]
            pnl = (px_x - en_x)
        else:                                                 # SCALE_BE: quarters at T1..T4, stop -> entry after T1
            pos, cur, remain, pnl, hits = 0, sl_x, 1.0, 0.0, 0
            while remain > 1e-9:
                sub = seg[pos:]
                a = np.flatnonzero(sub <= cur)
                b = np.flatnonzero(sub >= tg_x[hits])
                ia, ib = (a[0] if a.size else 10**12), (b[0] if b.size else 10**12)
                if ia == 10**12 and ib == 10**12:
                    pnl += remain * (sub[-1] - en_x); remain = 0; break
                if ia < ib:
                    pnl += remain * (cur - en_x); remain = 0; break
                pnl += 0.25 * (tg_x[hits] - en_x); remain -= 0.25; pos += ib; hits += 1
                if hits == 1:
                    cur = en_x
                if hits == 4:
                    break
        out.append(pnl / R)
    return np.array(out)


def ci(x):
    return 1.96 * x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else float("nan")


def main():
    seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    sessions = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    seed_off = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
    strides = tuple(STEPS // int(x) for x in sys.argv[4].split(",")) if len(sys.argv) > 4 else (60, 30, 10, 5, 2, 1)
    only = sys.argv[5] if len(sys.argv) > 5 else ""
    cfgs = {
        "AS-PLOTTED (close fill, level exits, PINE exit)": (dict(fill="close", gap_fills=False, eod_flat=False, exit_model="PINE"), "PINE", False),
        "REALISTIC (next-open, gap fills, EOD flat, PINE exit)": (dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="PINE"), "PINE", True),
        "FIXED-style (REALISTIC + 25% partials, BE after T1)": (dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="SCALE_BE"), "SCALE_BE", True),
    }
    print(f"world: {seeds} seeds x {sessions} sessions x {BARS} bars, true path {STEPS} steps/bar, bar sd 3.5 pts; strides (samples/bar)= {[STEPS//s for s in strides]}")
    for name, (kw, mode, real) in cfgs.items():
        if only and only not in name:
            continue
        print(f"\n== {name}")
        print(f"{'samples/bar':>11s} {'trades':>7s} {'bar-level R':>16s} {'true-path R':>16s} {'bar - true':>16s}")
        for s in strides:
            bar_r, true_r, n = [], [], 0
            for seed in range(seeds):
                P, t = make_world(seed_off + seed, sessions)
                chart = observed_bars(P, t, s)
                tr, _ = run(chart, Params(pivot_len=5, **kw))
                flat = P.reshape(-1)
                tr_ok = [x for x in tr if not (real and (x.sig_i % BARS == BARS - 1))]
                rt = eval_true(flat, tr_ok, chart, mode, real)
                # trades skipped by the evaluator (gap beyond stop at the open) are dropped from BOTH sides to keep the pairing exact
                keep = []
                for x in tr_ok:
                    k = x.sig_i; d, m = divmod(k, BARS)
                    if real:
                        entry = flat[d * ROW + (m + 1) * STEPS]; sl = chart["c"][k] - x.side * x.risk
                        if (x.side == 1 and entry <= sl) or (x.side == -1 and entry >= sl):
                            continue
                    keep.append(x)
                ok = ~np.isnan(rt)                              # drop unevaluable trades from BOTH sides (exact pairing)
                bar_r += [x.r for x, g in zip(keep, ok) if g]; true_r += list(rt[ok])
            br, tr_ = np.array(bar_r), np.array(true_r)
            print(f"{STEPS//s:>11d} {len(br):>7d} {br.mean():>+8.4f}±{ci(br):.4f} {tr_.mean():>+8.4f}±{ci(tr_):.4f} {(br-tr_).mean():>+8.4f}±{ci(br-tr_):.4f}", flush=True)
    if only:
        return
    print("\n== ordering sensitivity at 60 samples/bar (REALISTIC PINE): adverse-first (default) vs favourable-first")
    for fav in (False, True):
        br = []
        for seed in range(seeds):
            P, t = make_world(seed_off + seed, sessions)
            chart = observed_bars(P, t, 10)
            tr, _ = run(chart, Params(pivot_len=5, fill="next_open", gap_fills=True, eod_flat=True, exit_model="PINE", favorable_first=fav))
            br += [x.r for x in tr]
        br = np.array(br)
        print(f"  favorable_first={fav!s:5s} trades={len(br)} bar-level R={br.mean():+.4f}±{ci(br):.4f}")


if __name__ == "__main__":
    main()
