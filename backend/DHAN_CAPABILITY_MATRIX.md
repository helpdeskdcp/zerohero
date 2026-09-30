# Dhan capability matrix (verified live 2026-09-30, not assumed)

Class: **A** officially available historically · **B** live/current only · **C** reconstructable mathematically · **D** cannot be reconstructed reliably.
Probes were real API calls with the stored server-side session; no credentials appear in this file.

## Endpoints tested

| Endpoint | Result |
|---|---|
| `POST /v2/optionchain` (live chain) | NSE/BSE index: 6 underlyings OK. **MCX CRUDEOIL (207 strikes) and NATURALGAS (90 strikes) also OK** — underlying = front-month FUTURE security_id, `UnderlyingSeg=MCX_COMM`. |
| `POST /v2/optionchain/expirylist` | Real expiries for indices and MCX (CRUDEOIL 2026-10-15/11-17/12-16; NATURALGAS 2026-10-23/11-20/12-23). |
| `POST /v2/charts/rollingoption` (expired options, ATM-relative strikes) | NIFTY: 1-minute series returned back to at least **2022-06** (probed 2022, 2024, 2025, 2026). Fields: open, high, low, close, **iv, oi, volume, strike, spot, timestamp**, CE and PE. |
| Same, MCX_COMM / OPTFUT (CRUDEOIL, NATURALGAS, MONTH and WEEK) | HTTP 200 but **empty arrays**; `OPTCOM` rejected (DH-905). Recorded as **UNSUPPORTED_BY_DHAN** for historical expired MCX options (tried documented shapes only; not proof of permanent absence). |
| `/v2/charts/historical`, `/v2/charts/intraday` | OHLCV for option contracts; **no OI, no Greeks** (earlier probe). |

## Field classification

| Field | Indices (NIFTY etc.) | MCX (CRUDEOIL/NATURALGAS) |
|---|---|---|
| Live LTP, bid, ask, bid/ask qty, OI, previous OI, volume, IV, Delta/Gamma/Theta/Vega, security_id | **B** (live chain only, no timestamp parameter; we accumulate forward via DHAN_CHAIN / DHAN_OPTION_GREEK) | **B** (same fields present in the live chain) |
| Historical option OHLC, volume | **A** (rollingoption, 1m, years) | **UNSUPPORTED_BY_DHAN** (empty) for expired; contract-level `/charts/intraday` may work for live contracts |
| Historical IV | **A** (direct, rollingoption) | UNSUPPORTED_BY_DHAN (expired) |
| Historical OI | **A** (direct, rollingoption) | UNSUPPORTED_BY_DHAN (expired) |
| Historical OI change | **C** (difference of consecutive direct OI values; label derived) | — |
| Historical spot / underlying | **A** (`spot` in rollingoption) | — |
| Historical Delta/Gamma/Theta/Vega | **C** (from direct IV + spot + strike + time-to-expiry + rate; must be tagged `RECONSTRUCTED`, model + inputs stored) | **C only with a commodity model (Black-76 on the future)** and only if IV is available; not available historically here, so **D** for Dhan-sourced history |
| Historical bid/ask/bid qty/ask qty/spread | **D** (no historical depth anywhere) | **D** |
| Historical VWAP | **C** only from bar OHLCV approximations (not a true tick VWAP) | — |

## Rules for downstream code
- `greek_source ∈ {DIRECT, RECONSTRUCTED, UNAVAILABLE}`; never write a reconstructed value into a column that implies a broker value.
- Historical bid/ask is never estimated; spread-dependent logic must degrade to UNAVAILABLE on historical rows.
- Rolling expired data is strike-relative (ATM±n); mapping to absolute strikes uses the returned `strike` and `spot` fields per bar.
- MCX historical option research currently has **no Dhan source**; a second provider would be needed (design a provider interface before building it).
