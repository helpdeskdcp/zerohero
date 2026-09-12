"""
Canonical Telegram Signal Dispatcher -- section 19 of the "ZEROHERO — FINAL
PRODUCTION ARCHITECTURE..." brief.

The Phase 0 Telegram audit (see backend/PHASE0_AUDIT.md section 4) found 5
independent call sites broadcasting "new trade opportunity" signals straight
to app.connectors.telegram, each with its own dedup (or none), no awareness
of what any OTHER engine is currently saying about the same underlying, and
3 different message formats. This module is the ONE choke point those
call sites now go through instead of calling the connector directly.

**In scope** (a NEW TRADE OPPORTUNITY announcement -- the kind that can
genuinely compete/conflict across engines, per section 19's own framing):
  - pipeline_core.log_and_notify()          (orchestrator + scalp_pipeline)
  - autoscalp's ENTRY card                  (autoscalp/notify.py + runner.py)
  - orderflow's smart-money breakout card   (orderflow/notify.py)
  - scalper.py's S/R reversal-scan signal
  - scalper.py's Turning-Point signal

**Deliberately OUT of scope** (legitimate, one-per-position lifecycle
events -- not "multiple engines competing to announce the same
opportunity", so there is nothing to deduplicate or reconcile):
TARGET/STOP/TRAIL/EXIT lifecycle alerts, trade-closed alerts, combo
(strangle) exit alerts, session-report summaries, order-adapter execution
alerts. These keep calling app.connectors.telegram directly, unchanged.

**Design choice, stated up front**: this module does NOT rebuild every
engine's message into one new schema. Each in-scope call site already has
its own well-tested card builder (autoscalp/notify.py's `signal_card`,
orderflow/notify.py's `signal_card`, scalper.py's inline HTML) -- reusing
that text is "reuse existing functionality" in practice, not just in
principle, and rewriting five message formats into one risks changing what
the user is used to reading for no functional gain. What THIS module adds
is the one thing no single engine can know on its own:

  1. **Cross-engine agreement/conflict** -- a short-lived, in-memory,
     per-underlying registry of recently-dispatched (direction,
     source_engine, ts) tuples. A same-direction hit from a DIFFERENT
     engine within `agreement_window_sec` (default 900s, matching
     autoscalp's own existing `telegram_dedup_sec` default -- not a new
     number invented here) gets a one-line "CONFIRMED — also flagged by X"
     banner prepended; an opposing-direction hit gets a "CONFLICT" banner.
     No banner is added when there is no cross-engine evidence yet --
     manufacturing a status label with nothing behind it would be exactly
     the kind of unjustified precision this whole session has avoided
     elsewhere (break_score.py, shadow.py).
  2. **Exact-repeat suppression** for the two scalper.py call sites that
     the Phase 0 audit found had NO dedup at all (`exact_repeat_window_sec`,
     default 600s). pipeline_core/autoscalp/orderflow already have their
     own dedup and are unaffected by this (their own repeat within the
     window would already have been filtered before reaching here; this is
     a second, harmless check for them, not a new behavior).
  3. **Structural Break state + a derived Model Health label**, attached as
     a footer line ONLY when this session's app.structural_break layer
     already tracks the underlying -- a cheap read of the ALREADY-COMPUTED
     in-memory state (never a fresh synchronous evaluation; that would add
     DB/CPU cost to the alert-send hot path for no benefit here).
  4. Section 19's canonical-schema fields this codebase cannot yet compute
     -- Edge Score (Phase 9, not built) and Microstructure (Phase 10, not
     built) -- are recorded as `None` in the structured `DispatchRecord`,
     never fabricated and never mentioned in the human-readable text.

Every send failure is swallowed here exactly like app.connectors.telegram's
own contract: alerting must never break a trading pipeline.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from .connectors import telegram

# 15 min -- matches autoscalp/runner.py's own existing telegram_dedup_sec
# default, reused rather than inventing a second unrelated number.
AGREEMENT_WINDOW_SEC = 900
# 10 min -- suppress an identical (underlying, direction, engine) repeat.
# Only bites for call sites that had no dedup of their own before this file
# (scalper.py's two ad-hoc sends); a no-op for engines with their own dedup.
EXACT_REPEAT_WINDOW_SEC = 600

_BULL = {"BUY", "BUY_CE", "BULLISH", "CE", "UP", "LONG", "UP_TURN"}
_BEAR = {"SELL", "BUY_PE", "BEARISH", "PE", "DOWN", "SHORT", "DOWN_TURN"}

_HEALTHY_STATES = ("NORMAL", "RECOVERED")
_WATCHING_STATES = ("WATCH",)
_DEGRADED_STATES = ("DEGRADING", "STRUCTURAL_BREAK", "ADAPTATION", "VALIDATION")


def _norm_direction(d) -> str:
    s = str(d or "").upper()
    if s in _BULL:
        return "BULLISH"
    if s in _BEAR:
        return "BEARISH"
    return s or "UNKNOWN"


@dataclass
class DispatchRecord:
    ts: float
    underlying: str
    direction: str
    source_engine: str
    signal_id: str | None
    status: str             # SENT | SUPPRESSED_DUPLICATE
    agreement: str          # NONE | CONFIRMED | CONFLICT
    structural_break_state: str | None
    model_health: str | None
    edge_score: None = None            # Phase 9 -- not built yet, never fabricated
    microstructure: None = None        # Phase 10 -- not built yet, never fabricated

    def to_dict(self):
        return asdict(self)


class TelegramDispatcher:
    """One instance for the process lifetime (see `dispatcher()` singleton).
    Holds only a small in-memory recent-signal registry -- no DB, no
    persistence across a restart (a missed cross-engine correlation across
    a restart is a minor, self-healing loss, not a correctness issue)."""

    def __init__(self, *, agreement_window_sec: float = AGREEMENT_WINDOW_SEC,
                 exact_repeat_window_sec: float = EXACT_REPEAT_WINDOW_SEC,
                 send_fn=None):
        self.agreement_window_sec = agreement_window_sec
        self.exact_repeat_window_sec = exact_repeat_window_sec
        self._send_fn = send_fn or telegram._send
        self._recent: dict[str, list[dict]] = {}
        self.history: list[DispatchRecord] = []

    def _prune(self, underlying: str, now: float) -> list[dict]:
        window = max(self.agreement_window_sec, self.exact_repeat_window_sec)
        kept = [e for e in self._recent.get(underlying, []) if now - e["ts"] <= window]
        self._recent[underlying] = kept
        return kept

    @staticmethod
    def _model_health(sb_state: str | None) -> str | None:
        if sb_state is None:
            return None
        if sb_state in _HEALTHY_STATES:
            return "HEALTHY"
        if sb_state in _WATCHING_STATES:
            return "WATCHING"
        if sb_state in _DEGRADED_STATES:
            return "DEGRADED"
        return "UNKNOWN"

    @staticmethod
    def _structural_break_state(underlying: str) -> str | None:
        try:
            from .structural_break import evaluator
            for scope in evaluator.tracked_scopes():
                key = scope["scope_key"]
                if key == underlying or key.startswith(underlying + ":"):
                    return scope["state"]
        except Exception:
            pass
        return None

    def dispatch(self, *, source_engine: str, underlying: str, direction, text: str,
                 chat_id: str | None = None, signal_id: str | None = None) -> DispatchRecord:
        now = time.time()
        underlying = str(underlying or "").upper()
        norm_dir = _norm_direction(direction)
        entries = self._prune(underlying, now)

        for e in entries:
            if (e["source_engine"] == source_engine and e["direction"] == norm_dir
                    and now - e["ts"] <= self.exact_repeat_window_sec):
                rec = DispatchRecord(
                    ts=now, underlying=underlying, direction=norm_dir,
                    source_engine=source_engine, signal_id=signal_id,
                    status="SUPPRESSED_DUPLICATE", agreement="NONE",
                    structural_break_state=None, model_health=None)
                self.history.append(rec)
                return rec

        agreement, banner = "NONE", None
        for e in entries:
            if e["source_engine"] == source_engine or now - e["ts"] > self.agreement_window_sec:
                continue
            age_min = int((now - e["ts"]) // 60)
            if e["direction"] == norm_dir:
                agreement = "CONFIRMED"
                banner = f"✅ <b>CONFIRMED</b> — also flagged by {e['source_engine']} ({age_min}m ago)"
            else:
                agreement = "CONFLICT"
                banner = (f"⚠️ <b>CONFLICT</b> — {e['source_engine']} flagged "
                          f"{e['direction']} {age_min}m ago")
            break

        sb_state = self._structural_break_state(underlying)
        health = self._model_health(sb_state)

        final_text = f"{banner}\n{text}" if banner else text
        if sb_state:
            final_text = f"{final_text}\nStructural Break: {sb_state}  |  Model Health: {health}"

        self._send_fn(final_text, chat_id)
        self._recent.setdefault(underlying, []).append(
            {"ts": now, "direction": norm_dir, "source_engine": source_engine})

        rec = DispatchRecord(
            ts=now, underlying=underlying, direction=norm_dir, source_engine=source_engine,
            signal_id=signal_id, status="SENT", agreement=agreement,
            structural_break_state=sb_state, model_health=health)
        self.history.append(rec)
        return rec


_singleton: TelegramDispatcher | None = None


def dispatcher() -> TelegramDispatcher:
    global _singleton
    if _singleton is None:
        _singleton = TelegramDispatcher()
    return _singleton


def dispatch(*, source_engine: str, underlying: str, direction, text: str,
             chat_id: str | None = None, signal_id: str | None = None) -> dict:
    """Module-level convenience entry point for call sites. Never raises --
    same contract as app.connectors.telegram: alerting must never break a
    trading pipeline."""
    try:
        rec = dispatcher().dispatch(source_engine=source_engine, underlying=underlying,
                                     direction=direction, text=text, chat_id=chat_id,
                                     signal_id=signal_id)
        return rec.to_dict()
    except Exception as e:
        return {"status": "ERROR", "reason": str(e)}
