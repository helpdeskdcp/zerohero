# Option-Chain GitHub Audit + IDaddy Integration Plan

_2026-09-09. Audit of 7 public option-chain repos, a synthesis (A–F), and an
exact integration plan. **Do not modify existing IDaddy code. No live trading.
No API keys exposed. No GPL/copyleft copied without a compatibility check.**_

Method: inspected each repo's page, README, file tree and the key data/analytics
files via GitHub raw. No code copied.

---

## 1. PER-REPOSITORY AUDIT

Legend: ✅ present · ➖ absent · ⚠️ present but flawed · ❔ not determinable.

### 1. markov404/AngelOneOptionChainSmartApi
| attr | finding |
|---|---|
| language / framework | Python, no web framework (script + `components/` package) |
| data source | **Angel One SmartAPI** — instrument list + SmartWebSocketV2 **mode-3 SnapQuote** |
| Angel One compat | ✅ exclusively |
| NSE compat | ➖ (via Angel only) |
| NIFTY / BANKNIFTY / SENSEX | NIFTY ✅ · BANKNIFTY ✅ · SENSEX ➖ (BSE not handled) |
| option-chain structure | `{name:{symbol:{CE|PE: snapquote}}}` nested dict; unpaired strikes dropped |
| CE/PE handling | ✅ suffix parse of the tradingsymbol (`NIFTY09MAR2318050CE`) |
| expiry handling | ✅ parsed from tradingsymbol string |
| strike handling | ✅ parsed from tradingsymbol string |
| OI | ✅ raw | change-OI | ➖ | volume | ✅ | IV | ➖ | Greeks | ➖ | PCR | ➖ | Max Pain | ➖ |
| live refresh | polling every **3 min** |
| WebSocket | ✅ SmartWebSocketV2 mode 3 |
| architecture quality | modular (`user` / `smart_api_web_sock_v2` / `output_processor` / `utlis`) but weak: `except BaseException: pass`, no logging, **text-file** output, manual string concat |
| last meaningful maintenance | **Feb 2023** — outdated |
| outdated API deps | `smartapi-python` (old), `python-schema` |
| reusable modules | the tradingsymbol→(underlying,expiry,strike,CE/PE) parser; CE/PE pairing + drop-unpaired **concept** |
| license | **MIT** — the only repo we may copy verbatim (with attribution) |
| security risks | `settings.txt` template stores `API_KEY:…;PASSWORD:…;OTP_CODE:…` in plaintext — storing an OTP/TOTP value in a file is a red flag |

### 2. prasanna-venkatesh-m/NSE-OptionChain-Dashboard
| attr | finding |
|---|---|
| language / framework | **Next.js 15 + TypeScript + React** (app router, Tailwind) |
| data source | **NSE India `option-chain-v3`** (`www.nseindia.com/api/option-chain-v3?type=Indices&symbol=&expiry=`) proxied via `src/app/api/nse/route.ts` |
| Angel One compat | ➖ |
| NSE compat | ✅ direct v3 API, **raw JSON proxy** (no PCR/MaxPain/Greeks compute) |
| NIFTY / BANKNIFTY / SENSEX | NIFTY ✅ (default), BANKNIFTY/FINNIFTY via `symbol` param; SENSEX ➖ (NSE only) |
| option-chain structure | raw NSE JSON → React `context` + `components` table |
| CE/PE handling | ✅ from NSE payload (`CE`/`PE` per strike) |
| expiry handling | query param, hardcoded default `05-May-2026` |
| strike handling | from NSE response |
| OI | ✅ | change-OI | ✅ (`changeinOI` from NSE) | volume | ✅ | IV | ✅ (NSE gives `impliedVolatility`) | Greeks | ➖ (NSE has none) | PCR | ➖ compute | Max Pain | ➖ |
| live refresh | `cache:'no-store'` + client timestamp cache-buster; client interval likely |
| WebSocket | ➖ |
| architecture quality | **clean, modern** — `services/` `context/` `components/` separation |
| last meaningful maintenance | 2026 project (25 commits, 2026 default expiry) — recent |
| outdated API deps | Next 15 / React 19 — current |
| reusable modules | the **NSE `option-chain-v3` URL + header approach** (a *fact*), and the **React chain-table layout** as a design reference |
| license | **NONE** → all-rights-reserved; **code cannot be copied**, only referenced |
| security risks | proxy has **no cookie jar** → NSE 403s in production; NSE ToS / rate-limit risk |

