# NXI input reference (generated from the source - do not edit by hand)

127 inputs in 14 groups. Inputs are grouped exactly as in the TradingView settings dialog.


## 01 General / sessions

| input | type | default | options | notes |
|---|---|---|---|---|
| Weekly expiry weekday (VERIFY - holidays shift it) (`i_expDow`) | string | `"Tuesday"` | Monday, Tuesday, Wednesday, Thursday, Friday, Off | NIFTY weekly expiry day is an assumption you must verify. Use the override below on shifted-expiry weeks. |
| Override: today IS the expiry session (`i_expForce`) | bool | `false` |  |  |
| ATR length (chart timeframe) (`i_atrLen`) | int | `14` |  |  |
| Daily ATR window (sessions, own tracking) (`i_atrDays`) | int | `14` |  |  |
| Volume handling (`i_volMode`) | string | `"Auto"` | Auto, Ignore volume | Auto: volume features switch on only if the chart reports non-zero volume. Index charts usually have NO volume: features are then marked UNAVAILABLE, never estimated. |
| Relative-volume baseline (bars) (`i_volBaseN`) | int | `10` |  |  |

## 02 Higher-timeframe context (non-repainting: previous completed HTF bar)

| input | type | default | options | notes |
|---|---|---|---|---|
| HTF 1 (`i_tf1`) | timeframe | `"15"` |  |  |
| HTF 2 (`i_tf2`) | timeframe | `"30"` |  |  |
| HTF 3 (`i_tf3`) | timeframe | `"60"` |  |  |
| HTF 4 (`i_tf4`) | timeframe | `"240"` |  |  |
| HTF fast EMA (`i_htfFast`) | int | `9` |  |  |
| HTF slow EMA (`i_htfSlow`) | int | `21` |  |  |

## 03 Previous-session option-chain OI zones - MANUAL INPUT (Pine cannot read option-chain OI)

| input | type | default | options | notes |
|---|---|---|---|---|
| Use manual OI zones (`i_oiOn`) | bool | `true` |  |  |
| OI data date (the session the OI snapshot is from) (`i_oiDate`) | time | `timestamp("1 Jan 2000 00:00 +0000")` |  | Must be the PREVIOUS trading session. Left at 2000-01-01 = 'not provided'. |
| Option expiry the OI belongs to (text, e.g. 28-Oct-2026) (`i_oiExpiry`) | string | `""` |  |  |
| Put OI #1 strike (highest) (`i_p1K`) | float | `0` |  |  |
| Put OI #1 (contracts) (`i_p1OI`) | float | `0` |  |  |
| Put OI #2 strike (2nd) (`i_p2K`) | float | `0` |  |  |
| Put OI #2 (contracts) (`i_p2OI`) | float | `0` |  |  |
| Call OI #1 strike (highest) (`i_c1K`) | float | `0` |  |  |
| Call OI #1 (contracts) (`i_c1OI`) | float | `0` |  |  |
| Call OI #2 strike (2nd) (`i_c2K`) | float | `0` |  |  |
| Call OI #2 (contracts) (`i_c2OI`) | float | `0` |  |  |
| Extra zone #1 strike (`i_x1K`) | float | `0` |  |  |
| Extra zone #1 OI (`i_x1OI`) | float | `0` |  |  |
| Extra zone #1 side (`i_x1Type`) | string | `"PUT"` | PUT, CALL |  |
| Extra zone #2 strike (`i_x2K`) | float | `0` |  |  |
| Extra zone #2 OI (`i_x2OI`) | float | `0` |  |  |
| Extra zone #2 side (`i_x2Type`) | string | `"CALL"` | PUT, CALL |  |
| Zone half-width (index points) (`i_zoneW`) | float | `20` |  |  |
| Weight: highest-OI zone (`i_wRank1`) | float | `1.0` |  |  |
| Weight: 2nd-OI zone (`i_wRank2`) | float | `0.6` |  |  |
| Weight: extra zones (`i_wExtra`) | float | `0.4` |  |  |
| Weight: previous-day high/low price levels (`i_wPrice`) | float | `0.8` |  |  |
| Zone must be price-confirmed (respected) before its break counts (`i_needResp`) | bool | `true` |  | The largest-OI strike is NOT assumed to be the strongest. A zone only counts as 'confirmed' after price has tested it and closed back on the respecting side. |
| Minimum respected tests for 'confirmed' (`i_minRespect`) | int | `1` |  |  |
| Minimum zone weight to trade a break (`i_minZoneW`) | float | `0.35` |  |  |

