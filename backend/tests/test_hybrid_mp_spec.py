"""Spec-level tests from the master prompt: profile detection, opening gap, alerts/dedupe, VWAP-missing policy, MTF compatibility,
pending-setup interactions and expiry boundaries."""
import datetime as dt
import os

import numpy as np
import pytest

from app.research_engines.hybrid_mp.alerts import AlertDeduper, events_to_alerts
from app.research_engines.hybrid_mp.engine import Params, run
from app.research_engines.hybrid_mp.gap import gap_text, opening_gap_pine, opening_gap_session_aware
from app.research_engines.hybrid_mp.profile import detect_profile_corrected, detect_profile_pine
from tests.test_hybrid_mp import BASE, P, T0, mk
from tests.test_hybrid_mp_audit import synth

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PINE = os.path.join(os.path.dirname(__file__), "..", "app", "research_engines", "hybrid_mp", "pine", "hybrid_mp_v13.pine")


# ------------------------------------------------------------------ profile detection
@pytest.mark.parametrize("ticker,prefix,typ,pine_expect,fixed_expect", [
    ("NIFTY", "NSE", "index", "Indian Index/Options", "Indian Index/Options"),
    ("BANKNIFTY", "NSE", "index", "Indian Index/Options", "Indian Index/Options"),
    ("SENSEX", "BSE", "index", "Indian Index/Options", "Indian Index/Options"),
    ("CRUDEOIL1!", "MCX", "futures", "MCX", "MCX"),
    ("NIFTY26OCT22500CE", "NSE", "option", "Indian Index/Options", "Indian Index/Options"),
    ("BTCUSDT", "BINANCE", "crypto", "Forex/Crypto", "Forex/Crypto"),
    ("EURUSD", "OANDA", "forex", "Forex/Crypto", "Forex/Crypto"),
    ("TCS", "NSE", "stock", "Indian Equity", "Indian Equity"),
    # --- Pine false positives (the corrected detector must differ) ---
    ("RELIANCE", "NSE", "stock", "Indian Index/Options", "Indian Equity"),          # "CE" substring
    ("SPICEJET", "NSE", "stock", "Indian Index/Options", "Indian Equity"),          # "CE"
    ("PERSISTENT", "NSE", "stock", "Indian Index/Options", "Indian Equity"),        # "PE"
    ("GOLDBEES", "NSE", "fund", "MCX", "Indian Equity"),                           # "GOLD" substring -> wrong (MCX) session
    ("SILVERBEES", "NSE", "fund", "MCX", "Indian Equity"),
])
def test_profile_detection_pine_rules_and_corrected_rules(ticker, prefix, typ, pine_expect, fixed_expect):
    assert detect_profile_pine(ticker, prefix, typ)["profile"] == pine_expect
    assert detect_profile_corrected(ticker, prefix, typ)["profile"] == fixed_expect


def test_unknown_exchange_uses_fallback_and_profile_known_false_but_pine_still_trades():
    d = detect_profile_pine("AAPL", "NASDAQ", "stock", fallback="Indian Equity")
    assert d["profile"] == "Indian Equity" and d["known"] is False
    assert detect_profile_corrected("AAPL", "NASDAQ", "stock")["known"] is False
    src = open(PINE).read()
    buy_line = next(l for l in src.splitlines() if l.startswith("buySignal ="))
    assert "profileKnown" not in buy_line                  # confirmed: the dashboard says PROFILE NOT RECOGNIZED but the signal is not gated
    # explicit profile overrides detection and makes it "known"
    assert detect_profile_pine("AAPL", "NASDAQ", "stock", profile_input="MCX")["known"] is True


# ------------------------------------------------------------------ opening gap
def day_bars(date, n=375, first=(555), px=100.0, drop_last=0, extra_after=0, start_min=555):
    base = int(dt.datetime(date.year, date.month, date.day, 0, 0, tzinfo=IST).timestamp()) + start_min * 60
    rows = [(px, px + 1, px - 1, px) for _ in range(n - drop_last)]
    t = [base + i * 60 for i in range(n - drop_last)]
    for k in range(extra_after):                           # bars after the close (extended hours) at a different price
        rows.append((px + 7, px + 8, px + 6, px + 7)); t.append(base + (n + k) * 60)
    a = np.array(rows)
    return t, a


def build(days_spec):
    T, A = [], []
    for date, kw in days_spec:
        t, a = day_bars(date, **kw)
        T += t; A.append(a)
    a = np.vstack(A)
    return {"t": np.array(T, dtype=np.int64), "o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": np.zeros(len(T))}


