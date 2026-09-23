"""
Component 3 P&L backtest (retry, capped sample) -- real bar-by-bar P&L walk
for multi-TF S/R confluence bounces. Reuses the exact confluence definition
from component_backtests.py (5m swing within 0.03% of a 30m swing price).
No look-ahead: entry at the swing's own confirmation bar (i+2, since
swings(left=2,right=2) needs 2 forward bars to confirm a pivot), SL/target
walked forward only from bars after entry. Read-only reuse of
app/liquidity_sweep/ -- nothing there modified. Bounded: samples <=500
events, walks each forward <=30 bars only.
"""
from __future__ import annotations

import random
import statistics as st
import time

from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.liquidity_sweep.resample import resample_bars
from app.liquidity_sweep.structure import swings

SL_BUFFER_PCT = 0.15 / 100.0    # structural buffer beyond the swing zone
RR = 1.5                        # target = entry + RR * (entry - SL)
MAX_HOLD_BARS = 30              # ~2.5h on 5m bars, no overnight
SAMPLE_CAP = 500
SEED = 42

t0 = time.time()
print("Loading real Kaggle NIFTY 5m bars (2yr)...")
bars5 = load_kaggle_nifty_bars(limit_years=2.0)
bars30 = resample_bars(bars5, 30)
print(f"  {len(bars5)} 5m bars, {len(bars30)} 30m bars ({time.time()-t0:.1f}s)")

sw5 = swings(bars5, left=2, right=2)
sw30_prices = sorted(p.price for p in swings(bars30, left=2, right=2))
print(f"  {len(sw5)} 5m swings, {len(sw30_prices)} 30m swings ({time.time()-t0:.1f}s)")


def near_htf(price, tol_pct=0.03):
    for hp in sw30_prices:
        if abs(price - hp) <= hp * tol_pct / 100:
            return True
    return False


events = []
for p in sw5:
    i = p.index
    if i + 2 >= len(bars5):
        continue
    if not near_htf(p.price):
        continue
    events.append(p)
print(f"  {len(events)} real confluence events found ({time.time()-t0:.1f}s)")

rng = random.Random(SEED)
sample = events if len(events) <= SAMPLE_CAP else rng.sample(events, SAMPLE_CAP)
sample.sort(key=lambda p: p.index)   # keep chronological order for the split
print(f"  sample size for P&L walk: {len(sample)}")


