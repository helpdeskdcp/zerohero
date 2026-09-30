"""Versioned, auditable option-expiry resolver (no guessing).

Precedence (spec section 1/27):
  1. contract metadata (broker/Dhan) for the date range it actually covers -> VERIFIED
  2. versioned rule + exchange calendar -> RECONSTRUCTED, but only when the rule
     version's evidence is verified AND the calendar covers every date it needs
  3. otherwise UNAVAILABLE with a machine-readable reason.
If metadata and a rule disagree, metadata wins and expiry_rule_conflict=True is
recorded with BOTH dates.

Calendars here are OBSERVED trading days (a day is open iff a daily bar exists in
Dhan's index history) -- evidence, not an official exchange calendar. They are
never labelled EXCHANGE_CALENDAR. MCX has no calendar at all -> MCX rule
reconstruction is UNAVAILABLE unless the caller supplies one.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

VERIFIED, RECONSTRUCTED, UNAVAILABLE = "VERIFIED", "RECONSTRUCTED", "UNAVAILABLE"
SRC_BROKER, SRC_DHAN, SRC_RULE, SRC_NONE = "BROKER_METADATA", "DHAN_METADATA", "RULE_RECONSTRUCTION", "UNAVAILABLE"

# Rule versions keyed on the NOMINAL expiry date. ev=BROKER_METADATA_PATTERN means
# the pattern is confirmed by broker contract metadata over ev_range; anything
# outside ev_range is unverified and yields UNAVAILABLE unless allow_unverified.
# weekday: Mon=0..Sun=6. weekly False => monthly only (last `weekday` of the month).
_FAR = "2999-12-31"
RULES = [
    dict(v="NIFTY_WEEKLY_THU_v1", u="NIFTY", frm="2000-01-01", to="2025-08-31", weekday=3, weekly=True,
         ev_range=("2024-10-01", "2025-08-31")),
    dict(v="NIFTY_WEEKLY_TUE_v2", u="NIFTY", frm="2025-09-01", to=_FAR, weekday=1, weekly=True,
         ev_range=("2025-09-01", "2026-09-29")),
    dict(v="BANKNIFTY_MONTHLY_THU_v1", u="BANKNIFTY", frm="2025-01-01", to="2025-08-31", weekday=3, weekly=False,
         ev_range=("2025-01-01", "2025-08-31")),
    dict(v="BANKNIFTY_MONTHLY_TUE_v2", u="BANKNIFTY", frm="2025-09-01", to=_FAR, weekday=1, weekly=False,
         ev_range=("2025-09-01", "2026-09-29")),
    dict(v="SENSEX_WEEKLY_FRI_v1", u="SENSEX", frm="2000-01-01", to="2025-01-03", weekday=4, weekly=True,
         ev_range=("2024-10-01", "2025-01-03")),
    dict(v="SENSEX_WEEKLY_TUE_v2", u="SENSEX", frm="2025-01-04", to="2025-08-31", weekday=1, weekly=True,
         ev_range=("2025-01-04", "2025-08-31")),
    dict(v="SENSEX_WEEKLY_THU_v3", u="SENSEX", frm="2025-09-01", to=_FAR, weekday=3, weekly=True,
         ev_range=("2025-09-01", "2026-09-24")),
]
EXCHANGE = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE", "MIDCPNIFTY": "NSE", "SENSEX": "BSE", "BANKEX": "BSE"}
MCX_UNDERLYINGS = {"NATURALGAS", "NATGASMINI", "CRUDEOIL", "CRUDEOILM"}


def _d(s) -> date:
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


class Calendar:
    """Observed-trading-day calendar. is_open -> True / False / None (unknown)."""

    def __init__(self, exchange: str, days, source: str = "OBSERVED_TRADING_DAYS"):
        self.exchange, self.source = exchange, source
        self.days = {_d(x) for x in days}
        self.first, self.last = (min(self.days), max(self.days)) if self.days else (None, None)

    def is_open(self, d: date):
        if d.weekday() >= 5:
            return False            # weekends closed; no special sessions assumed
        if self.first is None or d < self.first or d > self.last:
            return None
        return d in self.days

    def prev_open(self, d: date, n: int = 1):
        cur, left = d, n
        while left:
            cur -= timedelta(days=1)
            o = self.is_open(cur)
            if o is None:
                return None
            if o:
                left -= 1
        return cur

    def on_or_before(self, d: date):
        o = self.is_open(d)
        if o is None:
            return None
        return d if o else self.prev_open(d, 1)


def load_observed_calendars() -> dict:
    with open(os.path.join(_DATA, "observed_trading_days.json")) as f:
        j = json.load(f)
    return {"NSE": Calendar("NSE", j["NIFTY_NSE"]), "BSE": Calendar("BSE", j["SENSEX_BSE"])}


def load_metadata() -> dict:
    with open(os.path.join(_DATA, "expiry_metadata_upstox.json")) as f:
        j = json.load(f)
    return {k: sorted(_d(x) for x in v) for k, v in j.items() if isinstance(v, list)}


@dataclass
class ExpiryResult:
    exchange: str
    underlying: str
    flag: str
    expiry_date: str | None
    expiry_status: str
    expiry_source: str
    reason: str | None = None
    rule_version: str | None = None
    rule_evidence: str | None = None
    metadata_expiry: str | None = None
    calculated_expiry: str | None = None
    expiry_rule_conflict: bool = False
    holiday_adjustment: str | None = None
    source_reference: str | None = None
    calculation_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self):
        return asdict(self)


def _rule_for(u: str, nominal: date):
    for r in RULES:
        if r["u"] == u and _d(r["frm"]) <= nominal <= _d(r["to"]):
            return r
    return None


def rule_expiries(u: str, year: int, month: int, cal: Calendar | None):
    """[(nominal, final, rule, holiday_adjustment)] for one month, sorted by final
    date. None if a rule version or calendar coverage needed is missing."""
    if cal is None:
        return None
    res, d = [], date(year, month, 1)
    same_weekday = {}
    while d.month == month:
        r = _rule_for(u, d)
        if r is not None and d.weekday() == r["weekday"]:
            same_weekday.setdefault(r["v"], []).append((d, r))
        d += timedelta(days=1)
    if not same_weekday:
        return None
    for v, items in same_weekday.items():
        chosen = items if items[0][1]["weekly"] else items[-1:]
        for nom, r in chosen:
            fin = cal.on_or_before(nom)
            if fin is None:
                return None
            res.append((nom, fin, r, None if fin == nom else "PREV_TRADING_DAY"))
    return sorted(res, key=lambda t: t[1])


class ExpiryResolver:
    def __init__(self, metadata: dict | None = None, calendars: dict | None = None,
                 allow_unverified_rules: bool = False):
        self.meta = metadata if metadata is not None else load_metadata()
        self.cals = calendars if calendars is not None else load_observed_calendars()
        self.allow_unverified = allow_unverified_rules

    @staticmethod
    def _unavail(ex, u, flag, reason, **kw):
        return ExpiryResult(ex, u, flag, None, UNAVAILABLE, SRC_NONE, reason=reason, **kw)

    def resolve(self, underlying: str, bar_date, *, flag: str = "WEEK", code: int = 1) -> ExpiryResult:
        """Expiry in force on `bar_date`: the `code`-th listed expiry on/after it.
        WEEK = every listed expiry; MONTH = last listed expiry of each month."""
        u, bd = underlying.upper(), _d(bar_date)
        ex = EXCHANGE.get(u) or ("MCX" if u in MCX_UNDERLYINGS else "UNKNOWN")
        if ex == "MCX":
            return self._unavail(ex, u, flag, "MCX_HISTORICAL_EXPIRY_NEEDS_FUTURES_EXPIRY_AND_MCX_CALENDAR")
        if ex == "UNKNOWN":
            return self._unavail(ex, u, flag, "UNKNOWN_UNDERLYING")
        if u == "BANKNIFTY" and flag == "WEEK" and bd >= date(2024, 12, 1):
            return self._unavail(ex, u, flag, "NO_WEEKLY_CONTRACTS_FOR_BANKNIFTY_FROM_2024-12",
                                 rule_version="BANKNIFTY_MONTHLY_ONLY")
        md = self.meta.get(u)
        if md and md[0] <= bd <= md[-1]:
            listed = md if flag == "WEEK" else [x for i, x in enumerate(md)
                                                if i + 1 == len(md) or md[i + 1].month != x.month]
            fut = [x for x in listed if x >= bd]
            if len(fut) >= code:
                m_exp = fut[code - 1]
                res = ExpiryResult(ex, u, flag, m_exp.isoformat(), VERIFIED, SRC_BROKER,
                                   metadata_expiry=m_exp.isoformat(),
                                   source_reference="Upstox /v2/expired-instruments/expiries (expiry_metadata_upstox.json)")
                calc = self._rule_calc(u, m_exp, flag)
                if calc is not None:
                    res.calculated_expiry = calc[0].isoformat()
                    res.rule_version = calc[1]["v"]
                    res.rule_evidence = "BROKER_METADATA_PATTERN"
                    res.holiday_adjustment = calc[2]
                    res.expiry_rule_conflict = calc[0] != m_exp      # metadata still wins
                return res
        return self._by_rule(ex, u, bd, flag, code)

    def _rule_calc(self, u, target: date, flag):
        cal, rows = self.cals.get(EXCHANGE.get(u)), []
        if _rule_for(u, target) is None:        # no rule version covers this date -> nothing to compare
            return None
        for k in (-1, 0, 1):
            y, m = target.year, target.month + k
            y, m = (y - 1, 12) if m == 0 else ((y + 1, 1) if m == 13 else (y, m))
            r = rule_expiries(u, y, m, cal)
            if r:
                rows += r[-1:] if flag == "MONTH" else r
        if not rows:
            return None
        near = min(rows, key=lambda t: abs((t[1] - target).days))
        return near[1], near[2], near[3]

    def _by_rule(self, ex, u, bd, flag, code):
        cal = self.cals.get(ex)
        cands, y, m = [], bd.year, bd.month
        for _ in range(4):
            rows = rule_expiries(u, y, m, cal)
            if rows is None:
                return self._unavail(ex, u, flag, "NO_RULE_VERSION_OR_CALENDAR_COVERAGE")
            pool = rows[-1:] if flag == "MONTH" else rows
            cands += [r for r in pool if r[1] >= bd]
            m += 1
            if m == 13:
                y, m = y + 1, 1
            if len(cands) >= code:
                break
        if len(cands) < code:
            return self._unavail(ex, u, flag, "COULD_NOT_ENUMERATE_EXPIRIES")
        nom, fin, rule, adj = sorted(cands, key=lambda t: t[1])[code - 1]
        verified = _d(rule["ev_range"][0]) <= bd <= _d(rule["ev_range"][1])
        if not verified and not self.allow_unverified:
            return self._unavail(ex, u, flag, "RULE_EVIDENCE_NOT_VERIFIED_FOR_THIS_PERIOD",
                                 rule_version=rule["v"], rule_evidence="USER_SPEC_UNVERIFIED",
                                 calculated_expiry=fin.isoformat())
        return ExpiryResult(ex, u, flag, fin.isoformat(), RECONSTRUCTED, SRC_RULE, rule_version=rule["v"],
                            rule_evidence="BROKER_METADATA_PATTERN" if verified else "USER_SPEC_UNVERIFIED",
                            calculated_expiry=fin.isoformat(), holiday_adjustment=adj,
                            source_reference=f"calendar={cal.source}")


def mcx_option_expiry(futures_expiry, calendar: Calendar | None, *, offset: int = 2,
                      underlying: str = "?") -> ExpiryResult:
    """MCX option expiry = `offset` MCX business days before the underlying
    FUTURES expiry. Needs an MCX calendar; without one -> UNAVAILABLE (no
    NSE/BSE/Mon-Fri substitute)."""
    if calendar is None or calendar.exchange != "MCX":
        return ExpiryResult("MCX", underlying, "MONTH", None, UNAVAILABLE, SRC_NONE, reason="NO_MCX_CALENDAR")
    fe = _d(futures_expiry)
    out = calendar.prev_open(fe, offset)
    if out is None:
        return ExpiryResult("MCX", underlying, "MONTH", None, UNAVAILABLE, SRC_NONE,
                            reason="MCX_CALENDAR_DOES_NOT_COVER_RANGE")
    return ExpiryResult("MCX", underlying, "MONTH", out.isoformat(), RECONSTRUCTED, SRC_RULE,
                        rule_version=f"MCX_FUT_MINUS_{offset}_BUSDAYS", rule_evidence="USER_SPEC_UNVERIFIED",
                        calculated_expiry=out.isoformat(), source_reference=f"futures_expiry={fe.isoformat()}")


def from_dhan_expiry_list(underlying: str, bar_date, iso_expiries: list[str], *, code: int = 1) -> ExpiryResult:
    """Current contract expiries straight from Dhan's option-chain expiry list
    (DHAN_METADATA; live/current contracts only)."""
    u, bd = underlying.upper(), _d(bar_date)
    ex = EXCHANGE.get(u, "MCX" if u in MCX_UNDERLYINGS else "UNKNOWN")
    flag = "MONTH" if ex == "MCX" else "WEEK"
    fut = sorted(_d(x) for x in iso_expiries if _d(x) >= bd)
    if len(fut) < code:
        return ExpiryResult(ex, u, flag, None, UNAVAILABLE, SRC_NONE, reason="NOT_IN_DHAN_LIST")
    return ExpiryResult(ex, u, flag, fut[code - 1].isoformat(), VERIFIED, SRC_DHAN,
                        metadata_expiry=fut[code - 1].isoformat(), source_reference="Dhan /v2/optionchain/expirylist")
