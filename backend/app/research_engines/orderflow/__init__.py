"""
ORDERFLOW_ENGINE v1 -- NIFTY  ::  RESEARCH / BACKTEST ONLY.

Built to ORDERFLOW_ENGINE_SPEC.md. Imported by NOTHING in the live app -- no
runner, HCS, ANN, EPM, signal, order, SL or target code. Reads existing
historical stores read-only; writes only under backend/data/research/orderflow/.
`live_trading` is never touched.

DATA HONESTY (spec section 0 / 20.3):
  TRUE ORDER FLOW NOT AVAILABLE for the NIFTY cash index -- no aggressor side,
  no per-trade size, no tick feed, cash-index volume = 0. This v1 therefore
  computes the DEGRADED / PROXY forms only:
    * flow      -> candle pressure proxy (method="PRESSURE_PROXY"), NOT delta
    * cvd       -> cumulative pressure proxy, NOT true CVD
    * vwap      -> session cumulative HLC3 mean (method="PRICE_PROXY_EQUAL_WEIGHT")
    * profile   -> TPO (exact) ; volume profile is undefined (no volume)
    * absorption/exhaustion/sweep -> STRUCTURAL proxies (reclaim distance,
                                     extension + failure), never volume-confirmed
  Every proxy carries its method string. Nothing is labelled "true" / "proven".

FOUR SEPARATE QUESTIONS (spec final principle), one module each:
  1. WHICH DIRECTION?      signal.direction()          (Signal Direction)
  2. HOW STRONG?           signal.rk_score()           (RK Score, 0-100)
  3. WHEN TO ENTER?        entry_timing.*              (Mathematical Optimal Entry Engine)
  4. WHEN TO EXIT?         exit_engine.simulate()      (Exit Engine)

PIPELINE (spec architecture):
  Market Data -> Strategy/OrderFlow features -> RK Score -> Signal Direction
    -> Entry Timing Engine (two-stage: SIGNAL_DETECTED -> WAITING_FOR_RETEST
       -> ENTRY_READY) -> Risk/Reward validation -> Final Entry -> Exit Engine

TWO-STAGE, never "signal == entry":
  SIGNAL DETECTED (dir + RK score)  --is not--  ENTRY.
  An entry is emitted only after the retest / pullback into the CALIBRATED
  optimal retracement zone is confirmed, momentum recovers, the no-chase
  filter passes, and RR >= min.

CALIBRATION is TRAIN -> VALIDATION -> OOS. The optimal pullback % is fitted on
TRAIN only; VALIDATION confirms; OOS is scored exactly once. Nothing is
hard-coded as the final zone.

OPTION EXECUTION is a SEPARATE downstream layer (option_map.py, advisory only).
Index direction + optimal index entry are decided first; option premium noise
never feeds back into the index signal.
"""
