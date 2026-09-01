# Session Verification Plan (verification-only mode)

**Created:** 2026-09-02 01:10 IST · **Author:** verification pass · **Status:** plan only —
nothing here is implemented; items needing new code are marked **[needs go-ahead]**.

Guardrails for this whole plan:
- **Verification-only.** No feature, refactor, strategy/trading-logic, threshold, broker,
  kill-switch, or Phase B/C AI-module work.
- **No PASS on code inspection** — runtime / PAPER evidence required for every PASS.
- **No manufactured trades or evidence.** Long-running gaps get a count, nothing more.
- **LIVE stays hard-off** (`live_trading=false`, `paper_mode=true`).
- Any production change only if a **critical safety/regression** issue is found — stop and
  report first.

---

## Part 1 — Tonight's runtime verification (2026-09-02, after MCX close)

**Trigger:** IST past 23:40 on 2026-09-02 (NSE close 15:30 + MCX close 23:30 + daily
report 23:35). Currently **auto-scheduled** (see Part 5).

**Why it cannot run now:** it is 01:10 IST — overnight close. The 2026-09-02 session
(NSE 09:15–15:30, MCX 09:00–23:30) has not run. Zero open-market rows since the
00:26 IST deploy. All three checks are **INCONCLUSIVE** until the session produces rows.

### Check 1 — NIFTY future VWAP
| | |
|---|---|
| Query | `SELECT count(*), sum(vwap IS NOT NULL), group_concat(DISTINCT vwap_status) FROM live_market_snapshots WHERE symbol='NIFTY' AND date(ts)='2026-09-02' AND regime!='MARKET_CLOSED'` |
| + | pull one NIFTY row's `vwap`/`vwap_status`; check `/api/autoscalp/snapshots?symbol=NIFTY&limit=1` `sr_diag`/reason mentions "index future" |
| PASS | a good fraction of open NIFTY rows have `vwap != NULL` **and** `vwap_status='available'` **and** reason identifies the front-month index FUTURE |
| FAIL | open NIFTY rows still `vwap NULL` / `invalid_volume` during market hours |
| Regression guard | `SELECT symbol,count(*),sum(vwap IS NOT NULL) FROM ... WHERE symbol IN ('NATURALGAS','CRUDEOIL') AND date(ts)='2026-09-02' AND regime!='MARKET_CLOSED' GROUP BY symbol` — must stay ~fully populated `available` (Sep-1 baseline: NG 1546/1546, CRUDE 1545/1545) |

### Check 2 — feed-staleness fix
| | |
|---|---|
| Split | on 2026-09-02 open-market rows, group by `length(chain_json)>50` (chain_fetched) vs not; report `avg(feed_age_sec)` + `% > 12s` per group and per symbol |
| Blocks | `SELECT count(*) FROM live_market_snapshots WHERE date(ts)='2026-09-02' AND reason LIKE 'BLOCKED[stale feed%'` |
| **Sep-1 baseline** | chain_fetched avg **11.8s / 54% stale**; CRUDEOIL/chain **15.8s / 99%**; NATURALGAS/chain 9.2s / 28%; **`BLOCKED[stale feed]` = 49** |
| PASS | chain_fetched avg_age collapses toward single digits (esp. CRUDEOIL) **and** `BLOCKED[stale feed]` well below 49 |
| FAIL | chain_fetched avg_age still >12s / stale% unchanged → deeper WS-reader / CPU / broker-REST-latency investigation needed |
| Do NOT | suppress or reinterpret genuine stale data — the 12s threshold and the BLOCKED path are unchanged; the fix only moved work off the event loop |

### Check 3 — GEX v1a sanity
| | |
|---|---|
| Query | `SELECT symbol,count(*),sum(gex_flip IS NOT NULL),round(avg(gex_sigma),3),min(gex_sigma),max(gex_sigma),group_concat(DISTINCT gex_regime_sign) FROM live_market_snapshots WHERE date(ts)='2026-09-02' AND regime!='MARKET_CLOSED' GROUP BY symbol` |
| + | pull rows with `index_ltp,gex_flip,gex_pin`; check distance to spot; check `/api/autoscalp/snapshots` `sr_diag.gex.status='ok'` in market hours |
| PASS | `gex_flip`/`gex_pin` populated on open-market rows; `sr_diag.gex.status='ok'` during hours (thin_chain/no_vol acceptable only in the aggregator warm-up window) |
| FLAG (already seen offline on Sep-1 chains — confirm live) | **A:** MCX `gex_sigma` ~1.0 (NG 1.04, CRUDE 1.00) — above the 0.05–0.8 expectation; likely genuine MCX IV. **B:** `gex_flip`/`gex_pin` 4–6 ATR from spot with `strike_window=2` → `flip_in_range=False`. |
| Rule | **flag out-of-range values, do not silently correct.** Both flags are inert for v1a (diagnostic only). |

### Evidence / log locations
- `data/chanakya.db` → `live_market_snapshots`, `ai_paper_trades`
- `data/feed_staleness_probe.jsonl` — **stopped 2026-09-01T18:56:32Z** (see Part 2); DB `feed_age_sec` is the primary source
- `FEED_STALENESS_AUDIT.md` §B (method + baseline), `VWAP_AUDIT.md`, `GEX_SR_SPEC.md` §A1
- systemd `oi-dashboard.service`, PID 3644504, up 2026-09-02 00:25:57 IST, git HEAD `2d2a98b`

