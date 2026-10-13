"""G5: per-tenant paper engine — executable-price fills, idempotency, isolation, stale-quote honesty, cutoff."""
import pytest

from app.platform.paper.engine import PaperEngine, PaperError
from app.platform.signals.personalize import PersonalSignal, TenantState
from app.platform.tenancy import TenantStore

import contextlib


@contextlib.contextmanager
def raises_paper():
    # compare by name: other suites reload app.* modules, so the class object can differ across the full run
    with pytest.raises(Exception) as ei:
        yield
    assert type(ei.value).__name__ == "PaperError"


T0 = 1_760_000_000.0          # arbitrary epoch; IST wall-clock derived inside engine


@pytest.fixture
def env(tmp_path):
    st = TenantStore(str(tmp_path / "p.db"))
    st.create_tenant("ta", "A", virtual_capital=100000.0)
    st.create_tenant("tb", "B", virtual_capital=100000.0)
    return st, PaperEngine(st)


def ps(tid, sid="s1", ask=100.0, lots=1, qty=50, fees=10.0, eligible=True):
    return PersonalSignal(tid, sid, eligible, "CE", contract={"strike": 24000, "option_type": "CE", "security_id": "X1"},
                          band="ATM", lots=lots, qty=qty, entry_premium=ask, fees_round_trip=fees, cost_model_status="OK")


KEY = "24000CE:X1"


def q(bid, ts):
    return {KEY: {"bid": bid, "ask": bid + 1, "ts": ts}}


def test_open_fills_at_ask_and_is_idempotent(env):
    st, e = env
    p = e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    assert p["entry_price"] == 100.0 and p["sl_price"] == 80.0 and p["target_price"] == 140.0
    again = e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0 + 1)
    assert again["id"] == p["id"] and len(e.positions("ta")) == 1


def test_stop_target_use_bid_and_net_of_fees(env):
    st, e = env
    e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    assert e.mark("ta", q(95, T0 + 5), T0 + 5) == []
    closed = e.mark("ta", q(78, T0 + 10), T0 + 10)
    assert closed[0]["exit_reason"] == "STOP_LOSS" and closed[0]["exit_price"] == 78      # gap through stop fills at the worse bid
    assert closed[0]["gross_pnl"] == -1100.0 and closed[0]["net_pnl"] == -1110.0
    s = e.state("ta", T0 + 10)
    assert s.consecutive_losses == 1 and s.realized_pnl_today == -1110.0 and s.open_positions == 0


def test_stale_or_missing_quote_never_fabricates_exit(env):
    st, e = env
    e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    assert e.mark("ta", q(10, T0 - 100), T0 + 60) == []          # stale
    assert e.mark("ta", {}, T0 + 60) == []                       # missing
    assert e.positions("ta", "OPEN") and any(j["event"] == "STALE_OR_MISSING_QUOTE" for j in e.journal("ta"))


def test_time_cutoff_exit(env):
    st, e = env
    e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    # find a timestamp same IST day at/after 15:20
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    d = datetime.fromtimestamp(T0, ist).replace(hour=15, minute=25, second=0)
    late = d.timestamp()
    c = e.mark("ta", q(100, late), late)
    assert c and c[0]["exit_reason"] == "TIME_CUTOFF"


def test_tenant_isolation_and_state(env):
    st, e = env
    e.open_from_signal(ps("ta"), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    assert e.positions("tb") == [] and e.state("tb", T0).open_positions == 0
    assert e.state("ta", T0).capital_available == pytest.approx(100000.0 - 5000.0)
    assert e.mark("tb", q(1, T0), T0) == [] and e.positions("ta", "OPEN")     # b's marks never touch a


def test_refusals(env):
    st, e = env
    with raises_paper():
        e.open_from_signal(ps("ta", eligible=False), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)
    with raises_paper():
        e.open_from_signal(ps("ta", ask=5000.0, qty=50), sl_fraction=0.2, target_rr=2, exchange="NSE", now=T0)   # 250k > capital
    with raises_paper():
        e.open_from_signal(ps("ta"), sl_fraction=0, target_rr=2, exchange="NSE", now=T0)
    assert e.positions("ta") == [] and any(j["event"] == "REFUSED" for j in e.journal("ta"))
