# Frontend Audit — Chanakya AI / Auto-Scalp Dashboard

**Date:** 2026-09-01 · **Commit:** `5d83a32` · **Scope:** frontend quality + integration only
(no trading logic, signals, risk limits, or PAPER/LIVE enforcement touched)

---

## 1. Pages / routes audited (9 SPA views, hash-free, `data-view` routing)

overview · monitor (Live Monitor) · signals (Signal Ledger) · trades (Paper Trades) ·
scalp (Scalping) · autoscalp (Auto-Scalp) · runner (Run Pipeline) · research · **system (now "System & Health")**

## 2. Components audited

Top bar (WS status pill, PAPER pill) · sidebar nav (≥860px) · mobile tab bar (<860px) ·
stat grids · panels · ledger tables (14) · forms (Run Pipeline, Scalp config, Track position) ·
`prompt()`/`confirm()` dialogs · live feed list · monitor strips · symbol pickers / datalists ·
research cards · system grid · **new: Operational Health panel, Session Report panel, health line, global toast**

## 3. API endpoints audited (23 distinct; all return 200 live)

`/api/health` `/api/env-check` `/api/instruments` `/api/market-instruments` `/api/market-selection`
`/api/market/calendar` `/api/research` `/api/monitor` `/api/signals` `/api/trades` `/api/trades/close`
`/api/scalp/{status,trades,arm,disarm,config}` `/api/positions{,/track,/untrack,/levels,/combo/levels}`
`/api/run` `/api/autoscalp/{status,signals,snapshots,universe,watchlist,arm,disarm,kill,`**`selfcheck,report`**`}`
`/api/execution/kill` · WS `/ws`

- All FE calls resolve to a real backend route. No 404/405.
- Field-by-field diff of every consumed response vs the render code: **no schema mismatch** after fixes.
- **`/api/autoscalp/selfcheck` and `/api/autoscalp/report` were not consumed by the UI at all** → now wired.

## 4. Real-time connections audited

- One `WebSocket('/ws')`, auto-reconnect 2.5 s, 20 s keepalive ping.
- `loadMonitor` has a request-sequence + in-flight guard (stale responses discarded).
- Polling: overview 15 s · scalp 3 s · monitor 1.5 s · autoscalp 3 s · **health 10 s (new)** — all gated by `state.view`.
- **Could not verify live WS reconnect / visual behaviour** — the audit environment's Chrome is network-isolated from the backend (`ERR_CONNECTION_REFUSED`). Verified by static analysis + a stub-DOM smoke test instead.

## 5–7. Bugs found / fixed / deferred

### Fixed (12)