### 3. Ashwin2k3/Option-chain-dashboard
| attr | finding |
|---|---|
| language / framework | Python + **Streamlit** |
| data source | **NSE direct** via `requests` to the NSE option-chain API |
| Angel One | ➖ | NSE | ✅ |
| NIFTY / BANKNIFTY / SENSEX | NIFTY ✅; `Option_chain_multiIndexed.py` hints BANKNIFTY; SENSEX ➖ |
| option-chain structure | CE/PE per strike; **ATM auto-detect** (closest strike to spot); shows 10 ATM + 10 OTM |
| CE/PE | ✅ | expiry | ❔ | strike | ✅ ATM ladder |
| OI | ✅ | change-OI | ✅ (NSE) | volume | ✅ | IV | ❔ (in payload) | Greeks | ➖ | **PCR** | ✅ computed | Max Pain | ➖ |
| live refresh | **`streamlit_autorefresh`**, user-set 1–60 min |
| WebSocket | ➖ |
| architecture quality | small (2 scripts), fine for a demo, not production |
| last meaningful maintenance | 14 commits, date weak |
| outdated deps | `requests` / `streamlit` / `pandas` — current |
| reusable modules | **ATM-ladder selection** + **PCR compute** (trivial, reimplement) |
| license | **NONE** → reference only |
| security risks | NSE scraping fragility (no cookie handling → breaks); no keys |