def first_bar_gap(res, chart, day):
    t = chart["t"]
    idx = [i for i in range(len(t)) if dt.datetime.fromtimestamp(int(t[i]), IST).date() == day][0]
    return res["gap_pct"][idx]


def test_gap_equal_when_bars_are_contiguous_regular_session():
    d1, d2 = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    ch = build([(d1, dict(px=100.0)), (d2, dict(px=101.0))])
    assert first_bar_gap(opening_gap_pine(ch), ch, d2) == pytest.approx(1.0)
    assert first_bar_gap(opening_gap_session_aware(ch), ch, d2) == pytest.approx(1.0)
    assert gap_text(1.0) == "GAP UP 1.00%" and gap_text(-0.2) == "GAP DOWN -0.20%" and gap_text(0.05) == "FLAT OPEN" and gap_text(float("nan")) == "GAP N/A"


def test_gap_pine_is_wrong_when_extended_hours_bars_follow_the_close_and_corrected_is_right():
    d1, d2 = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    ch = build([(d1, dict(px=100.0, extra_after=3)), (d2, dict(px=101.0))])       # 3 post-close bars at 107
    pine = first_bar_gap(opening_gap_pine(ch), ch, d2)
    fixed = first_bar_gap(opening_gap_session_aware(ch), ch, d2)
    assert pine == pytest.approx((101 - 107) / 107 * 100) and fixed == pytest.approx(1.0)       # Pine measured against the 107 post-market bar


def test_gap_flags_a_prior_session_whose_last_candles_are_missing():
    d1, d2 = dt.date(2026, 10, 13), dt.date(2026, 10, 14)
    ch = build([(d1, dict(px=100.0, drop_last=20)), (d2, dict(px=101.0))])
    r = opening_gap_session_aware(ch)
    idx = [i for i in range(len(ch["t"])) if dt.datetime.fromtimestamp(int(ch["t"][i]), IST).date() == d2][0]
    assert r["incomplete_prior"][idx] and r["gap_pct"][idx] == pytest.approx(1.0)


def test_gap_over_weekend_holiday_and_first_day_and_mcx_rollover():
    fri, mon = dt.date(2026, 10, 9), dt.date(2026, 10, 12)
    ch = build([(fri, dict(px=100.0)), (mon, dict(px=99.0))])
    assert first_bar_gap(opening_gap_session_aware(ch), ch, mon) == pytest.approx(-1.0)
    assert np.isnan(first_bar_gap(opening_gap_session_aware(ch), ch, fri)) and np.isnan(first_bar_gap(opening_gap_pine(ch), ch, fri))
    d1, d2 = dt.date(2026, 10, 13), dt.date(2026, 10, 14)                       # MCX 09:00-23:30 (870 bars)
    m = build([(d1, dict(n=870, px=50.0, start_min=540)), (d2, dict(n=870, px=51.0, start_min=540))])
    assert first_bar_gap(opening_gap_session_aware(m, (540, 1410)), m, d2) == pytest.approx(2.0)
    assert first_bar_gap(opening_gap_pine(m), m, d2) == pytest.approx(2.0)


# ------------------------------------------------------------------ alerts
def test_alert_mapping_ids_and_dedupe_on_replay():
    b = synth(9, sessions=6)
    _, st = run(b, Params(fill="close", gap_fills=False, eod_flat=False, exit_model="PINE"), want_events=True)
    al = events_to_alerts(st["events"], b["t"], "NIFTY", 1)
    names = {a["name"] for a in al}
    assert {"BUY Confirmed", "SELL Confirmed", "Stop Loss Hit", "Reversal Confirmed"} <= names
    assert len({a["id"] for a in al}) == len(al)                                    # deterministic and unique per (bar, event, direction)
    d = AlertDeduper()
    first = d.fresh(al)
    assert len(first) == len(al)
    assert d.fresh(al) == []                                                         # same candles processed again -> nothing new
    overlap = events_to_alerts(st["events"], b["t"], "NIFTY", 1)[-10:]
    assert d.fresh(overlap) == []                                                    # overlapping window (restart/replay)
    assert events_to_alerts(st["events"], b["t"], "NIFTY", 1)[0]["id"] == al[0]["id"]


def test_signal_without_trade_still_alerts_in_pine_but_is_flagged():
    al = events_to_alerts([(3, "SIGNAL_NO_TRADE", 1)], [0, 60, 120, 180], "X", 1)
    assert al[0]["name"] == "BUY Confirmed" and al[0]["no_trade"] is True