| # | Problem | Root cause | Fix | Verification |
|---|---|---|---|---|
| 1 | Self-check + session report endpoints unused; no operational-health UI, no reporting UI | never built | New **Operational Health** panel (`/selfcheck`: per-check GREEN/WARN/ERROR, segments, feed age, aggregators, `entry_blocks`, `config_warnings`) + **Session Report** panel (`/report?day=`: date picker, load/empty/error/retry, per-symbol W/L/net/avgR/exits/blocks, PAPER-labelled) + compact health line on the Auto-Scalp view | `render_smoke.test.js` drives both loaders against real payloads; live contract diff shows every consumed field present |
| 2 | "Live Trading" rendered as a **red dot when safely disabled** (and green when ON = danger shown as "good") | `class="dot on/off"` bound to `health.live_trading` | Explicit text: `DISABLED ✓` (teal) / `ENABLED ⚠` (ember) + a `Mode: PAPER` cell | `live_monitor.test.js` asserts both strings present; smoke test asserts `#sysGrid` contains them |
| 3 | Data-derived values interpolated into `innerHTML` **without escaping** in overview feed, signals, trades, scalp blotter, tracked positions (the monitor view was already hardened) | inconsistent hardening | Route `underlying/direction/decision/regime/reason/status/strategy/setup/exit_reason/option_type/strike/trade_id` through `esc()` / `text()` / `directionClass()` incl. `class="badge …"` and `data-*` attributes | smoke test feeds `<img onerror>` / `<b>` payloads → asserts entity-escaped, no live tag |
| 4 | WS keepalive `setInterval` **leaked on every reconnect** (N stacked ping timers after N drops) | `connectWs()` created a new interval each call, never cleared | `wsPingTimer` / `wsReconnectTimer` cleared before reconnect; ping wrapped in try/catch | `live_monitor.test.js` asserts `clearInterval(wsPingTimer)` |
| 5 | Fetch failures on overview / signals / trades / research / system were **console-only** — user saw silently stale data | `catch (e) { console.error(e) }` | `showError(where, e)` → dismissable `#toast` (`role=status`, auto-hide 8 s); each view keeps last-known content | test asserts no `console.error(e)`-only catch remains |
| 6 | A burst of `autoscalp_*` WS events fired `loadAutoscalp()` (**8 parallel requests**) **per event** during an active session | no debounce on the WS-triggered reload | `scheduleAutoscalpReload()` coalesces to one reload / 500 ms | test asserts `scheduleAutoscalpReload` present |
| 7 | Stale / market-closed prices in the Auto-Scalp strip rendered **as if fresh** | strip always styled the same | `is-stale` / `is-closed` classes → muted cells + `STALE FEED` / `MARKET CLOSED — showing last values` banner, driven by `selfcheck.market_open` / `regime==MARKET_CLOSED` / `feed_age>30s` | CSS + smoke test |
| 8 | SCALP config **`<option>LIVE`** freely selectable in the dashboard | select had a live option | `disabled` on the LIVE option ("server-gated — off"); a stored LIVE config displays as SHADOW with a note; checkbox label clarified to "PAPER / SHADOW only from here" | `live_monitor.test.js` asserts `<option value="LIVE" disabled>`; FE has **no live-order endpoint** (grep-verified) |
| 9 | System view **unreachable on mobile** (<860px) — not in the tab bar | tab bar had 8 of 9 views | Swapped "Run" (backtest form, least mobile-critical) for "Health" in the tab bar; Run Pipeline stays in the desktop sidebar | HTML |
| 10 | `fmt("")` returned `"0.00"` instead of `"—"`; P&L cells were **colour-only** (no sign) — fails colour-blind users | `isNaN("")` is `false`; no sign prefix | `fmt` guards `""`; new `fmtSigned()` used for every P&L / net / R cell | smoke test |
| 11 | Table headers had no `scope`; no visible keyboard-focus ring; faint text `#5c6478` on `#151a24` ≈ 3:1 (below AA) | a11y gaps | `scope="col"` on 26 `<th>`; `:focus-visible` outline; `--ink-faint` → `#737d94` (~4.6:1) | HTML + CSS |
| 12 | `loadSystem` fetched env + health **sequentially**, and one failure blanked the panel | `await` chain, single try | `Promise.all`; three independent try blocks (env/health, selfcheck, sysgrid) | smoke test drives `loadSystem` with partial-failure fixtures |

### Deferred (not genuine / needs a live browser / would be speculative)

| Item | Why deferred |
|---|---|
| Monitor 1.5 s / 22 KB poll cadence; full-table `innerHTML` rebuild | Deliberate "live monitor" design; has a seq guard; changing cadence is a product decision, not a bug. Noted for the next perf pass. |
| Overview/signals/trades/scalp WS reloads not debounced | Those events are per-trade (low frequency), unlike the per-cycle `autoscalp_*` burst that was fixed. Not worth the complexity now. |
| Pixel-level visual / true responsive / orientation / touch-target verification | The audit environment's Chrome cannot reach the backend. Static + CSS review done; a real device pass is still owed (see §17). |
| Timezone labelling on timestamps | `toLocaleTimeString` renders in the viewer's TZ; for an India desk that ≈ IST. Adding an explicit "IST" everywhere is cosmetic; deferred. |
| `api()` 401 → N `prompt()`s when many parallel calls fail | Only reachable if `CHANAKYA_API_TOKEN` is set (it is not in this deploy). Edge case; deferred. |

## 8. Responsive issues fixed

- System / Health / Report reachable on mobile (tab bar).
- Tab bar: `min-width:0` + `white-space:nowrap` + font step-down `<380px` so 9 items don't clip.
- New panels use the existing `.sysgrid` (2-col mobile / 3-col desktop) and `.table-wrap` (`overflow-x:auto`) — no new horizontal page scroll.
- **Not verified on real devices** (see §4 / §17).

## 9. Accessibility issues fixed

