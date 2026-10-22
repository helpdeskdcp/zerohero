# Signal Engine "merge duplicate rules" — premise audit

Date: 2026-09-23
Scope: read-only. No files under `app/` were modified (`git diff --stat -- app/` empty at end).
Trigger: new spec asking to merge all rules into one deterministic Signal Engine
(DATA→REGIME→STRUCTURE→MOMENTUM/FLOW→BREAKOUT/REVERSAL→SCORE→RISK→ACTION,
55/70/80 score bands, RISK>STRUCTURE>REGIME>FLOW>MOMENTUM>EVENT>SCORE precedence).
Constraint: the 2026-09-22 forensic audit (`../trading_logic_forensic_audit_2026-09/AUDIT.md`)
found NO duplication requiring a merge, but only traced `autoscalp/runner.py` and `ai/shadow.py`
as "the two real entry points." This audit checks whether that scope was complete.

## Headline finding

**Yesterday's audit's conclusion was correct for the path it looked at, but its scope was
incomplete.** There is a second, fully independent, currently-armed autonomous decision-and-
execution engine it never traced: `app/scalper.py::ScalpRunner` → `scalp_pipeline.py` →
`scalp_engine.py` + `risk_engine.py` → `pipeline_core.open_from_contract()` → real paper-trade
opens. It runs in parallel to `autoscalp/runner.py`, started at the same time
(`app/main.py` lines 321-322), confirmed **armed: true, running: true** via a live query to
`/api/scalp/status` on the running production service.

So: genuine rule duplication **does** exist in the live path — but it is not inside
`scalp_strategy.py`/`state_classifier.py` (which the new spec's language, and yesterday's audit,
both implicitly assume is the site of the problem). It is a second, older, independently-maintained
engine running *alongside* them, never merged in.

## 1. Engine inventory — LIVE vs DEAD

| Module | Status | Evidence |
|---|---|---|
| `engines/scalp_strategy.py` | LIVE | called by `autoscalp/runner.py::tick_once()` |
| `engines/state_classifier.py` | LIVE | called by `scalp_strategy.decide_from_context()` |
| `engines/option_engine.py`, `regime_mtf.py`, `sr_engine.py`, `oi_math.py` | LIVE | imported by `scalp_strategy.py` |
| `sr_dynamic/*`, `backtest/calibration.py` | LIVE | imported by `scalp_strategy.py` |
| `autoscalp/safeguards.py` | LIVE | risk gate for `autoscalp/runner.py` (max_daily_loss, max_trades_per_day, max_consecutive_losses, kill_switch) |
| `engines/paper_trading.py` | LIVE | trade lifecycle for both live engines (below) |
| `app/scalper.py::ScalpRunner` | **LIVE** | started `app/main.py:321`; confirmed `armed: true, running: true` via `GET /api/scalp/status` on the running service |
| `app/scalp_pipeline.py::run_scalp_pipeline()` | **LIVE** | called from `ScalpRunner._manage()` (`scalper.py:1138`); line 174 calls `pipeline_core.open_from_contract()` on `final_decision == "APPROVED"` → real autonomous paper-trade open |
| `engines/scalp_engine.py` | **LIVE** | imported and run by `scalp_pipeline.py` |
| `engines/risk_engine.py` | **LIVE** | imported and run by `scalp_pipeline.py` |
| `engines/turning_point_engine.py` | **LIVE** | imported by both `scalper.py:664` and `scalp_pipeline.py:65` |
| `engines/signal_engine.py` | LIVE, but **not a decision engine** | shared RSI/EMA/ATR/SMA primitives library, imported by `sr_engine`, `option_engine`, `scalp_engine`, `state_classifier`, `regime_mtf`, `turning_point_engine`, `autoscalp/runner.py` — confirms yesterday's audit's own characterization |
| `app/orchestrator.py::run_pipeline()` | **DEAD / dormant** | no background loop, no `_loop`/`Thread`/`asyncio.create_task`, not started in `main.py` or `runtime.py`; reachable only via on-demand `POST /api/run` (`app/api/engines_routes.py:40`); nothing in the codebase or crontab calls that route. Same `open_from_contract` autonomous-open pattern as `scalp_pipeline.py`, but it never fires on its own. |
| `engines/oi_options_engine.py` | **DEAD / dormant** | imported only by `orchestrator.py:17` — no other live caller found |
| `app/ai/shadow.py` | LIVE, shadow-only | no direct effect on trade decisions, per yesterday's audit — unaffected by this audit's findings |

