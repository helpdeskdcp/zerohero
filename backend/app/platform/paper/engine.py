"""Per-tenant PAPER trading (virtual capital). No broker object, order endpoint or network call exists here.

Fill model (executable prices, never LTP): BUY fills at the ASK (+ configured slippage points); every exit fills at the BID.
Exits: stop-loss and target are checked against the BID; a gap through the stop fills at the (worse) bid; a mandatory
intraday cutoff force-exits. If the quote is stale or missing nothing is simulated (no exit is fabricated) and the event is
journalled. All state is tenant_id-scoped; every open is idempotent per (tenant, master signal).
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from ..signals.personalize import PersonalSignal, TenantState
from ..tenancy import TenantStore

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_EXIT_CUTOFF = {"NSE": "15:20", "BSE": "15:20", "MCX": "23:15"}     # configurable; mandatory intraday exit
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id), signal_id TEXT NOT NULL,
  exchange TEXT NOT NULL, contract_key TEXT NOT NULL, contract_json TEXT NOT NULL, option_type TEXT NOT NULL,
  lots INTEGER NOT NULL, qty INTEGER NOT NULL, entry_ts REAL NOT NULL, entry_price REAL NOT NULL, ask_at_entry REAL NOT NULL,
  slippage_points REAL NOT NULL, sl_price REAL NOT NULL, target_price REAL NOT NULL, fees_round_trip REAL,
  cost_model_status TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', exit_ts REAL, exit_price REAL, exit_reason TEXT,
  gross_pnl REAL, net_pnl REAL, session_date TEXT NOT NULL, UNIQUE (tenant_id, signal_id));
CREATE INDEX IF NOT EXISTS ix_paper_pos_tenant ON paper_positions(tenant_id, status, session_date);
CREATE TABLE IF NOT EXISTS paper_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, tenant_id TEXT NOT NULL, event TEXT NOT NULL, detail_json TEXT NOT NULL);
"""


class PaperError(Exception):
    pass


def _ist_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).date().isoformat()


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


