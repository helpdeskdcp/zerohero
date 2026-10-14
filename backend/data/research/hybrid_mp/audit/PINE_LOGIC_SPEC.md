# Hybrid Market Profile Engine v13.0 — complete logic specification (reference: `pine/hybrid_mp_v13.pine`, sha256 `ea4bf352…`)

Conventions: *bar* = chart candle, *confirmed* = the candle has closed (the Python port evaluates only closed bars). `[1]` = previous bar. All defaults are the script's own.
Python locations: `app/research_engines/hybrid_mp/` — `engine.py` (state machine + simulator), `smc.py` (optional SMC layer, **not** part of the Pine script), `profile.py`, `gap.py`, `alerts.py`, `option_model.py`, `reference_literal.py` (independent literal transliteration used for parity).

## 1. Inputs (group · name · default · effect)
| group | input | default | effect |
|---|---|---|---|
| 01 Profile | `profileInput` | Auto Detect | Auto Detect / Forex-Crypto / Indian Index-Options / Indian Equity / MCX |
| | `fallbackProfile` | Indian Equity | profile used when auto-detection finds nothing |
| 02 Session | `useSession` | true | gate signals/pendings to the session |
| | `indiaSessionInput` | `0915-1530:23456` (Asia/Kolkata) | NSE/BSE regular session, Mon–Fri |
| | `mcxSessionInput` | `0900-2330:23456` (exchange tz) | MCX session |
| | `customSessionInput`, `useCustomForexSession` | `0000-2359:1234567`, false | Forex/Crypto 24/7 unless restricted |
| 03 MTF | `tf1..tf4` | 3, 5, 15, 40 | higher timeframes (minutes) |
| | `requireMTF` | false | filter off by default |
| | `minAgree` | 3 | timeframes that must agree (1–4) |
| | `mtfLookback` | 5 | breakout look-back inside the MTF direction |
| 04 Structure | `pivotLen` | 5 (2–30) | bars each side to confirm a pivot |
| | `showStructure`, `showStructureEvery` | true, 3 | label display only (every Nth event); **does not affect any signal** |
| 05 Entry/Risk | `capital` | 12,000 | informational quantity only |
| | `riskPct` | 1.0 | informational |
| | `atrLen` | 14 | ATR for retest tolerance |
| | `retestBars` | 8 | pending-setup lifetime |
| | `retestTolATR` | 0.20 | retest tolerance = ATR × 0.20 |
| | `useVWAP` | false | VWAP side filter |
| | `useCandleSL` | true | candle-based stop (else previous candle extreme) |
| | `usePatternFilter` | false | require a named pattern |
| | `maruBodyRatio`, `maxWickRatio` | 0.85, 0.08 | marubozu shape |
| | `showLines`, `showGapStatus` | true, true | display |
| | `entryMode` | Hybrid: Retest or Continuation | or "Breakout + Retest" (retest only) |
| | `continuationLookback` | 5 | continuation breakout window |
| 06 Diagnostics | `showPendingList` | true | table display |