`scope="col"` on all 26 table headers · visible `:focus-visible` outline on all controls ·
`--ink-faint` raised to WCAG-AA contrast for 11 px text · P&L sign no longer colour-only ·
toast is `role="status" aria-live="polite"` · env dots got `title` text.

## 10. Performance issues fixed

WS keepalive timer leak (unbounded) · `autoscalp_*` WS-event storm (8 req/event → 1 req/500 ms) ·
`loadSystem` parallelised.

## 11. Security issues fixed / verified

- Output escaping extended to all non-monitor views (defence-in-depth; data is backend-controlled but now safe regardless).
- **No secrets in the frontend**: `/api/env-check` returns booleans only; `/api/health` returns status only. Grepped source — no tokens/keys/passwords. `localStorage` holds only an optional `chanakya_token` the user types (not a shipped secret).
- **No live-order path in the frontend** (grep for `entry|place|submit|market_entry` → none). Only `/api/*/kill` (a safety control) and config/arm endpoints, all backend-gated.
- `innerHTML` still used (vanilla-JS choice) but every interpolation of dynamic data now goes through `esc()`/`text()`.

## 12. Tests

| Suite | Result |
|---|---|
| `frontend/tests/live_monitor.test.js` (extended: +13 assertions) | **PASS** |
| `frontend/tests/render_smoke.test.js` (**new** — boots app.js in a stub DOM, drives all 9 view loaders + feed renderer against real captured API payloads, checks output escaping) | **PASS** (also passes standalone with inline fixtures — CI-safe) |
| `node -c app.js` (syntax) | **PASS** |
| backend `pytest -q` (unchanged; run as the safety check) | **277 passed** |

## 13. Build status

No build step (vanilla JS, `<script src>`). Files served by FastAPI `StaticFiles` + `FileResponse`,
read from disk per request → the deploy is complete on file write. Verified: the running backend
serves the updated `app.js` / `index.html` / `style.css` (Last-Modified current, new symbols present).
**No service restart** → evidence collectors uninterrupted, PID unchanged (3492058).

## 14. Console / network status

- Static analysis + stub-DOM execution: **0 runtime errors** across all 9 view loaders with real payloads, including partial-failure and hostile-input fixtures.
- Live browser console/network capture **not possible** from the audit environment (network isolation). This is the one item that still needs a real-browser pass (§17).

## 15. Backend contract compatibility

**No backend files changed.** `git diff` = 4 frontend files only. Live field-by-field verification:
every key the new FE reads from `/api/autoscalp/selfcheck` and `/api/autoscalp/report` is present in
the live response. All other endpoints already matched.

## 16. PAPER / LIVE safety verification

| check | state |
|---|---|
| `/api/health` | `live_trading: false`, `paper_mode: true` |
| `/api/autoscalp/status` | `live_trading: false`, `paper_mode: true`, `last_error: null` |
| `/api/autoscalp/selfcheck` | `ok: true`, `live_trading_disabled: true`, `config_warnings: []` |
| Frontend → live-order endpoint | **none exists** (grep-verified) |
| SCALP `execution_mode` LIVE option | **disabled** in the dashboard |
| Kill-switch controls | preserved (`/api/autoscalp/kill`, `/api/execution/kill`) |
| Safety indicators | **none removed**; "Live Trading DISABLED ✓" text **added**; topbar `PAPER MODE` pill untouched |
| Evidence collectors (`3480137` / `3490027` / `3486913`) | alive, uninterrupted (no restart) |

## 17. Remaining known issues

1. **Real-browser pass still owed** — pixel layout, true responsive/orientation, touch targets, live WS reconnect, and browser console/network 4xx/5xx capture were not doable here (Chrome ↔ backend network isolation). Static + stub-DOM + CSS review substituted. Recommend a 15-minute manual pass on desktop + one Android device.
2. Monitor view still polls at 1.5 s and rebuilds full tables via `innerHTML` (scroll-reset, selection loss). Deliberate; candidate for a keyed-diff render in a future pass.
3. Timestamps render in the viewer's local timezone with no "IST" label.
4. `runner` (Run Pipeline) view dropped from the mobile tab bar — still reachable on desktop; a backtest form is low-priority on mobile, but note the change.
5. The `render_smoke` test uses a hand-rolled stub DOM (no jsdom, to keep the frontend dependency-free) — it verifies "does not throw + output escaped + panels populate", not layout.