class PaperEngine:
    def __init__(self, store: TenantStore, *, exit_cutoff: dict | None = None, stale_quote_sec: float = 12.0,
                 entry_slippage_points: float = 0.0):
        self.store = store
        self.exit_cutoff = {**DEFAULT_EXIT_CUTOFF, **(exit_cutoff or {})}
        self.stale_sec, self.entry_slip = stale_quote_sec, float(entry_slippage_points)   # slippage is an ASSUMPTION, recorded per fill
        with store._c() as c:
            c.executescript(_SCHEMA)

    # ------------------------------------------------------------------ state
    def state(self, tenant_id: str, now: float) -> TenantState:
        prof = self.store.scope(tenant_id).profile()
        today = _ist_date(now)
        with self.store._c() as c:
            open_rows = c.execute("SELECT entry_price, qty FROM paper_positions WHERE tenant_id=? AND status='OPEN'", (tenant_id,)).fetchall()
            realized_all = c.execute("SELECT COALESCE(SUM(COALESCE(net_pnl, gross_pnl)),0) FROM paper_positions "
                                     "WHERE tenant_id=? AND status='CLOSED'", (tenant_id,)).fetchone()[0]
            realized_today = c.execute("SELECT COALESCE(SUM(COALESCE(net_pnl, gross_pnl)),0) FROM paper_positions WHERE tenant_id=? "
                                       "AND status='CLOSED' AND session_date=?", (tenant_id, today)).fetchone()[0]
            recent = [r[0] for r in c.execute("SELECT COALESCE(net_pnl, gross_pnl) FROM paper_positions WHERE tenant_id=? AND status='CLOSED' "
                                              "ORDER BY exit_ts DESC, id DESC LIMIT 20", (tenant_id,))]
        losses = 0
        for p in recent:
            if p is not None and p < 0:
                losses += 1
            else:
                break
        invested = sum(r["entry_price"] * r["qty"] for r in open_rows)
        cash = float(prof["virtual_capital"]) + realized_all - invested
        return TenantState(open_positions=len(open_rows), realized_pnl_today=float(realized_today),
                           consecutive_losses=losses, capital_available=max(0.0, cash))

    # ------------------------------------------------------------------ open
    def open_from_signal(self, ps: PersonalSignal, *, sl_fraction: float, target_rr: float, exchange: str, now: float) -> dict:
        tid = ps.tenant_id
        prof = self.store.scope(tid).profile()
        if prof["execution_mode"] != "PAPER":
            self._journal(tid, now, "REFUSED", {"reason": "mode is not PAPER"})
            raise PaperError("paper engine only trades PAPER-mode tenants")
        if not ps.eligible or ps.contract is None or not ps.lots:
            self._journal(tid, now, "REFUSED", {"reason": "personal signal not eligible", "rejections": ps.rejections})
            raise PaperError("ineligible personal signal")
        if not (0 < sl_fraction < 1) or target_rr <= 0:
            raise PaperError("invalid stop/target parameters")
        with self.store._c() as c:
            c.execute("BEGIN IMMEDIATE")
            dup = c.execute("SELECT id FROM paper_positions WHERE tenant_id=? AND signal_id=?", (tid, ps.master_signal_id)).fetchone()
            if dup:
                c.rollback()
                return self._position(tid, dup[0])
            st = self._cash_locked(c, tid, float(prof["virtual_capital"]))
            fill = round(ps.entry_premium + self.entry_slip, 4)
            cost = fill * ps.qty
            if cost > st:
                self._journal_c(c, tid, now, "REFUSED", {"reason": "INSUFFICIENT_CAPITAL_AT_FILL", "needed": cost, "cash": st})
                c.commit()
                raise PaperError("insufficient capital at fill")
            sl = round(fill * (1 - sl_fraction), 4)
            tgt = round(fill + (fill - sl) * target_rr, 4)
            key = f"{ps.contract.get('strike')}{ps.contract.get('option_type', '')}:{ps.contract.get('security_id') or ''}"
            cur = c.execute(
                "INSERT INTO paper_positions (tenant_id, signal_id, exchange, contract_key, contract_json, option_type, lots, qty, entry_ts, "
                "entry_price, ask_at_entry, slippage_points, sl_price, target_price, fees_round_trip, cost_model_status, session_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, ps.master_signal_id, exchange.upper(), key, json.dumps(ps.contract, default=str), ps.direction, ps.lots, ps.qty, now,
                 fill, ps.entry_premium, self.entry_slip, sl, tgt, ps.fees_round_trip, ps.cost_model_status, _ist_date(now)))
            pid = cur.lastrowid
            self._journal_c(c, tid, now, "OPENED", {"position_id": pid, "fill": fill, "ask": ps.entry_premium, "qty": ps.qty,
                                                    "sl": sl, "target": tgt, "slippage_points_assumed": self.entry_slip})
            c.commit()
        return self._position(tid, pid)

    # ------------------------------------------------------------------ mark / exit
    def mark(self, tenant_id: str, quotes: dict, now: float) -> list[dict]:
        """quotes: contract_key -> {"bid": float, "ask": float, "ts": epoch}. Returns positions closed on this call."""
        closed = []
        with self.store._c() as c:
            rows = c.execute("SELECT * FROM paper_positions WHERE tenant_id=? AND status='OPEN'", (tenant_id,)).fetchall()
        for r in rows:
            q = quotes.get(r["contract_key"])
            cutoff = self.exit_cutoff.get(r["exchange"], "15:20")
            due_cutoff = _hhmm(now) >= cutoff and _ist_date(now) >= r["session_date"]
            if not q or q.get("bid") is None or now - float(q.get("ts", 0)) > self.stale_sec:
                self._journal(tenant_id, now, "STALE_OR_MISSING_QUOTE", {"position_id": r["id"], "cutoff_due": due_cutoff})
                continue                                      # never fabricate an exit from stale data
            bid = float(q["bid"])
            reason = None
            if bid <= r["sl_price"]:
                reason = "STOP_LOSS"
            elif bid >= r["target_price"]:
                reason = "TARGET"
            elif due_cutoff:
                reason = "TIME_CUTOFF"
            if reason:
                closed.append(self._close(r, bid, reason, now))
        return closed

    def _close(self, r, bid: float, reason: str, now: float) -> dict:
        gross = round((bid - r["entry_price"]) * r["qty"], 2)
        net = round(gross - r["fees_round_trip"], 2) if r["fees_round_trip"] is not None else None
        with self.store._c() as c:
            c.execute("UPDATE paper_positions SET status='CLOSED', exit_ts=?, exit_price=?, exit_reason=?, gross_pnl=?, net_pnl=? "
                      "WHERE id=? AND status='OPEN'", (now, bid, reason, gross, net, r["id"]))
            self._journal_c(c, r["tenant_id"], now, "CLOSED", {"position_id": r["id"], "reason": reason, "exit_bid": bid,
                                                              "gross": gross, "net": net, "cost_model_status": r["cost_model_status"]})
        return self._position(r["tenant_id"], r["id"])

    # ------------------------------------------------------------------ helpers
    def _cash_locked(self, c, tid, capital) -> float:
        realized = c.execute("SELECT COALESCE(SUM(COALESCE(net_pnl, gross_pnl)),0) FROM paper_positions WHERE tenant_id=? AND status='CLOSED'", (tid,)).fetchone()[0]
        invested = c.execute("SELECT COALESCE(SUM(entry_price*qty),0) FROM paper_positions WHERE tenant_id=? AND status='OPEN'", (tid,)).fetchone()[0]
        return capital + realized - invested

    def _position(self, tenant_id: str, pid: int) -> dict:
        with self.store._c() as c:
            r = c.execute("SELECT * FROM paper_positions WHERE id=? AND tenant_id=?", (pid, tenant_id)).fetchone()
        return dict(r) if r else {}

    def positions(self, tenant_id: str, status: str | None = None) -> list[dict]:
        q, p = "SELECT * FROM paper_positions WHERE tenant_id=?", [tenant_id]
        if status:
            q += " AND status=?"; p.append(status)
        with self.store._c() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY id", p)]

    def journal(self, tenant_id: str) -> list[dict]:
        with self.store._c() as c:
            return [{"ts": r["ts"], "event": r["event"], "detail": json.loads(r["detail_json"])}
                    for r in c.execute("SELECT * FROM paper_journal WHERE tenant_id=? ORDER BY id", (tenant_id,))]

    def _journal(self, tid, now, event, detail):
        with self.store._c() as c:
            self._journal_c(c, tid, now, event, detail)

    @staticmethod
    def _journal_c(c, tid, now, event, detail):
        c.execute("INSERT INTO paper_journal (ts, tenant_id, event, detail_json) VALUES (?,?,?,?)", (now, tid, event, json.dumps(detail, default=str)))