## 04 Gap intelligence (ATR-normalised, daily ATR tracked from the chart's own sessions)

| input | type | default | options | notes |
|---|---|---|---|---|
| Flat open if |gap| < x daily ATR (`i_flatThr`) | float | `0.15` |  |  |
| Exceptional gap if |gap| >= x daily ATR (`i_excThr`) | float | `1.00` |  |  |
| Gap-fill fraction that counts as a reversal (`i_fillFrac`) | float | `0.35` |  |  |

## 05 Opening / first completed candle

| input | type | default | options | notes |
|---|---|---|---|---|
| Opening range minutes (`i_orMin`) | int | `15` |  |  |
| Strong candle: body / range >= (`i_strongBody`) | float | `0.55` |  |  |
| Close location (0..1) bullish if >= (`i_locHi`) | float | `0.65` |  |  |
| Close location bearish if <= (`i_locLo`) | float | `0.35` |  |  |
| Bars to wait after the first candle before any entry (`i_waitBars`) | int | `1` |  | 1 = the second 5-minute candle must close and validate the first one. The first candle itself never triggers an entry. |

## 06 SMC / liquidity (confirmed pivots only)

| input | type | default | options | notes |
|---|---|---|---|---|
| Pivot left bars (`i_pivL`) | int | `3` |  |  |
| Pivot right bars (= confirmation delay) (`i_pivR`) | int | `3` |  |  |
| Structure / sweep evidence stays valid for N bars (`i_evFresh`) | int | `12` |  |  |
| Failed breakout/breakdown window (bars) (`i_fbBars`) | int | `6` |  |  |
| Retest window (bars) (`i_retestBars`) | int | `8` |  |  |
| Retest tolerance (x ATR) (`i_retestTol`) | float | `0.25` |  |  |
| Displacement: range >= x ATR (`i_dispAtr`) | float | `1.2` |  |  |
| Displacement: body / range >= (`i_dispBody`) | float | `0.6` |  |  |
| Draw recent unmitigated FVG boxes (`i_showFvg`) | bool | `true` |  |  |

## 07 Seller-stress rules (symmetrical)

| input | type | default | options | notes |
|---|---|---|---|---|
| Require failed reclaim / rejection at the broken zone (`i_reqReject`) | bool | `true` |  |  |
| Require BOS/CHoCH or displacement in the break direction (`i_reqStruct`) | bool | `true` |  |  |
| Entry trigger (`i_trig`) | string | `"Stress or gap scenario"` | Stress only, Gap scenario only, Stress or gap scenario | Stress: potential seller-stress pattern at a broken zone. Gap scenario: validated gap continuation/reversal/flat-open breakout plus structure in the same direction. |
| Rejection / reclaim window (bars) (`i_rejBars`) | int | `8` |  |  |
| Momentum EMA fast (`i_emaF`) | int | `9` |  |  |
| Momentum EMA slow (`i_emaS`) | int | `21` |  |  |
| RSI length (`i_rsiLen`) | int | `14` |  |  |

## 08 Candle/volume proxy (original transparent proxy - NOT a copy of any proprietary indicator)

| input | type | default | options | notes |
|---|---|---|---|---|
| Relative volume >= (`i_rvThr`) | float | `1.5` |  |  |
| Range >= x ATR (`i_rngAtr`) | float | `1.3` |  |  |
| Body / range >= (`i_bodyThr`) | float | `0.6` |  |  |
| Conditions required (of 6) (`i_proxyMin`) | int | `4` |  |  |

