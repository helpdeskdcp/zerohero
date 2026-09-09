"""The canonical, source-agnostic option-chain shape + small helpers."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, time, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))


def expiry_phase(expiry: str, now: datetime | None = None) -> dict:
    """Where this expiry sits in its life-cycle. `expiry` in '15SEP2026' /
    '15-SEP-2026' / '2026-09-15'. Returns:
      { expiry_date, dte (calendar days, may be <0), is_expiry_day, phase }
      phase = EXPIRED | EXPIRY_DAY | EXPIRY_WEEK (1-4 dte) | NORMAL | UNKNOWN
    On EXPIRY_DAY the expiring series' OI unwinds structurally (ΔOI is not
    sentiment) and PCR / OI-walls for that series are unreliable; Max-Pain /
    GEX pinning matter MORE. Callers should steer to the next expiry.
    """
    d = None
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(str(expiry).upper(), f).date()
            break
        except (ValueError, TypeError):
            continue
    if d is None:
        return {"expiry_date": None, "dte": None, "is_expiry_day": False, "phase": "UNKNOWN"}
    now = now or datetime.now(_IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(_IST).date()
    dte = (d - today).days
    close_passed = now.astimezone(_IST).timetz() >= time(15, 30, tzinfo=_IST)
    if dte < 0 or (dte == 0 and close_passed):
        phase = "EXPIRED"
    elif dte == 0:
        phase = "EXPIRY_DAY"
    elif dte <= 4:
        phase = "EXPIRY_WEEK"
    else:
        phase = "NORMAL"
    return {"expiry_date": d.isoformat(), "dte": dte,
            "is_expiry_day": phase == "EXPIRY_DAY", "phase": phase}

# a sensible strike grid per underlying (the profile granularity, not the tick)
_STRIKE_STEP = {
    "NIFTY": 50.0, "BANKNIFTY": 100.0, "FINNIFTY": 50.0, "MIDCPNIFTY": 25.0,
    "SENSEX": 100.0, "BANKEX": 100.0, "NIFTYNXT50": 100.0,
    "CRUDEOIL": 50.0, "NATURALGAS": 5.0,
}


def strike_step_for(underlying: str) -> float:
    return _STRIKE_STEP.get(str(underlying or "").upper(), 50.0)


@dataclass
class OptionLeg:
    """One side (CE or PE) at one strike. Any field may be None if the source
    did not provide it (see `OptionChain.capability`)."""
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_qty: float | None = None
    ask_qty: float | None = None
    oi: float | None = None
    oi_change: float | None = None
    volume: float | None = None
    iv: float | None = None                # decimal (0.11 = 11%)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class StrikeRow:
    strike: float
    ce: OptionLeg | None = None
    pe: OptionLeg | None = None

    def to_dict(self) -> dict:
        return {"strike": self.strike,
                "ce": self.ce.to_dict() if self.ce else None,
                "pe": self.pe.to_dict() if self.pe else None}


@dataclass
class OptionChain:
    underlying: str
    expiry: str                            # canonical, e.g. "15SEP2026"
    ts: str                                # ISO-8601, when this chain was built/observed
    source: str                            # "angelone_captured" | "upstox_open" | "nse_v3" (+ "+greeks:<src>")
    spot: float | None = None
    atm_strike: float | None = None
    rows: list[StrikeRow] = field(default_factory=list)     # sorted ascending by strike
    capability: dict = field(default_factory=dict)          # has_greeks/has_iv/has_oi/has_oi_change/cadence_sec/...
    quality: dict | None = None
    notes: list[str] = field(default_factory=list)
    expiry_ctx: dict = field(default_factory=dict)          # expiry_phase(): dte / is_expiry_day / phase
    available_expiries: list = field(default_factory=list)  # other captured/known expiries for this underlying

    # -------- helpers --------
    def sort(self) -> "OptionChain":
        self.rows.sort(key=lambda r: r.strike)
        return self

    def with_expiry_ctx(self) -> "OptionChain":
        self.expiry_ctx = expiry_phase(self.expiry)
        return self

    def compute_atm(self) -> "OptionChain":
        if not self.rows:
            return self
        if self.spot is None:
            # put-call-parity proxy: strike where |ce_ltp - pe_ltp| is smallest
            best = None
            for r in self.rows:
                if r.ce and r.pe and r.ce.ltp is not None and r.pe.ltp is not None:
                    d = abs(r.ce.ltp - r.pe.ltp)
                    if best is None or d < best[0]:
                        best = (d, r.strike)
            self.atm_strike = best[1] if best else None
        else:
            self.atm_strike = min((r.strike for r in self.rows),
                                  key=lambda s: abs(s - self.spot))
        return self

    def leg(self, strike: float, side: str) -> OptionLeg | None:
        for r in self.rows:
            if abs(r.strike - strike) < 1e-6:
                return r.ce if side.upper() == "CE" else r.pe
        return None

    def window(self, n: int) -> list[StrikeRow]:
        """The +/- n strikes around the ATM (inclusive), for analytics/quality."""
        if self.atm_strike is None or not self.rows:
            return list(self.rows)
        idx = min(range(len(self.rows)), key=lambda i: abs(self.rows[i].strike - self.atm_strike))
        return self.rows[max(0, idx - n): idx + n + 1]

    def to_dict(self) -> dict:
        return {
            "underlying": self.underlying, "expiry": self.expiry, "ts": self.ts,
            "source": self.source, "spot": self.spot, "atm_strike": self.atm_strike,
            "n_strikes": len(self.rows),
            "rows": [r.to_dict() for r in self.rows],
            "capability": self.capability, "quality": self.quality, "notes": self.notes,
            "expiry_ctx": self.expiry_ctx or expiry_phase(self.expiry),
            "available_expiries": self.available_expiries,
        }
