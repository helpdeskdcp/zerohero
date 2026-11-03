# ZEROHERO MISSING FEATURES (priority = measurable benefit x data availability x ease of validation)
1. Regime engine (ADX trend vs range; ATR high/low vol) - data available - TESTED OFFLINE as a keep/skip gate (ablation.py) before touching Pine.
2. Momentum/exhaustion (RSI side, RSI not exhausted) - data available - TESTED OFFLINE.
3. Price-action strength (body >= 1.0 ATR, close in outer 25%) - TESTED OFFLINE.
4. Trend (5m EMA50 side; 1h EMA stack) - TESTED OFFLINE (15m HTF filter already rejected).
5. Session windows - TESTED (early window only an unproven hypothesis).
6. T4 + trailing SL + daily loss limit - risk-management utilities; they change the exit path, so they can only be judged by the full Pine re-run; implemented as OPTIONAL inputs defaulting to OFF only if an ablation shows a benefit (see EXPERIMENT_LOG).
7. Volume engine - NOT AVAILABLE on indices (volume = 0); would need futures volume (only since 2026-07).
8. OI/IV/Greeks inside Pine - NOT AVAILABLE (Pine cannot read them). Handled by Safe AI outside Pine; actual option history is only 11 days of NIFTY 1-minute bars (Aug 2026) -> options profitability UNVALIDATED.
9. Order blocks, CHoCH labels, engulfing/pin-bar library - low expected benefit (prior studies in this repo found no edge for these price-only patterns); not built, to avoid complexity without evidence.