### 4. Danish2op/OptionChainDashboard
| attr | finding |
|---|---|
| language / framework | Python + **Streamlit** |
| data source | **Hugging Face dataset** `Danish-for-TFU/Nifty-Option-Data-1min` (1-min **historical** parquet) + expiry CSV; `HF_TOKEN` from secrets; synthetic demo fallback |
| Angel One | ➖ | NSE | ➖ (uses a 3rd party's pre-scraped set) |
| NIFTY / BANKNIFTY / SENSEX | NIFTY only |
| option-chain structure | aggregate by strike & type from parquet |
| CE/PE | ✅ | expiry | ✅ from CSV | strike | ✅ |
| OI | ✅ (lakhs) | **change-OI + change-OI %** | ✅ **vs a chosen baseline time** | volume | ✅ | IV | ➖ | Greeks | ➖ | **PCR** | ✅ (`pe_oi/ce_oi`) | Max Pain | ➖ |
| live refresh | ➖ (historical replay; 1–2h TTL cache) |
| WebSocket | ➖ |
| architecture quality | single `app.py` (v3) — ok |
| last meaningful maintenance | 4 commits |
| outdated deps | `huggingface_hub`, `pyarrow`, `streamlit`, `pandas` — current |
| reusable modules | the **time-baseline ΔOI** interaction (pick "then", compare OI now vs then) |
| license | **NONE** → reference only |
| security risks | `HF_TOKEN` via secrets (good); ⚠️ **synthetic-data demo mode** — must never enter a research pipeline |

### 5. TaylorMadeData/OptionsDashboard
| attr | finding |
|---|---|
| language / framework | **Power BI (.pbix)** + screenshots — not code |
| data source | manual **Think-or-Swim** CSV exports, **US options (SPY)** |
| Angel One | ➖ | NSE | ➖ | Indian indices | ➖ |
| everything else | N/A — a BI report, no reusable modules |
| license | **NONE** |
| verdict | **zero reusability** (wrong market, wrong platform, no code) |

### 6. PyStatIQ-Lab/OptionsChain-Analysis-dashboard
| attr | finding |
|---|---|
| language / framework | Python + **Streamlit** |
| data source | **Upstox** `service.upstox.com/option-analytics-tool/**open**/v1/strategy-chains?assetKey=&strategyChainType=PC_CHAIN&expiry=` — **keyless / public** (`/open/` path, `accept: application/json` only) |
| Angel One | ➖ | NSE | indirect (Upstox aggregates NSE) |
| NIFTY / BANKNIFTY / SENSEX | NIFTY ✅ · BANKNIFTY ✅ · SENSEX ➖ |
| option-chain structure | `strategyChainData.strikeMap` → per strike `callOptionData`/`putOptionData` → `marketData{ltp,bid,ask,volume,oi,prevOi}` + `analytics{iv,delta,gamma,theta,vega}` |
| CE/PE | ✅ | expiry | user date-picker (`dd-mm-yyyy`) | strike | ✅ from `strikeMap` |
| OI | ✅ | **change-OI** | ✅ (`oi - prevOi`) | volume | ✅ | **IV** | ✅ (from API) | **Greeks** | ✅ δγθν (from API) | **PCR** | ✅ (from API) | **Max Pain** | ⚠️ **computed but WRONG** (min total OI, not writer-payout minimisation) | **IV skew** | ✅ (`call_iv − put_iv`) |
| live refresh | `@st.cache_data(ttl=300)` = 5 min |
| WebSocket | ➖ |
| architecture quality | single `app.py`, 6 commits, functional not modular |
| last meaningful maintenance | recent-ish |
| outdated deps | `streamlit` / `requests` / `pandas` — current |
| reusable modules | **the keyless Upstox open endpoint + its JSON shape** (facts) as a free Greeks+IV+PCR source; the `strikeMap`→CE/PE mapping (reimplement) |
| license | **NONE** → code not copyable; endpoint/shape are facts |
| security risks | undocumented public endpoint — may change / rate-limit / ToS-gray; no auth to leak |

### 7. JayeshSRathod/coa-dashboard
| attr | finding |
|---|---|
| language / framework | Python + **Streamlit** (+ a local-only React "CQRP" paper-trading workstation) |
| data source | **Fyers API** (primary) → **Dhan API** (fallback, ₹499/mo) → manual entry → **simulated** chain (`engine/data_feed.py`) |
| Angel One | ➖ | NSE | indirect (Fyers/Dhan) |
| NIFTY / BANKNIFTY / SENSEX | "all four indices" (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY); SENSEX unclear |
| option-chain structure | CE/PE per strike, near-the-money ladder, volume-change shading, **OI "walls" = support/resistance** |
| CE/PE | ✅ | expiry | ❔ | strike | ✅ NTM ladder |
| OI | ✅ | **volume + volume-change** | ✅ | IV | ➖ | Greeks | ➖ | PCR | ➖ explicit | Max Pain | ➖ |
| extras | scenario-based **trade logging + win-rate**, "risk mode", momentum leaderboard (simulated) |
| live refresh | polling, interval unspecified |
| WebSocket | ➖ |
| architecture quality | **best of the 7** — 118 commits, an `engine/` layer, multi-source fallback, secrets hygiene ("never store a broker PIN", refresh tokens, `secrets.toml` gitignored) |
| last meaningful maintenance | **most active** of the 7 |
| outdated deps | Fyers / Dhan SDKs — current |
| reusable modules | **multi-source fallback pattern** · **OI support/resistance wall detection** · **secrets hygiene** · **scenario-log + win-rate** structure |
| license | **NONE** → reference only |
| security risks | good hygiene, but **wrong broker** (Fyers/Dhan, not Angel One); Dhan fallback is **paid**; simulated-data generator |

---

## 2. SYNTHESIS (A–F)

### A. BEST DATA LAYER
- **Live, our broker:** none of the 7 beats what IDaddy already has —
  `app/connectors/angelone.py` + `app/l2capture/snapquote.py` (mode-3) +
  `app/instruments.resolve_nse_option` + the captured `quote_snapshots`
  (OPTION: bid/ask/oi/oi_change/volume/depth) and `option_greeks`
  (δγθν/iv) tables. markov404 is the only Angel-One reference but it is
  Feb-2023, text-file output, weak error handling.
- **Free independent source (new):** **PyStatIQ's keyless Upstox
  `option-analytics-tool/open/v1` endpoint** — the *only* source in the set
  that hands back **pre-computed Greeks + IV + PCR** for NIFTY/BANKNIFTY with
  **no auth**. Ideal as a cross-check / fallback (our Angel greek feed was
  stale for much of the last week).
- **Fallback with no greeks:** prasanna's **NSE `option-chain-v3`** (raw OI /
  ΔOI / IV / volume) — but needs a cookie-priming step the repo lacks.