### Known non-blocker
- `tests/test_nse_mcx_pipeline_audit.py::test_dynamic_expiry_and_atm_resolver_uses_master`
  fails on 2026-09-02 — **date-driven fixture** (`01SEP2026` now expired; resolver correctly
  returns `08SEP2026`). Not a regression (clean tree fails identically; no deploy commit
  touches it). Fix = relative-date fixture. Cosmetic; **[needs go-ahead]** (test-only).

---

## Part 2 — Proposed: one durable read-only verification tool  **[needs go-ahead]**

**Problem it solves:** the `nohup` probes (`feed_staleness_probe.sh`, earlier `vwap_watch`,
`ev2.sh`, …) keep dying within minutes, and each verification is re-assembled ad-hoc from
shell one-liners. This is exactly the "retire ad-hoc monitoring, evidence-first" principle
from the earlier audit — not yet applied to the verification path itself.

**Proposal:** `backend/tools/verify_session.py` — a single **read-only** script:
- Args: `--date YYYY-MM-DD` (default = today IST).
- Runs Check 1 / 2 / 3 exactly as Part 1 specifies, plus the Sep-1 baseline, the
  long-running-gap counts, and a `pytest -q` tail.
- Prints a `PASS / FAIL / INCONCLUSIVE` table with observed vs baseline values and every
  flag spelled out (never auto-corrected).
- Writes `data/session_verify_<date>.md` (dated, committed) so each session's evidence is
  a durable artefact, not scrollback.
- **Zero** service interaction beyond `GET /api/autoscalp/*`; no writes to the DB; no
  config, broker, or process control.
- Optionally a `--watch-feed` mode that samples `feed_age_sec` from `/api/autoscalp/status`
  every N s into a JSONL **as a foreground run under a systemd `--user` timer or `tmux`**,
  not a bare `nohup` (which is what keeps dying).

**Not built.** Needs your go-ahead — it is new tooling even though it changes no product
behaviour. If you'd rather keep it as shell, the same logic can be a `data/verify_session.sh`.

**Interim (no go-ahead needed):** tonight's scheduled job already runs the Part 1 checks
inline from SQL — it does not depend on this tool.

---

## Part 3 — Decision points the verification will surface (no action until then)

1. **GEX FLAG A — MCX sigma band.** If live 2026-09-02 rows confirm NG/CRUDE `gex_sigma`
   routinely ~0.8–1.3: either (a) widen the "normal" band to ~0.05–1.5 for MCX in the
   spec, or (b) lower `iv_cap` for MCX. **Decide only after ≥5 sessions of `gex_sigma`
   data** — do not tune on one session.
2. **GEX FLAG B — flip/pin distance.** With `strike_window=2` the GEX levels sit >4·ATR
   out. Only matters for **v1b / A3** (when GEX becomes an S/R candidate and the
   `max_dist_atr` guard would suppress it). Options for later: widen the option window for
   the GEX calc only, or express the guard in strike-steps not ATR. **No change for v1a.**
3. **Feed fix — if it did NOT reduce staleness.** Escalate to: WS reader starvation under
   CPU, broker candle-REST latency, or `poll_sec`/`decide_every_sec` tuning. Investigation
   only; no threshold change.

---

## Part 4 — Gated roadmap (reference only — every item needs evidence + explicit approval)

| track | next gate |
|---|---|
| **GEX A2** | `gex_*` populated over ≥5 sessions + ≥~30 closed trades → measure if flip-proximity / `regime_sign` separates WIN/LOSS or helps the EV gate (method: `SNAPSHOT_DATA_AUDIT.md` §5) |
| **GEX A3** | only if A2 shows a measurable edge → enable `gex.enabled` for **NG/CRUDE only**, add candidates + `gex_backing` strength; resolve FLAG B first |
| **GEX A4** | NIFTY A/B with an explicit before/after PAPER comparison (frozen profile) |
| **Snapshot DQ-1** | MARKET_CLOSED heartbeat throttling (~61% of rows) — small `_evaluate` change, careful with report semantics |
| **Snapshot DQ-2** | advisory / `out_none` returns should carry the full `ctx` block (390 open NO_TRADE rows currently miss `atr`/S-R) |
| **Snapshot FIX-1** | `vwap_prox` weight redistribution when VWAP unavailable — needs NIFTY PAPER A/B (strength-affecting) |
| **NIFTY `max_hold_sec`** | ≥20 closed NIFTY AUTOSCALP trades with `risk_ref` (have **4**, 0 with `risk_ref`) → re-run `analyze_holdtime.py` |
| **`PRODUCTION_READINESS.md` §D/§E** | finalise once feed-staleness + NIFTY-VWAP checks land |
| **vibe Phase B** | vibe `/brief` sidecar (local Ollama, free) → advisory snapshot column — **separate spec, not started** |
| **vibe Phase C** | evidence-gated soft influence — only after B has data |

Long-running gap snapshot (2026-09-02 01:00 IST): closed AUTOSCALP trades —
NATURALGAS 21 (9W/7L/5F), NIFTY 4 (3W/1L, 0 `risk_ref`), CRUDEOIL 0.

---

## Part 5 — Schedule

**Armed:** a dynamic wakeup loop that re-checks `TZ=Asia/Kolkata date` each fire and
re-schedules until IST > 23:40 on 2026-09-02, then runs the Part 1 checks on that day's
rows, pushes a one-line PASS/FAIL/INCONCLUSIVE summary, appends results to
`FEED_STALENESS_AUDIT.md` §D / `VWAP_AUDIT.md` / `GEX_SR_SPEC.md`, commits that doc
update, and **stops the loop**.

**Not in scope for the scheduled job:** any code change, service restart, config edit, or
roadmap work. It is read-only verification + a doc commit.