def simulate(p):
    i = p.index
    entry_i = i + 2   # confirmation bar -- earliest actionable point, no look-ahead
    if entry_i >= len(bars5):
        return None
    entry = bars5[entry_i]["c"]
    long_side = (p.kind == "L")
    sl = entry * (1 - SL_BUFFER_PCT) if long_side else entry * (1 + SL_BUFFER_PCT)
    risk = abs(entry - sl)
    target = entry + RR * risk if long_side else entry - RR * risk

    mfe = mae = 0.0
    for k in range(1, MAX_HOLD_BARS + 1):
        j = entry_i + k
        if j >= len(bars5):
            return {"outcome": "TIME", "points": bars5[j - 1]["c"] - entry if long_side
                    else entry - bars5[j - 1]["c"], "mfe": mfe, "mae": mae, "bars_held": k - 1}
        b = bars5[j]
        fav = (b["h"] - entry) if long_side else (entry - b["l"])
        adv = (entry - b["l"]) if long_side else (b["h"] - entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        hit_sl = (b["l"] <= sl) if long_side else (b["h"] >= sl)
        hit_tgt = (b["h"] >= target) if long_side else (b["l"] <= target)
        if hit_sl and hit_tgt:
            # ambiguous same-bar touch -- conservative: SL first (matches
            # this session's established "assume the worse outcome" convention)
            return {"outcome": "SL", "points": -risk, "mfe": mfe, "mae": mae, "bars_held": k}
        if hit_sl:
            return {"outcome": "SL", "points": -risk, "mfe": mfe, "mae": mae, "bars_held": k}
        if hit_tgt:
            return {"outcome": "TARGET", "points": RR * risk, "mfe": mfe, "mae": mae, "bars_held": k}
    j = entry_i + MAX_HOLD_BARS
    close = bars5[j]["c"]
    pts = (close - entry) if long_side else (entry - close)
    return {"outcome": "TIME", "points": pts, "mfe": mfe, "mae": mae, "bars_held": MAX_HOLD_BARS}


trades = []
for n, p in enumerate(sample, 1):
    r = simulate(p)
    if r is not None:
        trades.append(r)
    if n % 50 == 0:
        print(f"  ...{n}/{len(sample)} simulated ({time.time()-t0:.1f}s)")

print(f"  {len(trades)} trades simulated ({time.time()-t0:.1f}s)")


def _metrics(rows):
    if not rows:
        return {"n": 0}
    wins = [r["points"] for r in rows if r["points"] > 0]
    losses = [r["points"] for r in rows if r["points"] <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for r in rows:
        eq += r["points"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "n": len(rows), "win_rate": round(len(wins) / len(rows), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "expectancy_pts": round(sum(r["points"] for r in rows) / len(rows), 4),
        "avg_win": round(gross_win / len(wins), 3) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 3) if losses else None,
        "avg_mfe": round(st.mean(r["mfe"] for r in rows), 3),
        "avg_mae": round(st.mean(r["mae"] for r in rows), 3),
        "max_drawdown_pts": round(mdd, 3),
    }


mid = len(trades) // 2
fwd_train, fwd_test = trades[:mid], trades[mid:]
swap_train, swap_test = trades[mid:], trades[:mid]

m_all = _metrics(trades)
m_fwd_test = _metrics(fwd_test)
m_swap_test = _metrics(swap_test)

print("\n=== RESULTS ===")
print("all:", m_all)
print("forward split (test = 2nd half):", m_fwd_test)
print("swapped split (test = 1st half):", m_swap_test)
print(f"\ntotal runtime: {time.time()-t0:.1f}s")

out_md = f"""

## Component 3 — P&L Backtest (retry, capped sample)

Real bar-by-bar P&L walk for the multi-TF S/R confluence signal (5m swing
within 0.03% of a 30m swing price), reusing the exact confluence detector
from the hit-rate pass above. Not the T1/T2/T3 ladder (already NO-GO,
component 4) -- a single 1:{RR} R:R target for a first P&L read.

**Rule**: entry at the swing's confirmation bar close (i+2, since
`swings(left=2,right=2)` needs 2 forward bars to confirm a pivot -- no
look-ahead). Structural SL = swing price +/- {SL_BUFFER_PCT*100:.2f}% buffer
beyond the zone. Target = entry +/- {RR}R. Max hold {MAX_HOLD_BARS} bars
(~2.5h), no overnight. Same-bar SL+target ambiguity resolved conservatively
(SL wins). GROSS-ONLY (NIFTY has no validated cost profile in
app.institutional_edge.costs, confirmed earlier this session -- not
fabricated here).

- Total confluence events found: {len(events)}; sampled {len(sample)} (seed {SEED}, cap {SAMPLE_CAP}); {len(trades)} simulated.
- **All trades**: {m_all}
- **Forward split** (train=1st half, test=2nd half): {m_fwd_test}
- **Swapped split** (train=2nd half, test=1st half): {m_swap_test}

**Verdict**: {"GO" if (m_fwd_test.get("profit_factor") or 0) > 1.3 and (m_swap_test.get("profit_factor") or 0) > 1.3 else "PROMISING" if (m_fwd_test.get("profit_factor") or 0) > 1.0 and (m_swap_test.get("profit_factor") or 0) > 1.0 else "NO-GO"} --
{"both splits show a real, consistent edge" if (m_fwd_test.get("profit_factor") or 0) > 1.0 and (m_swap_test.get("profit_factor") or 0) > 1.0 else "edge does not survive both the forward and swapped split -- do not treat the earlier hit-rate result as validated for real P&L"}.
"""
with open("COMPONENT_BACKTESTS.md", "a") as f:
    f.write(out_md)
print("\nAppended to COMPONENT_BACKTESTS.md")