### B. BEST UI / DASHBOARD
- **prasanna-venkatesh-m** — the only React/Next.js one; clean
  `services / context / components` separation; matches IDaddy's HTML/JS
  frontend far better than the four Streamlit apps. **Layout reference only**
  (license = none → we build our own table).
- Runner-up for *interaction* ideas: **Danish2op**'s time-baseline ΔOI toggle.

### C. BEST OPTION-CHAIN ANALYTICS
- **PyStatIQ** — OI-change, PCR, **IV skew**, per-strike Greeks table, a Max
  Pain attempt. **Its Max Pain is mathematically wrong** (min-OI, not writer
  payout) → we implement true Max Pain.
- **coa-dashboard** — **OI-wall support/resistance** detection (IDaddy already
  has a variant in `sr_engine`; cross-check).
- **Danish2op** — ΔOI vs a chosen baseline time.

### D. BEST ANGEL ONE INTEGRATION
- **markov404** (the only one) — instrument-master → NFO OPTIDX filter,
  tradingsymbol parse, mode-3 SnapQuote, CE/PE pairing. But outdated and
  fragile. **IDaddy's own Angel stack is more current and robust** → markov404
  is a *reference for the shape*, not an adoption.

### E. CODE WE SHOULD REUSE
_(All pattern/fact-level. Only markov404 (MIT) is verbatim-eligible, with
attribution; everything else is re-implemented from public facts.)_
| from | what | how |
|---|---|---|
| markov404 (MIT) | tradingsymbol → (underlying, expiry, strike, CE/PE) parser; CE/PE pairing + drop-unpaired | cherry-pick edge cases into our `instruments` / new `optionchain` module |
| PyStatIQ | the **Upstox `open/v1/strategy-chains` endpoint + JSON shape** | new `sources/upstox_open.py` — a free Greeks/IV/PCR source; mapping reimplemented |
| prasanna | the **NSE `option-chain-v3` URL + browser headers** | new `sources/nse_v3.py` — reimplemented **with a cookie-prime GET first** |
| Danish2op | time-baseline **ΔOI** interaction | a dashboard toggle: OI now vs OI at a chosen earlier snapshot |
| coa-dashboard | multi-source **fallback chain** + secrets hygiene | `chain.py` source ordering: Angel(captured) → Upstox(open) → NSE(v3); secrets already handled by IDaddy `.env` |

### F. CODE WE SHOULD NOT REUSE
| item | why |
|---|---|
| any verbatim code from repos **2–7** | **no LICENSE = all rights reserved**; reference/reimplement only |
| PyStatIQ Max Pain | wrong algorithm (min-OI ≠ writer-payout) |
| markov404 error handling / text-file output / 3-min polling / `settings.txt` OTP-in-plaintext | anti-patterns; conflicts with IDaddy logging + capture design + secret hygiene |
| TaylorMadeData (all) | US options, Power BI, no code |
| coa-dashboard Fyers/Dhan SDK coupling + simulated-chain generator | wrong broker, Dhan is paid, **synthetic data must never enter a research pipeline** |
| prasanna NSE proxy *as-is* | no cookie jar → 403 in prod; NSE ToS/rate-limit risk if used as a *primary* live source |
| `nsepython` / raw NSE scraping as a **primary** live feed | fragile, ToS-gray — **fallback only** |

