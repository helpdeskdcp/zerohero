# H-VRP1 addendum A (data actually used) — written BEFORE any result, 2026-10-23
The owner asked to use data already on disk instead of pulling Upstox (disk 87%). What exists: NIFTY option quote snapshots with real bid/ask in market_history.db (captured, about 36 session days, 2026-09-02 .. 2026-10-22) and hist_options.db (11 days, Aug 2026). Index 1-minute history is plentiful (Dhan 2006..2026-09-28, Angel 2026-09-01..).
Consequences, stated now:
- Day count (about 36, DTE >= 1 about 30) is far below what the Stage 1 gate needs (two halves, terciles). The run is therefore EXPLORATORY: whatever it prints, the verdict is INSUFFICIENT_SAMPLE and Stage 2 is not run. It cannot pass or sink H-VRP1; it only shows whether the premise looks plausible and whether the data pipeline works.
- V0 = MID of bid/ask of the K0 CE + PE at the last snapshot received 03:56..03:59:59 UTC (09:26-09:29:59 IST); days with a missing/zero bid or ask on either leg are skipped and counted. This is better than the last-traded proxy in the spec.
- Hm uses index 1-minute closes (09:29 and 15:14 IST bars) of the previous 20 sessions (one source per date, Angel preferred, else Dhan).
- Nothing else in the spec changes. A conclusive test still needs more days (the captured set grows each session; re-run after about 120 captured days, or the Upstox pull if the owner allows the disk).
