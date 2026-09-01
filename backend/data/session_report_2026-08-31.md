# Autonomous Scalper — session report (2026-08-31)

Window: first open 09:20 IST · last close 21:59 IST · 3063 decision snapshots (08:40–23:58 IST).
LIVE disabled throughout — PAPER only. Calibration still `prior` (0 resolved LIVE samples fitted; needs ≥40).

## Headline
| symbol | closed | W | L | FLAT | net pts | exits |
|---|---|---|---|---|---|---|
| NIFTY | 4 | 3 | 1 | 0 | **+20.95** | TIME ×4 |
| NATURALGAS | 7 | 2 | 2 | 3 | **+0.85** | TIME ×4, TRAIL ×2, STOP ×1 |
| CRUDEOIL | 0 | – | – | – | 0 | never triggered an entry |
| **total** | **11** | **5** | **3** | **3** | **+21.80** | |

ZTH (zero-to-hero): 0 — expected. NSE cash was closed today; the expiry-day engine's
first exercise is tomorrow's NIFTY session (2026-09-01, weekly expiry).

## NIFTY (NSE — carried-over prior session, no new trades today)
```
op            cl     leg        entry   exit    result  pnl     exit
08-31 09:20   09:45  PE 24050   61.35   74.00   WIN    +12.65   TIME
08-31 09:46   11:35  PE 24000   48.65   49.80   WIN     +1.15   TIME
08-31 12:30   12:55  CE 24050  115.70  123.35   WIN     +7.65   TIME
08-31 13:00   13:25  PE 24050   45.05   44.55   LOSS    -0.50   TIME
```
- **All 4 exits are TIME.** Not one trade reached T1 or SL. Same pattern as the very first
  session — the hold clock is expiring before the geometry resolves. This is the open
  strategy-tuning question (max_hold_sec vs T1/SL distance); needs your sign-off with
  forward data, not a silent change.
- decisions: BUY_CE 9 · BUY_PE 123 · NO_TRADE 1385 (heavily NO-TRADE biased, as designed).
- BUY signals fired almost entirely in TRENDING_DOWN (114) vs TRENDING_UP (18).
- regime mix (live rows): TRENDING_UP 184 · UNSTABLE 166 · RANGE 145 · TRENDING_DOWN 123.

## NATURALGAS (MCX evening session)
```
op            cl     leg       entry   exit    result  pnl    exit
08-31 17:05   17:30  CE 280    67.50   67.50   FLAT    0.00   TIME
08-31 18:08   18:38  PE 280    15.85   15.85   FLAT    0.00   TIME
08-31 18:38   19:08  CE 275    12.40   13.60   WIN    +1.20   TIME
08-31 19:35   20:05  PE 280    15.10   14.60   LOSS   -0.50   TIME
08-31 21:00   21:08  CE 275    14.40   14.40   FLAT    0.00   TRAIL
08-31 21:08   21:10  CE 275    14.35   14.15   LOSS   -0.20   STOP
08-31 21:31   21:59  CE 275    15.15   15.50   WIN    +0.35   TRAIL
```
- Net roughly flat (+0.85). 3 FLAT scratches + the profit-lock TRAIL exits doing their job
  (booked +0.35 / 0.00 instead of decaying into a TIME loss). No blow-ups.
- The 21:08 CE 280 @ 67.50 FLAT is the one to watch — a 67-pt premium is not a scalp entry;
  worth a per-NG `min_option_premium` / max-premium cap review.
- decisions: BUY_CE 72 · BUY_PE 33 · NO_TRADE 666.
- regime mix: TRENDING_UP 300 · RANGE 146 · UNSTABLE 125 · TRENDING_DOWN 95.

## CRUDEOIL (MCX evening session)
- **Zero entries.** 141 BUY-lean reads (BUY_CE 22 · BUY_PE 119) but every one was gated
  out — EV gate / safeguards / premium filter. decisions: NO_TRADE 630.
- BUY-lean regimes: TRENDING_DOWN 104 · REVERSAL_REGIME 22 · RANGE 15.
- regime mix: RANGE 406 · TRENDING_DOWN 176 · UNSTABLE 78 · REVERSAL_REGIME 54.
- Not a bug — Crude simply never cleared the bar tonight. Confirm tomorrow it can take a
  trade when conditions are cleaner.

## Takeaways
1. Infra is clean end-to-end across NSE + two MCX underlyings — signal → contract-lock →
   paper fill → mark → exit → persist. The shared-feed clobber fix held (no unmarkable
   positions, no false TIME-at-entry).
2. **The TIME-exit problem persists on NIFTY** (4/4 again). Edge still NOT established.
   Recommend one deliberate change next: widen `max_hold_sec` OR tighten T1 — pick one,
   forward-test, compare. Your call.
3. Profit-lock + TRAIL exits are working as intended on NG (small wins/scratches instead
   of decay-to-TIME losses).
4. Expiry-day engine + ZTH deploy (commit a2b63b8) is live and untriggered — tomorrow is
   its first real test.