---

## 3. INTEGRATION PLAN

```
GitHub components (reference/facts only)
        ↓
IDaddy Option Chain Data Layer          app/optionchain/  (NEW, read-only)
        ↓
OI / IV / Greeks / PCR / Max Pain       app/optionchain/analytics.py
        ↓
Option Structure Engine                 app/optionchain/structure.py
        ↓
ANN (separate confirmation layer)       reuse hcs.adaptive_mc / tri_compare.ann_layer
        ↓
Signal Qualification                    app/optionchain/qualify.py
        ↓
IDaddy Dashboard                        GET /api/optionchain/{underlying} + a new frontend view
```

**Nothing existing is modified.** Every item below is a NEW file under
`backend/app/optionchain/` (or a NEW route/view). No live trading, no key
exposure (Upstox + NSE are keyless; Angel uses the existing env-based connector).

### Layer 1 — IDaddy Option Chain Data Layer  `app/optionchain/`

| new file | purpose | source / reference | reuses (IDaddy) |
|---|---|---|---|
| `chain.py` | the canonical `OptionChain` dataclass: `{underlying, spot, expiry, atm_strike, rows:[{strike, ce:{oi,oi_chg,vol,iv,ltp,bid,ask,delta,gamma,theta,vega}, pe:{…}}], source, quality, ts}` — **one shape, source-agnostic**; per-source capability flags (`has_greeks`, `has_iv`, `cadence_sec`) | — | — |
| `sources/angelone_chain.py` | **PRIMARY.** Assemble the chain from data IDaddy already captures: `market_history.db.quote_snapshots` (OPTION rows) + `option_greeks` (δγθν/iv), strikes/expiry/token via `instruments.resolve_nse_option`. | markov404 `output_processor` (CE/PE pairing concept, MIT) | `app/connectors/angelone.py`, `app/instruments.py`, `app/histcap` store, `app/l2capture` |
| `sources/upstox_open.py` | **SECONDARY / cross-check.** GET the keyless `service.upstox.com/option-analytics-tool/open/v1/strategy-chains` → per-strike CE/PE with **pre-computed Greeks + IV + PCR** (NIFTY, BANKNIFTY). Timeout + 5-min cache + graceful degrade. | PyStatIQ (endpoint + JSON shape are facts; mapping reimplemented) | — |
| `sources/nse_v3.py` | **TERTIARY fallback (no greeks).** GET `nseindia.com` once to prime cookies, then `api/option-chain-v3?type=Indices&symbol=&expiry=` with a browser UA; raw OI / ΔOI / IV / volume. | prasanna (URL + headers are facts; **cookie-prime added**) | — |
| `resolve.py` | source ordering + failover: Angel(captured) → Upstox(open) → NSE(v3); merge greeks from whichever source has them onto the primary rows | coa-dashboard (fallback-chain pattern) | — |
| `quality.py` | DQ score per fetch: ATM-window strike coverage %, stale %, crossed quotes, `oi==0` band, source freshness — mirrors `l2capture.health` | — | `l2capture.health` philosophy |

### Layer 2 — OI / IV / Greeks / PCR / Max Pain  `app/optionchain/analytics.py`

