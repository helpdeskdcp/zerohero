# Historical options + expiry-rule audit (2026-09-30)

Status vocabulary: VERIFIED (confirmed by broker/exchange data in this session) · RECONSTRUCTABLE (deterministic from verified inputs) · PARTIALLY VERIFIED · NOT VERIFIED · UNAVAILABLE.
No official exchange circular or official holiday calendar was available to this audit; nothing below is labelled EXCHANGE_CIRCULAR / EXCHANGE_CALENDAR.

## Evidence sources actually used
- **Upstox `/v2/expired-instruments/expiries`** (broker contract metadata): real historical expiry lists, 2024-10 → 2026-09 (NIFTY 105, BANKNIFTY 30, SENSEX 103, FINNIFTY 31). Saved: `app/hist_options/data/expiry_metadata_upstox.json`. MCX keys rejected by Upstox (Invalid Instrument key).
- **Dhan `/v2/optionchain/expirylist`** (current MCX contract expiries) + Dhan instrument master (futures expiries).
- **Dhan `/v2/charts/historical` daily index candles** → observed trading days (NIFTY 2022-01 → 2026-09, SENSEX 2024-01 → 2026-09; Dhan errored for SENSEX 2022–23). Saved: `data/observed_trading_days.json`. This is *observed*, not official: it includes special sessions (Diwali muhurat).
- Dhan docs (`instruments`, `expired-options-data`): rolling endpoint = index + stock options; strike ATM±10 for index, ±3 for other contracts; ≤30 days/request; 1/5/15/25/60-min intervals; up to 5 years; MCX not mentioned.

## Findings that contradict the task text (broker metadata wins)
| Task text | Broker metadata shows |
|---|---|
| SENSEX current weekly = Tuesday | Tuesday only 2025-01-07 → 2025-08-26. **Thursday from 2025-09-04 onward** (through 2026-09). Friday until 2025-01-03. |
| BANKNIFTY before revision = last Thursday | Wednesday weeklies existed until 2024-11-27; Dec-2024 monthly expired **Tue 2024-12-24**; last-Thursday monthlies 2025-01 → 2025-08; last-Tuesday from 2025-09. |
| NIFTY Thursday → Tuesday from Sep-2025 | **Confirmed**: last Thursday expiry 2025-08-28, first Tuesday 2025-09-02. Holiday shifts confirmed (2025-10-20, 2026-03-02, 2026-03-30, 2026-04-13). |

## Rule engine vs metadata (audit result)
NIFTY / BANKNIFTY / SENSEX: 232 broker expiries resolve VERIFIED. Rule-vs-metadata conflicts (metadata wins, both dates stored): NIFTY 2025-10-20 vs rule 2025-10-21; SENSEX 2024-10-31 vs rule 2024-11-01 — both are Diwali **muhurat** days, which an observed-days calendar counts as open. BANKNIFTY 2024-12 has no rule version (metadata only). Apr/May 2025 "mismatch" was a month-boundary artifact (1-May holiday → 30-Apr), handled by neighbour-month comparison.

## Capability matrix
| Instrument | Exch | Weekly | Monthly / Qtr / Half-yr | Current expiry rule | Historical rule versions | Holiday rule | Dhan hist OHLC/OI/IV/spot/strike | OI change | Hist Greeks / model | Calendar | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| NIFTY | NSE | yes | metadata only for qtr/half-yr | Tue (from 2025-09) | Thu ≤2025-08; Tue after (metadata-confirmed 2024-10 →) | previous trading day (metadata-confirmed) | 1m, back to ≥2022, IV/OI direct | DERIVED | RECONSTRUCTED, BS carry; expiry check: median price error 4.2% vs 68–97% with a wrong expiry | NSE observed days 2022→ | VERIFIED 2024-10→; NOT VERIFIED before (rule = task text only → UNAVAILABLE by default) |
| BANKNIFTY | NSE | **no weekly from 2024-12** (Wed weeklies until 2024-11) | monthly | last Tue (from 2025-09) | Wed weekly ≤2024-11; Tue 2024-12-24 (metadata); last Thu 2025-01→08; last Tue 2025-09→ | previous trading day (metadata-confirmed 2026-03-30) | same endpoint (not separately sampled) | DERIVED | RECONSTRUCTED | NSE observed | PARTIALLY VERIFIED |
| SENSEX | BSE | yes | metadata | **Thu (from 2025-09-04)** | Fri ≤2025-01-03; Tue 2025-01-07→08; Thu 2025-09→ | previous trading day (Wed shifts seen) | Dhan BSE_FNO rolling: **not sampled this session** | DERIVED | RECONSTRUCTABLE | BSE observed 2024→ (2022–23 UNAVAILABLE) | VERIFIED expiries 2024-10→; hist data NOT VERIFIED |
| NATURALGAS | MCX | no | monthly | option = futures expiry − 2 MCX business days | not reconstructable historically | needs MCX calendar (none available) | expired options: **empty** | — | Black-76 available for live values only | none | current expiries VERIFIED (3/3); history UNAVAILABLE |
| NATGASMINI | MCX | no | monthly | same rule; own contracts (own security ids/OI verified) | as above | as above | empty/not sampled | — | — | none | current VERIFIED via Dhan list; history UNAVAILABLE |
| CRUDEOIL | MCX | no | monthly | futures expiry − 2 MCX business days | as above | as above | empty | — | — | none | current VERIFIED (3/3); history UNAVAILABLE |
| CRUDEOILM | MCX | no | monthly | same; own contracts | as above | as above | empty/not sampled | — | — | none | current VERIFIED via Dhan list; history UNAVAILABLE |

MCX "−2 business days" checked against 6 live Dhan contracts (NG 27-Oct→23-Oct, 24-Nov→20-Nov, 28-Dec→23-Dec; CRUDE 19-Oct→15-Oct, 19-Nov→17-Nov, 18-Dec→16-Dec): all match; the NG December case only matches if 25-Dec is an MCX holiday, i.e. a real MCX calendar is required (not built: UNAVAILABLE without one).

## Not done / not verified
- Pre-2024-10 expiries (all instruments): only the task-text rules exist → UNAVAILABLE unless `allow_unverified_rules=True` (then RECONSTRUCTED, evidence USER_SPEC_UNVERIFIED).
- No official NSE/BSE/MCX calendar; MCX calendar absent.
- Quarterly / half-yearly NIFTY contracts not modelled (metadata lists do not tag contract type).
- Risk-free rate for Greeks is an input (not sourced); Dhan's own IV model/rate unknown, hence ~4% residual in the expiry check.
- Historical Dhan bars for BANKNIFTY / SENSEX not sampled; MCX minis' expired-option history not sampled (MCX main contracts returned empty).