## 09 CE / PE premium (select the two option charts' symbols manually)

| input | type | default | options | notes |
|---|---|---|---|---|
| CE contract symbol (e.g. NSE:NIFTY261027C24500) (`i_ceSym`) | symbol | `""` |  | Empty = OPTION DATA UNAVAILABLE. Pine cannot scan the chain or pick a contract for you. |
| PE contract symbol (`i_peSym`) | symbol | `""` |  |  |
| Premium EMA fast (`i_prEmaF`) | int | `9` |  |  |
| Premium EMA slow (`i_prEmaS`) | int | `21` |  |  |
| Premium stop distance (x premium ATR) (`i_prAtrM`) | float | `1.0` |  |  |

## 10 Evidence weights (signed evidence index, NOT a probability)

| input | type | default | options | notes |
|---|---|---|---|---|
| Weight: higher-timeframe agreement (`w_htf`) | float | `2.0` |  |  |
| Weight: gap scenario (`w_gap`) | float | `1.0` |  |  |
| Weight: position vs previous-day / OI levels (`w_lvl`) | float | `1.5` |  |  |
| Weight: opening-candle confirmation (`w_open`) | float | `1.5` |  |  |
| Weight: BOS / CHoCH (`w_struct`) | float | `1.5` |  |  |
| Weight: liquidity sweep / failed break (`w_sweep`) | float | `1.0` |  |  |
| Weight: volume confirmation (needs volume) (`w_volume`) | float | `1.0` |  |  |
| Weight: momentum (RSI) (`w_mom`) | float | `1.0` |  |  |
| Weight: VWAP/TWAP side (`w_vwap`) | float | `1.0` |  |  |
| Weight: option-premium confirmation (needs CE+PE) (`w_prem`) | float | `1.5` |  |  |
| Weight: actual OI/IV evidence (manual selector below) (`w_oi`) | float | `1.0` |  |  |
| Actual OI/IV evidence you have verified (`i_oiEvid`) | string | `"None"` | None, Bullish, Bearish | Only set this if you have REAL option-chain OI/IV data pointing one way. 'None' disables the component. Rising OI alone is not treated as direction. |
| Entry: |evidence index| >= (`i_entryThr`) | float | `0.35` |  |  |
| Entry: minimum components with non-zero evidence (`i_minComp`) | int | `4` |  |  |

## 11 Entry / stop / targets / risk (index points; one position at a time, no averaging, no martingale)

| input | type | default | options | notes |
|---|---|---|---|---|
| Stop buffer beyond structure (x ATR) (`i_slBuf`) | float | `0.30` |  |  |
| Reject setup if risk > x ATR (`i_maxRisk`) | float | `3.0` |  |  |
| Target 1 (R) (`i_rr1`) | float | `1.0` |  |  |
| Target 2 (R) (`i_rr2`) | float | `2.0` |  |  |
| Target 3 (R) (`i_rr3`) | float | `3.0` |  |  |
| Exit % at T1 (`i_q1`) | float | `40` |  |  |
| Exit % at T2 (`i_q2`) | float | `30` |  | T3 gets the rest. Use a quantity divisible by 10 so partial exits are whole units. |
| Minimum distance to nearest opposing level (R) (`i_minRR`) | float | `1.0` |  | If the nearest opposing level (zone / previous-day extreme) is closer than this many R, the setup is skipped. |
| After T1 move stop to entry; after T2 to T1 (`i_trailBE`) | bool | `true` |  |  |
| Position sizing (`i_sizeMode`) | string | `"Fixed quantity"` | Fixed quantity, Risk amount per trade |  |
| Fixed quantity (index units; use multiples of 10) (`i_qty`) | float | `10` |  |  |
| Risk amount per trade (account currency; qty = amount / risk points, floored to 10s) (`i_riskAmt`) | float | `5000` |  |  |
| Exit if opposite evidence confirms (opposite CHoCH or opposite index beyond threshold) (`i_flipExit`) | bool | `true` |  |  |
| Max trades per session (`i_maxTrades`) | int | `2` |  |  |
| Max session loss (account currency; realised + open P&L; halts and flattens) (`i_maxLoss`) | float | `1500` |  |  |
| Cooldown after a loss (bars) (`i_cooldown`) | int | `6` |  |  |
| No entries before the opening-wait window ends (`i_noEarly`) | bool | `true` |  |  |
| No new entries from (HHMM, exchange time) (`i_lateCut`) | string | `"1445"` |  |  |
| Flatten at (HHMM, exchange time) (`i_squareOff`) | string | `"1515"` |  |  |
| No-trade if a bar's range > x ATR (abnormal volatility) (`i_spikeAtr`) | float | `3.0` |  |  |
| No-trade on EXCEPTIONAL gap sessions (`i_blockExc`) | bool | `true` |  |  |