| function | definition | reference |
|---|---|---|
| `pcr(chain)` | PCR by OI and by volume; ATM-window (±N strikes) and full-chain | Ashwin2k3 / PyStatIQ |
| `max_pain(chain)` | **TRUE** max pain: for candidate strike `K`, writer payout `= Σ_ce max(0, spot_at_expiry−strike)·ce_oi + Σ_pe max(0, strike−spot)·pe_oi` evaluated with `spot_at_expiry = K`; `max_pain = argmin_K payout(K)`. **NOT** min-OI. | corrects PyStatIQ |
| `iv_skew(chain)` / `iv_smile(chain)` | `call_iv − put_iv` per strike; ATM IV; 25-delta skew if greeks present | PyStatIQ |
| `oi_walls(chain)` | top-k CE OI = resistance, top-k PE OI = support; ΔOI-weighted | coa-dashboard; cross-check `sr_engine` |
| `oi_change_vs_baseline(now, baseline)` | per-strike ΔOI and ΔOI% vs an earlier `OptionChain` snapshot | Danish2op |
| `gex(chain)` | Σ `gamma·oi·spot²·sign(ce−pe)`; flip strike; pin strike | reuse `app/greeks_engine/` if present |
| `bs.py` (only if needed) | Black–Scholes δγθν + IV solve — **fallback** when a source lacks greeks | reuse `app/engines/option_engine` BS bits if they exist |

### Layer 3 — Option Structure Engine  `app/optionchain/structure.py`
Consumes `OptionChain` + analytics → a per-underlying `OptionStructureState`
(NOT a trade signal): max-pain magnet direction & distance, PCR regime
(OI + volume), OI-wall support/resistance levels, IV-skew bias, ATM-IV vs
realised-vol, GEX regime. Deterministic, method-tagged, capability-aware
(degrades cleanly when greeks are absent).

### Layer 4 — ANN  (reuse, do not rebuild)
`tri_compare.ann_layer.AnnConfirm` (wraps `hcs.adaptive.OnlineLogit` +
`hcs.adaptive_mc.Isotonic`) as a **separate confirmation layer** fed the
`OptionStructureState` features. Fit TRAIN-only per walk-forward fold,
leak-free, report engine-only (A) vs engine+ANN (B). **Kept only on a
demonstrated OOS improvement** — same discipline as `tri_compare` /
`trend_swing`. ANN cannot rescue a NO-GO.

### Layer 5 — Signal Qualification  `app/optionchain/qualify.py`
Deterministic rule combining `{OptionStructureState, existing RK/HCS signal
for the underlying, ANN p_win, DQ score}` → `QUALIFIED | WATCH | NO_TRADE`
with configurable thresholds; **no blind score sum** — each input is an
independent gate (mirrors `tri_compare` hybrid + the SIGNAL≠ENTRY discipline).
Research-only output; not wired to any order path.

### Layer 6 — IDaddy Dashboard
- `GET /api/optionchain/{underlying}?expiry=AUTO` → `{chain, analytics,
  structure, quality, qualification}` (read-only; no new framework — a router
  under the existing FastAPI app).
- New frontend view in `backend/frontend/`: one row per strike
  (`CE cols | strike | PE cols`), ATM highlighted; columns OI / ΔOI / Vol / IV
  / δ γ θ ν; header strip: spot, **PCR**, **Max Pain**, **IV skew**, GEX
  regime, DQ score, source. A right-panel OI-profile bar chart + walls, and a
  **time-baseline ΔOI toggle** (Danish2op). Layout inspired by prasanna's
  Next.js table, rebuilt in IDaddy's stack.

---

## 4. LICENSE / SAFETY SUMMARY

| repo | license | verbatim copy allowed? |
|---|---|---|
| markov404/AngelOneOptionChainSmartApi | **MIT** | ✅ with attribution |
| prasanna / Ashwin2k3 / Danish2op / TaylorMadeData / PyStatIQ / coa-dashboard | **none** | ❌ — reference / reimplement from public facts only |

- **No GPL/copyleft** was found in any of the 7, so there is no copyleft
  contamination risk — but "no license" ≠ permissive: repos 2–7 are
  all-rights-reserved and must be treated as read-only references.
- Endpoint URLs, request headers, and JSON field names are **facts**, not
  copyrightable expression — safe to use.
- No API keys are introduced: Upstox `open/v1` and NSE `v3` are keyless;
  Angel One stays on the existing `.env` connector.
- Nothing here touches the order path or `live_trading`.