## 2. Per-bar evaluation order (the port keeps exactly this order)
1. **Session / day:** `sessionOK` from the bar OPEN time in the profile session; `newTradingDay` = daily timestamp change (calendar day in the exchange tz).
2. **MTF (section 03):** for each `tf`: `f(len)` on the higher-TF series = `+1` if close > highest(high[1],len); `−1` if close < lowest(low[1],len); else `+1` bullish candle / `−1` bearish / `0` doji. Chart value = `f()[1]` with `lookahead_on, gaps_off` ⇒ the direction of the **last completed higher-TF bar**. `bullAgree/bearAgree` = counts; `mtfLongOK = !requireMTF ∨ bullAgree ≥ minAgree` (same for short).
3. **Structure (section 04):** `ph/pl = ta.pivothigh/low(·, pivotLen, pivotLen)` (known `pivotLen` bars late; TradingView tie rule: left ties allowed, right ties not). `swingH/swingL` keep the last confirmed pivots. `breakH = close > swingH ∧ close[1] ≤ swingH`; `breakL` symmetric. `bullCH = breakH ∧ structureDir == −1` and `bearCH = breakL ∧ structureDir == +1` **evaluated before** the update; then (confirmed bar) `structureDir := +1` on `breakH`, `−1` on `breakL`. `bullBoS/bearBoS` are the non-CHoCH breaks. (A level can re-break many times: there is no "used" flag in Pine.)
4. **Candle patterns (section 05):** `cRange = max(high−low, mintick)`, body, wicks, `bRatio`. bull/bear engulfing (previous opposite candle, current open/close engulf previous close/open), marubozu (`bRatio ≥ 0.85`, both wicks/range ≤ 0.08), hammer (`lWick ≥ 2·body ∧ uWick ≤ body`, bullish close) and bearish star (mirror). `bullCandleOK = close > open ∧ (!usePatternFilter ∨ bullPattern)`; bearish mirrored. `trendDir = structureDir` (read **after** step 3, so a continuation entry can fire on the BoS bar itself).
5. **VWAP:** `vwapLongOK = !useVWAP ∨ na(vwap) ∨ close ≥ vwap` (short `≤`). `ta.vwap(hlc3)` resets each session; with no volume it is `na` and **the filter silently passes**.
6. **Pending housekeeping (sections 06/08):** on a new day with no active trade → clear pending. `tol = nz(atr,0)·retestTolATR`. Expired if `bar_index − pendingBar > retestBars` (→ status EXPIRED); cancelled if the session is closed. A bullish break creates `pendingDir=+1, pendingLevel=swingH, pendingBar=bar` only if `sessionOK ∧ !active ∧ pendingDir == 0` (a pending SELL therefore blocks a new BUY setup until it expires/is consumed).
7. **Setups:** `bullRetest = pendingDir==1 ∧ bar_index>pendingBar ∧ low ≤ level+tol ∧ close > level ∧ bullCandleOK` (bear mirrored with `high ≥ level−tol ∧ close < level`). `bullContinuation = entryMode==Hybrid ∧ close > highest(high[1], contLookback) ∧ bullCandleOK` — independent of any pending setup. `buySetup = bullRetest ∨ bullContinuation`.
8. **Signals:** `buySignal = sessionOK ∧ !active ∧ trendDir==1 ∧ buySetup ∧ vwapLongOK ∧ mtfLongOK` (short mirrored; **`profileKnown` is not part of it**). `buyRejected = bullRetest ∧ (trendDir≠1 ∨ filters fail)`. Any signal/rejection consumes the pending setup. Type text: RETEST if `bullRetest` else CONTINUATION.
9. **Entry (sections 10/11):** only if `candidateR ≥ mintick`: `entry = close`; `SL = (bullEngulf ? low[1] : bullMaru∨bullHammer ? low : low[1])` when `useCandleSL` else `low[1]` (short mirrored with highs); `R = |entry − SL|`; `T1..T4 = entry ± R·{1,2,3,4}`; `qty = capital·riskPct/100 / max(R·pointvalue, mintick)` (informational). If `candidateR < mintick`, **no trade is created but the signal/alert has already fired**.
10. **Management (section 12):** runs after entries, only for `bar_index > entryBar`. `hitSL` (long `low ≤ SL`, short `high ≥ SL`) is tested **first**: if true the trade closes as STOPPED even if targets were also touched in the same bar. Otherwise T1..T4 flags are set independently; when `done4` the trade closes ("T4 COMPLETE"). The stop never moves; there is **no end-of-day exit, no time stop, no break-even**.
11. **Diagnostics (section 13/14/15):** direction text, `weakening` (two consecutive closes against the trend), `reversalConfirmed = (bullCH ∨ bearCH) ∧ confirmed`, `reversalWatch`, trend status, signal-check text (profile → session → active → no structure → confirmed signals → rejection reason → pending → "wait" text), trade status, next target (`na` after the trade closes), opening gap (below), pending-table fields.
12. **Alerts (section 17):** BUY/SELL Confirmed (`buySignal/sellSignal`), T1–T4 Reached (event flags), Stop Loss Hit, Reversal Confirmed (CHoCH on a confirmed bar).

## 3. Opening gap (section 07)
`if newTradingDay: priorDayClose := close[1]; todayOpen := open` and `gap% = (todayOpen − priorDayClose)/priorDayClose·100`; > +0.10 → GAP UP, < −0.10 → GAP DOWN, else FLAT OPEN. `close[1]` is the previous **chart bar**: correct for a contiguous regular-session chart, wrong with extended-hours bars or when the last candle of the previous session is missing. `gap.py` keeps both: `opening_gap_pine` (parity) and `opening_gap_session_aware` (uses the last in-session bar of the previous trading date, flags incomplete prior sessions).

## 4. What the Python port adds (all optional and OFF by default unless stated)
Execution assumptions that Pine leaves undefined (`fill`, `gap_fills`, `eod_flat`, `exit_model` PINE/T1–T3/SCALE/SCALE_BE) · correction flags (`swing_used_flag`, `opposite_replaces_pending`, risk bounds in ATR, entry windows, daily limits, `mtf_mode="state"`, `vwap_proxy`, `vwap_missing_blocks`) bundled in `fixed_params()` · SMC context (`smc_filter`, `smc_min`, `smc_require`) · event log (`want_events`) · dashboard state (`want_state`). `Params()` with defaults + `fill="close", gap_fills=False, eod_flat=False, exit_model="PINE"` reproduces the script's own behaviour (verified event-for-event against `reference_literal.py`).

## 5. Known unverifiable / ambiguous points
Higher-TF bucket anchoring (assumed session-open anchored) · behaviour of `request.security` when the requested TF is **lower** than the chart TF (engine flags `mtf_incompatible_tfs`; not TradingView-verified) · `ta.vwap` anchor · exchange holidays · `syminfo.mintick/pointvalue` per instrument.