## 12 Probability tracker (running, in-sample, expanding window)

| input | type | default | options | notes |
|---|---|---|---|---|
| Minimum resolved virtual signals per side before any empirical rate is shown (`i_minSamples`) | int | `50` |  |  |
| Outcome horizon (bars): target 1 before stop within N bars (`i_probHorizon`) | int | `24` |  |  |

## 14 Entry timing - lower-timeframe (e.g. 3-minute) refinement

| input | type | default | options | notes |
|---|---|---|---|---|
| Entry refinement mode (`i_refine`) | string | `"Off"` | Off, Micro-breakout, Pullback then resume | Needs a chart BELOW 5 minutes (3-minute recommended). The 5-minute engine still decides the bias (it is read from the last COMPLETED 5-minute bar); the entry then waits for a lower-timeframe trigger. Off = enter at the next bar open after the 5-minute signal. On a 5-minute chart this setting has no effect. |
| Chart timeframe handling (leave on Auto) (`i_hostMode`) | string | `"Auto"` | Auto, 5-minute chart, Chart below 5 minutes | Auto reads the chart timeframe with timeframe.main_period. If a platform reports it wrongly the timing guard (dashboard: ENGINE TIMING MISMATCH) blocks all entries; then force the correct choice here. |
| Bars to wait for the trigger (chart bars) (`i_refWin`) | int | `4` |  | Armed setups that are not confirmed within this many chart bars (or break the stop, or are contradicted by the next 5-minute signal) are dropped - never chased. |

## 13 Dashboard

| input | type | default | options | notes |
|---|---|---|---|---|
| Show dashboard (`i_dashOn`) | bool | `true` |  |  |
| Position (`i_dashPos`) | string | `"Top right"` | Top left, Top center, Top right, Middle left, Middle center, Middle right, Bottom left, Bottom center, Bottom right |  |
| Text size (`i_dashSize`) | string | `"Small"` | Tiny, Small, Normal, Large |  |
| Compact mode (fewer rows - good for mobile) (`i_compact`) | bool | `false` |  |  |
| Bullish text (`i_cBull`) | color | `#26a69a` |  |  |
| Bearish text (`i_cBear`) | color | `#ef5350` |  |  |
| Neutral text (`i_cNeu`) | color | `#b0b6c2` |  |  |
| Header text (`i_cHead`) | color | `#ffffff` |  |  |
| General text (`i_cGen`) | color | `#d1d4dc` |  |  |
| Background (`i_cBg`) | color | `#131722` |  |  |
| Background transparency (`i_bgTransp`) | int | `15` |  |  |
| Border (`i_cBorder`) | color | `#434651` |  |  |
| Border width (`i_borderW`) | int | `1` |  |  |
| Show backtest statistics panel (bottom left) (`i_showStats`) | bool | `true` |  |  |
| Plot previous-day levels, opening range and OI zones (`i_showLvls`) | bool | `true` |  |  |
| Chart labels for events (`i_showLbl`) | bool | `true` |  |  |
| Out-of-sample split date for the statistics panel (`i_oosDate`) | time | `timestamp("1 Jan 2026 00:00 +0000")` |  |  |