**Count: 12 modules confirmed LIVE (10 decision-relevant + 1 shared-primitives + 1 shadow-only), 2 confirmed DEAD/dormant (`orchestrator.py`, `oi_options_engine.py`).**

Two of the LIVE modules (`scalp_engine.py`, `risk_engine.py`) were missed by yesterday's audit
entirely because its runtime trace stopped at `autoscalp/runner.py` + `ai/shadow.py`.

## 2. Is there genuine rule duplication in the live path?

**Yes — at the engine-pair level, not inside `scalp_strategy.py`/`state_classifier.py`.**

Two independently-coded, independently-armed pipelines both consume live candles and both
autonomously open real paper trades:

| Concept | Pipeline A: `scalp_strategy.py` + `state_classifier.py` (via `autoscalp/runner.py`) | Pipeline B: `scalp_engine.py` + `risk_engine.py` (via `ScalpRunner`/`scalp_pipeline.py`) |
|---|---|---|
| Structure/state | HH/HL/LH/LL-style zone-reversal states: `SUPPORT_REVERSAL`, `SUPPORT_BREAKDOWN`, `RESISTANCE_REVERSAL`, `RESISTANCE_BREAKOUT` → `BULLISH`/`BEARISH` | No structure layer — setup detectors only (`VWAP_RECLAIM`, `EMA_PULLBACK`, `MOMENTUM_BREAK`, fixed priority order) |
| Regime taxonomy | `regime_mtf.py`: `TRENDING_UP` / `TRENDING_DOWN` / `RANGE` / `HIGH_VOLATILITY` / `LOW_VOLATILITY` / `UNSTABLE` | own, different taxonomy: `SCALP_TREND_UP` / `SCALP_TREND_DOWN` / `SCALP_RANGE` / `SCALP_HIGH_VOL` (EMA-cross + price-vs-EMA rule, not the same computation as `regime_mtf.py`) |
| Scoring | `state_classifier.py`: 9-component weighted sum (price_action 0.20, level_strength 0.16, volume 0.12, oi 0.12, momentum 0.14, vwap 0.08, atr 0.08, htf 0.06, retest 0.04) → blended `0.62*state_score + 0.24*option_quality + 0.14*mtf_magnitude` in `scalp_strategy.py` | separate 5-component evidence sum (EMA cross 1.0, price>VWAP 0.9, +ROC 0.8, MACD>0 0.5, vol expansion 0.7) → sigmoid → `prob_up_pct`, different weights, different inputs, no OI/HTF/retest component at all |
| Decision bands | `NO_TRADE` / `WATCH` / `BUY_CE` / `BUY_PE` (3-tier, no numeric 55/70/80 split found anywhere in this file) | `DATA_UNAVAILABLE` / directional `BUY`/`SELL` gated by `prob_min` (default 55) and `rr_min` — different vocabulary again |
| Risk gate | `autoscalp/safeguards.py`: `max_daily_loss` (₹), `max_trades_per_day`, `max_consecutive_losses`, `kill_switch` | `engines/risk_engine.py` (docstring: *"Ported 1:1 from the n8n Code node logic"*): `max_daily_loss_pct`, `max_trades`, `max_open_positions`, `max_consecutive_losses`, `max_volatility_pct`, `kill_switch` — near-identical concept set, independently thresholded, independently coded |
| Indicators | via `signal_engine.py` shared primitives | own private re-implementations (`_ema_series`, `_rsi`, `_atr`, `_vwap`, `_macd`, `_roc_pct` inside `scalp_engine.py` — comment says *"same idiom as signal_engine"* but does not import it) |

