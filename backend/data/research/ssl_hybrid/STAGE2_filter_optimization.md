# SSL Hybrid PRO -- Stage 2: confirmation/filter-layer optimisation

_2026-09-09. SSL1=100 fixed. SSL signal logic UNCHANGED -- Stage 2 only adds a
post-signal filter that drops triggered setups. Filters were chosen from the
IN-SAMPLE slice (2016 -> ~2022, 70% of sessions) by win% + expectancy + economic
rationale, then validated ONCE on OOS (last 30% of sessions) + walk-forward.
Pooled NIFTY+BANKNIFTY 15m; baseline IS n=4160, OOS n=1760._

## In-sample screening -- what actually separates winners from losers

| feature | finding (in-sample) |
|---|---|
| **time-of-day** | opening hour (<10:00) win **34.7%**, last ~90 min win **34.5%**; the 10:00-13:30 window win **~45-47%**. Largest, most robust, most sensible split. |
| HMA slope agrees with side | ON win 40.4% / PF 0.76 vs OFF 34.3% / PF 0.51 |
| VWAP extension > 3.5 ATR | win **19%**, PF 0.20 (n=142) -- a clear "too stretched" tail |
| ADX | *lower* is better (20-25 -> 43% win; 40+ -> 40% but PF 0.57). Chasing a stretched trend hurts. Weak. |
| confidence score | non-monotonic (75-79 best, 80+ worst) -> spurious, rejected |
| EMA200 align, DI spread, ATR regime, cross-freshness | no usable separation |

## Selected filter (opt-in; config keys, all default OFF)

```
filter_session_window     = [600, 810]   # minute-of-day: 10:00-13:30 IST
filter_require_hma_slope   = true
filter_max_vwap_ext_atr    = 3.0
```

## Ablation -- 15m, 2016-2025, real engine (thirds + SL->BE + session flat)

| filter set | NIFTY OOS n / win% / net pts / PF / +yrs | BANKNIFTY OOS n / win% / net pts / PF / +yrs |
|---|---|---|
| baseline | 905 / 38.6% / **-8,055** / 0.69 / 0-10 | 855 / 37.5% / **-32,096** / 0.59 / 0-10 |
| + session[600,810] | 496 / 49.2% / **+1,118** / 1.09 / 5-10 | 498 / 43.0% / -12,784 / 0.71 / 1-10 |
| + hma_slope only | 795 / 39.9% / -5,392 / 0.76 / 0-10 | 761 / 38.5% / -24,976 / 0.63 / 1-10 |
| + vwap_ext<=3 only | 859 / 39.7% / -6,735 / 0.73 / 0-10 | 820 / 38.0% / -30,797 / 0.59 / 0-10 |
| session + hma | 434 / 50.9% / +1,953 / 1.18 / 5-10 | 444 / 43.9% / -9,428 / 0.76 / 2-10 |
| **FULL (session+hma+vwap)** | **427 / 51.5% / +2,256 / 1.22 / 5-10** | 437 / 43.7% / -9,712 / 0.75 / 2-10 |
| FULL + adx<=30 | 261 / 51.3% / +1,195 / 1.19 / 4-10 | 278 / 42.1% / -8,332 / 0.66 / 4-10 |

**The session-time filter does almost all of the work.** HMA + VWAP add a little.
ADX cap just shrinks the sample.

## Result

**NIFTY 15m, FULL filter** -- the first OOS-positive result in this whole study:
- ALL: 1362 tr, 49.5% win, **+2,018 pts**, PF 1.07
- IS : 935 tr, 48.7% win, +987 pts, PF 1.03 (barely positive)
- **OOS: 427 tr, 51.5% win, +2,256 pts, PF 1.22**, net-positive in 2020, 2021,
  2023, 2024, 2025 (5 of the last 6 years)
- IS -> OOS holds (win 48.7 -> 51.5, both far above the 38% baseline): not curve-fit.

**BANKNIFTY 15m, FULL filter** -- still NO-GO: OOS -9,712 pts, PF 0.75. BANKNIFTY
does not work under any filter combination tried.

## Verdict: **still NO-GO** (as a universal strategy), but NIFTY is now marginal

- BANKNIFTY fails outright -> not a GO.
- NIFTY OOS PF **1.22 < 1.30** GO bar, and the in-sample half is only breakeven
  (PF 1.03). It is a thin, NIFTY-only, recent-regime-favoured edge -- promising,
  not proven.

## Carried into Stage 3

- baseline for the SSL1 robustness sweep = **NIFTY 15m + FULL filter**
  (`filter_session_window=[600,810]`, `filter_require_hma_slope=true`,
  `filter_max_vwap_ext_atr=3.0`). BANKNIFTY dropped from the GO question;
  reported for reference only.
- Do NOT re-tune the filter thresholds during the SSL1 sweep.
- A value survives only if OOS PF and walk-forward hold at that SSL1 -- never
  selected on in-sample win rate.

Filters live at `strategy._passes_filters` (config keys above); OFF by default so
the baseline is unchanged. Tests: `tests/test_ssl_hybrid.py` (subset-only,
session-window respected).