# ------------------------------------------------------------------ VWAP policy and MTF compatibility
def test_missing_vwap_lets_trades_through_by_default_and_can_be_made_to_block():
    b = mk(BASE + [(105.8, 106, 105, 105.5)])                      # volume all zero -> VWAP unavailable
    tr, _ = run(b, Params(use_vwap=True, **P))
    assert len(tr) == 1                                             # Pine behaviour: unavailable VWAP = pass (flagged as potentially unsafe)
    tr, st = run(b, Params(use_vwap=True, vwap_missing_blocks=True, **P))
    assert tr == []


def test_mtf_timeframes_lower_than_the_chart_timeframe_are_flagged():
    b = synth(4, sessions=3)
    _, st = run(b, Params(require_mtf=True, chart_tf=5), b1=b)
    assert st["mtf_incompatible_tfs"] == [3]
    _, st = run(b, Params(require_mtf=True, chart_tf=1), b1=b)
    assert st["mtf_incompatible_tfs"] == []


# ------------------------------------------------------------------ pending setups
HEAD = BASE[:6]
BEARISH_BREAK = (107, 108, 105.2, 105.5)                          # close 105.5 > swing 105 (break) but a BEARISH candle: no continuation
QUIET = (106, 107, 105.8, 106.5)                                   # low stays above level + tolerance: no retest
RETEST = (105.6, 106.9, 104.9, 106.6)


def _events(rows, **kw):
    _, st = run(mk(HEAD + rows), Params(entry_mode="retest", **P, **kw), want_events=True)
    return st["events"]


def test_pending_retest_allowed_on_the_last_valid_bar_and_expired_one_bar_later():
    ok = _events([BEARISH_BREAK, QUIET, QUIET, RETEST], retest_bars=3)               # retest on bar 9 = 6 + 3
    assert any(e[1] == "SIGNAL" and e[0] == 9 for e in ok)
    late = _events([BEARISH_BREAK, QUIET, QUIET, QUIET, RETEST], retest_bars=3)      # retest on bar 10 = 6 + 4 -> pending already expired
    assert any(e[1] == "PENDING_EXPIRED" and e[0] == 10 for e in late) and not any(e[1] == "SIGNAL" for e in late)


def test_retest_takes_precedence_over_continuation_when_both_conditions_hold():
    rows = [BEARISH_BREAK, (105.4, 109, 104.9, 108.5)]                                # retests the level AND closes above the prior 3 highs
    _, st = run(mk(HEAD + rows), Params(**P), want_events=True)                       # hybrid mode
    sig = [e for e in st["events"] if e[1] == "SIGNAL"]
    assert sig and sig[0][0] == 7 and sig[0][3] == "RETEST"


def test_opposite_pending_blocks_a_new_setup_but_a_continuation_entry_still_fires_and_consumes_it():
    rows = [(100, 101, 99, 100), (100, 102, 99.5, 101), (101, 105, 100, 102), (102, 103, 98, 99), (99, 100, 96, 97),
            (97, 98.5, 96.5, 98), (98, 99, 97, 98.5),
            (93, 95.9, 92.5, 95.5),          # bar 7: closes below swing low 96 (break) on a BULLISH candle -> pending SELL, no continuation
            (95.6, 100, 95.4, 99.8),
            (99.9, 106.5, 99.6, 106.2)]      # bar 9: breaks swing high 105 (CHoCH up) with a bullish marubozu above the prior 3 highs
    b = mk(rows)
    _, st = run(b, Params(**P), want_events=True)
    ev = st["events"]
    assert (7, "PENDING_SET", -1, 96.0) in [tuple(e) for e in ev]
    assert (9, "CHOCH", 1) in [tuple(e) for e in ev]
    assert not any(e[1] == "PENDING_SET" and e[0] == 9 for e in ev)                    # the new bullish setup is NOT created while the SELL setup is pending
    sig = [e for e in ev if e[1] == "SIGNAL"]
    assert sig and sig[0][0] == 9 and sig[0][2] == 1 and sig[0][3] == "CONT"           # ... but the continuation buy fires
    from app.research_engines.hybrid_mp.reference_literal import run_literal
    from tests.test_hybrid_mp_audit import same
    ref = run_literal(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"], b, dict(pivotLen=2, atrLen=3, continuationLookback=3, mintick=0.05))
    assert same(ref, ev)