This is real duplication: two parallel regime classifiers, two parallel scoring formulas, two
parallel risk-limit gates, two parallel indicator implementations — maintained separately, drifting
independently, each capable of opening a trade with no cross-awareness of the other. Neither one
individually resembles the new spec's exact pipeline shape (DATA→REGIME→STRUCTURE→MOMENTUM/FLOW→
BREAKOUT/REVERSAL→SCORE→RISK→ACTION) — Pipeline A has structure+regime+score+risk but no explicit
breakout/reversal stage separate from structure; Pipeline B has regime+momentum/setup+score+risk but
no structure (HH/HL/LH/LL) layer at all. The new spec's exact 4-band `NO_TRADE`/`WATCH`/`TRIGGER`/
`CONFIRMED` vocabulary and explicit RISK>STRUCTURE>REGIME>FLOW>MOMENTUM>EVENT>SCORE precedence chain
exist **nowhere** in either pipeline verbatim — both are implicit/positional in code, not a named,
inspectable precedence chain.

No overlap check was found between `ScalpRunner`'s watchlist and `AutoScalpRunner`'s at the safeguard
level — i.e. nothing prevents both engines from opening a position on the same underlying/side
simultaneously; they only share the underlying market-data feed object (`runtime.py:199`,
`scalp_runner.feed` reused by `autoscalp`), not a common entry-arbitration/dedup layer.

## 3. Verdict on the new spec's premise

- **"Merge duplicate rules into ONE deterministic engine"** — premise is correct, but pointed at
  the wrong target. There is no duplication to remove *inside* `scalp_strategy.py`/
  `state_classifier.py` (yesterday's audit's conclusion on that specific file pair stands, and this
  audit found nothing that contradicts it). The real duplication is a second, older engine
  (`scalp_engine.py` + `risk_engine.py`, running via `ScalpRunner`) sitting entirely outside
  yesterday's audit's field of view, parallel-live with the audited path.
- **The specific pipeline shape / score bands / precedence chain in the new spec** do not match
  either existing live engine closely enough to be a "just rename it" job — building it exactly as
  specified would mean either (a) a third new engine (worse — three parallel live paths), or
  (b) a genuine consolidation of A+B into one, which is a real design/backtest undertaking, not a
  mechanical merge.

## 4. Recommendation

**Do not build a new third engine matching the spec's exact pipeline as an addition.** Two live
engines opening real (paper) trades independently, with divergent regime/scoring rules and no
mutual awareness, is itself an operational risk worth fixing regardless of the new spec.

**Proceed narrowly, in this order, before anything resembling the new spec's full rebuild:**
1. Confirm with the user whether `app/scalper.py::ScalpRunner` autonomously opening trades via
   `scalp_pipeline.py`/`scalp_engine.py`/`risk_engine.py` is *intentional* (e.g. a deliberately
   simpler/faster secondary scalp system) or a legacy holdover from the n8n port that should be
   disarmed. This is a factual/product question, not a code question — do not assume.
2. If it's legacy: disarm/retire `ScalpRunner`'s autonomous-open path (keep its broker-position-
   mirroring feature, which is unrelated and legitimate) rather than merging its rules into
   `scalp_strategy.py`. That removes the duplication with the least risk and no new engine needed.
3. If it's intentional: the "one deterministic Signal Engine" spec becomes a real design task —
   reconcile two conflicting regime taxonomies and two scoring formulas with real backtest evidence
   before touching either, per the user's standing rule that `scalp_strategy.py`/`state_classifier.py`
   require explicit approval + evidence before modification. That evidence does not exist yet.
4. Either way: `scalp_strategy.py`/`state_classifier.py` should **not** be modified as part of this —
   nothing found here overturns yesterday's audit's conclusion that they are internally
   non-duplicative and correctly the "source of truth" for the audited path.
5. `orchestrator.py` and `oi_options_engine.py` are currently dormant (on-demand API only, nothing
   calls them). Leave as-is; flag only if something is later found to call `POST /api/run`
   programmatically, which would make orchestrator a third live autonomous engine.

**Verdict: do not proceed with the spec as written. Proceed narrowly on item 1 (a product decision
about `ScalpRunner`'s autonomous-open path) before any merge/rebuild work.**
