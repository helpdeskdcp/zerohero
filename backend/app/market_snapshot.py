"""Atomic Market Snapshot (MCP 3.3 section 4).

Ties one underlying's spot + option chain + India VIX together under ONE
snapshot_id and ONE `taken_at` timestamp, with each field's own source and
data age carried alongside it -- never silently blended across sources or
capture times without a tolerance check (section 3: "A Dhan price at T and
Angel One OI at T+delta must NOT automatically form one snapshot").

This is a read-only composition layer over already-existing, already-tested
primitives -- market_hub.snapshot() (spot/bars/chain, WS-feed-or-histcap),
app.optionchain.resolve.get_chain() (the existing multi-source chain
failover), and HistStore.get_quotes() (the existing captured-quote reader,
reused here for India VIX). No new capture path is added; this module adds
only the atomicity/coherence layer on top of data that was already being
read separately.

Provenance today: every field this module can populate currently traces
back to AngelOne (app.connectors.angelone / app.histcap) -- Dhan is not yet
a verified option-chain/quote source (see MCP_3_3_STATUS.md: Dhan has no
option-chain capture built, and the stored OAuth token requires an
interactive human re-consent to refresh, which this module will never
attempt on its own). When Dhan is verified, it plugs into market_hub's own
fallback chain as an additional source, not a rewrite of this module.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from . import market_hub
from .histcap.store import HistStore
from .optionchain.resolve import get_chain as _get_chain

DEFAULT_MAX_AGE_SEC = 420.0
# Calibrated against real observed capture cadence, not guessed: INDIAVIX
# is one of 9 symbols histcap captures sequentially every ~20s-quote_sec
# cycle, and live testing 2026-09-30 (market open) showed real gaps up to
# ~4m40s between successive INDIAVIX rows under normal, healthy operation
# (histcap/worker.py processes symbols in sequence, not in parallel). A
# tighter threshold (180s was the first guess, copied from
# mathematical_confluence.context._spot_from_histcap's WS-fed-spot
# default) flagged UNAVAILABLE on a perfectly healthy capture -- confirmed
# live via GET /api/market/vix mid-testing. 420s gives headroom above the
# observed worst case while still catching a genuinely stalled capture
# (worker crashed, symbol dropped from config, etc.).

# Two components captured further apart than this are never silently
# treated as "the same moment" -- the more-stale one is kept (never
# dropped) but flagged in `conflicts`. Same 2026-09-30 live evidence as
# DEFAULT_MAX_AGE_SEC above: a 60s tolerance flagged INDIAVIX as a
# conflict on essentially every snapshot even under healthy operation,
# since spot refreshes every second (WS feed) but VIX only every
# ~20s-4m40s (sequential REST capture) -- making `conflicts` permanently
# noisy defeats its purpose as a real signal. 300s (comfortably inside
# DEFAULT_MAX_AGE_SEC's 420s ceiling) still catches a VIX read that's
# stale relative to a fresh snapshot without flagging normal capture
# cadence as if it were a data problem.
DEFAULT_COHERENCE_TOLERANCE_SEC = 300.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_index_quote(symbol: str, *, max_age_sec: float = DEFAULT_MAX_AGE_SEC,
                        store: HistStore | None = None) -> dict | None:
    """{ltp, net_change, pct_change, captured_ts, age_sec, source} for the
    latest captured INDEX quote, or None if nothing recent enough exists.
    Reuses HistStore.get_quotes() (already tested) rather than a second raw
    SQL reader -- `since` is required to get the newest rows first (see
    that method's own docstring on the ASC+LIMIT footgun), so this passes
    a cutoff and takes the last (most recent) row of what comes back."""
    store = store or HistStore()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_sec)).isoformat()
    try:
        rows = store.get_quotes(symbol, kind="INDEX", since=cutoff, limit=1000)
    except Exception:
        return None
    if not rows:
        return None
    r = rows[-1]
    ts_raw = r.get("exch_ts") or r.get("received_ts")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
    if age_sec > max_age_sec:
        return None
    return {"ltp": r.get("ltp"), "net_change": r.get("net_change"), "pct_change": r.get("pct_change"),
            "captured_ts": ts_raw, "age_sec": round(age_sec, 1), "source": r.get("source")}


@dataclass
class AtomicSnapshot:
    snapshot_id: str
    underlying: str
    taken_at: str
    spot: float | None
    spot_source: str | None
    chain_rows: int
    chain_expiry: str | None
    chain_source: str | None
    chain_quality: dict | None
    india_vix: dict | None            # _latest_index_quote()'s shape, or None
    data_quality: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)   # DATA_CONFLICT reasons; empty when clean

    def to_dict(self) -> dict:
        return asdict(self)


def build_snapshot(underlying: str, *, allow_network: bool = False,
                   coherence_tolerance_sec: float = DEFAULT_COHERENCE_TOLERANCE_SEC,
                   store: HistStore | None = None) -> AtomicSnapshot:
    """One immutable snapshot_id tying spot + option chain + India VIX
    together. `allow_network=False` (the default -- matching every other
    offline/deterministic read path in this codebase, e.g. hedging's own
    get_chain() calls) restricts every component to already-captured data,
    never a live broker call, so two calls a millisecond apart return the
    same picture instead of racing a fresh fetch against a stale one.

    Never blends: the India VIX read is timestamped and checked against
    `coherence_tolerance_sec` relative to when THIS snapshot was taken --
    a stale VIX quote is still returned (nothing is silently dropped) but
    is flagged in `conflicts`, never presented as if it were as fresh as
    the rest of the snapshot."""
    underlying = underlying.upper()
    taken_at = _now_iso()
    conflicts: list[str] = []

    ctx = market_hub.snapshot(underlying, allow_rest_fallback=allow_network)
    spot = ctx.get("spot")
    spot_source = ctx.get("data_quality", {}).get("spot")

    chain = _get_chain(underlying, "AUTO", allow_network=allow_network)
    chain_rows = len(chain.rows) if chain and chain.rows else 0
    chain_expiry = chain.expiry if chain else None
    chain_source = chain.source if chain else None
    chain_quality = chain.quality if chain else None

    vix = _latest_index_quote("INDIAVIX", store=store)
    if vix is not None and vix["age_sec"] > coherence_tolerance_sec:
        conflicts.append(
            f"INDIAVIX quote is {vix['age_sec']:.0f}s old (> {coherence_tolerance_sec:.0f}s "
            f"tolerance relative to this snapshot) -- included but flagged, not treated as live")

    if spot is None:
        conflicts.append("no spot available -- snapshot is incomplete")
    if chain_rows == 0:
        conflicts.append("no option chain available -- snapshot is incomplete")

    return AtomicSnapshot(
        snapshot_id=str(uuid.uuid4()), underlying=underlying, taken_at=taken_at,
        spot=spot, spot_source=spot_source,
        chain_rows=chain_rows, chain_expiry=chain_expiry, chain_source=chain_source,
        chain_quality=chain_quality, india_vix=vix,
        data_quality=ctx.get("data_quality", {}), conflicts=conflicts)
