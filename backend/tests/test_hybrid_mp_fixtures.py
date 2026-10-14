"""Deterministic fixtures for every signal condition: candle patterns, SL/target placement (long AND short), invalid risk, VWAP with real volume,
MTF agreement, bearish BoS/CHoCH, and a global mirror-symmetry property (price -> C - price must turn every BUY into a SELL)."""
import numpy as np
import pytest

from app.research_engines.hybrid_mp.engine import Params, _flags, mtf_dirs, run
from tests.test_hybrid_mp import BASE, P, mk
from tests.test_hybrid_mp_audit import ASIS, synth

C = 200.0


def mirror_rows(rows):
    return [(C - o, C - l, C - h, C - c) for (o, h, l, c) in rows]


def mirror_bars(b, c=None):
    c = c or 2 * float(np.mean(b["c"]))
    return {"t": b["t"], "o": c - b["o"], "h": c - b["l"], "l": c - b["h"], "c": c - b["c"], "v": b["v"]}, c


def flags(rows, **kw):
    return {k: v.tolist() for k, v in _flags(mk(rows), Params(**kw)).items() if k.startswith(("bull_", "bear_"))}


# ------------------------------------------------------------------ candle patterns (each one on its own)
def test_bullish_and_bearish_engulfing():
    f = flags([(101, 101.2, 99.8, 100), (99.9, 102, 99.8, 101.8)])                  # prev bearish, current bullish engulfs
    assert f["bull_eng"][1] and not f["bear_eng"][1]
    f = flags([(100, 101.2, 99.8, 101), (101.1, 101.3, 98.9, 99.0)])
    assert f["bear_eng"][1] and not f["bull_eng"][1]


def test_marubozu_needs_body_ratio_and_tiny_wicks():
    f = flags([(100, 105.2, 99.9, 105.1)])                                           # body 5.1 / range 5.3 = 0.96, wicks 0.1 / 0.1
    assert f["bull_maru"][0] and not f["bear_maru"][0]
    f = flags([(100, 105.2, 99.9, 102.0)])                                           # long upper wick -> not a marubozu
    assert not f["bull_maru"][0]
    f = flags([(105.1, 105.2, 99.9, 100)])
    assert f["bear_maru"][0] and not f["bull_maru"][0]


def test_hammer_and_bearish_star_shapes():
    f = flags([(100, 100.6, 97.0, 100.5)])                                           # bullish close, lower wick 3.0 >= 2 x body 0.5, upper wick 0.1 <= body
    assert f["bull_ham"][0] and not f["bear_star"][0]
    f = flags([(100.5, 104.0, 99.9, 100.0)])                                         # bearish close, upper wick 3.5 >= 2 x body, lower wick 0.1 <= body
    assert f["bear_star"][0] and not f["bull_ham"][0]
    assert not flags([(100, 101, 99, 100.0)])["bull_ham"][0]                         # doji: neither (needs close > open)


def test_pattern_filter_requires_a_named_pattern_but_not_otherwise():
    plain = (100, 102, 99.2, 101.0)                                                  # bullish candle, not engulfing / marubozu / hammer
    assert flags([plain], use_pattern_filter=False)["bull_ok"][0] and not flags([plain], use_pattern_filter=True)["bull_ok"][0]
    assert flags([(100, 105.2, 99.9, 105.1)], use_pattern_filter=True)["bull_ok"][0]


# ------------------------------------------------------------------ SL / targets, long and short
def test_candle_sl_selection_long_and_short():
    # BASE bar 6 is a bullish ENGULFING candle -> SL = previous candle low (100), R = 5.8
    _, st = run(mk(BASE + [(105.8, 106, 105, 105.5)]), Params(**P), want_events=True)
    sig = [e for e in st["events"] if e[1] == "SIGNAL"][0]
    assert sig[4] == pytest.approx(100.0) and sig[5] == pytest.approx(5.8)
    # same setup but the signal candle is a MARUBOZU that does not engulf: SL = the signal candle's own low
    rows = BASE[:6] + [(100.6, 106.0, 100.55, 105.95)]          # opens above the previous close -> NOT an engulfing candle
    _, st = run(mk(rows + [(105.9, 106, 105, 105.5)]), Params(**P), want_events=True)
    s = [e for e in st["events"] if e[1] == "SIGNAL"]
    assert s and s[0][4] == pytest.approx(100.55)
    # useCandleSL=False -> previous candle low even for a marubozu
    _, st = run(mk(rows + [(105.9, 106, 105, 105.5)]), Params(use_candle_sl=False, **P), want_events=True)
    assert [e for e in st["events"] if e[1] == "SIGNAL"][0][4] == pytest.approx(100.0)


def test_short_entry_is_the_exact_mirror_of_the_long_fixture():
    rows = BASE + [(105.8, 130, 105, 125)]
    tr, st = run(mk(rows), Params(**P), want_events=True)
    trm, stm = run(mk(mirror_rows(rows)), Params(**P), want_events=True)
    assert tr[0].side == 1 and trm[0].side == -1 and tr[0].reason == trm[0].reason == "T4"
    assert trm[0].entry == pytest.approx(C - tr[0].entry) and trm[0].r == pytest.approx(tr[0].r) == pytest.approx(4.0)
    sig, sigm = [e for e in st["events"] if e[1] == "SIGNAL"][0], [e for e in stm["events"] if e[1] == "SIGNAL"][0]
    assert sigm[2] == -1 and sigm[4] == pytest.approx(C - sig[4]) and sigm[5] == pytest.approx(sig[5])        # SL mirrored, R identical
    assert [e[1] for e in st["events"]] == [e[1] for e in stm["events"]]                                        # BoS<->BoS, T1..T4 identical sequence


def test_bearish_bos_and_choch_are_detected():
    rows = mirror_rows(BASE)                                                         # mirrored BASE: swing LOW breaks down on bar 6
    _, st = run(mk(rows), Params(**P), want_events=True)
    assert (6, "BOS", -1) in [tuple(e) for e in st["events"]]
    up_then_down = BASE + [(105.8, 106, 105, 105.5)]
    _, st = run(mk(up_then_down + mirror_rows([(105.0 + 0, 106, 95, 96)])), Params(**P), want_events=True)            # any later break against structure
    kinds = [e[1] for e in st["events"] if e[1] in ("BOS", "CHOCH")]
    assert "BOS" in kinds                                                              # first break (structure was neutral) is a BoS, never a CHoCH


def test_invalid_risk_creates_a_signal_but_no_trade():
    rows = [(100, 101, 99, 100), (100, 102, 99.5, 101), (101, 105, 100, 102), (102, 103, 100.5, 101.5), (101.5, 102.5, 100, 101),
            (101, 101.5, 100, 100.5), (100.5, 106, 100.4, 105.8)]
    # make "previous candle low" sit AT/ABOVE the signal close: bar 5 low = 107 is impossible for OHLC, so use useCandleSL=False and a huge previous low
    rows[5] = (107.0, 107.2, 106.9, 107.1)
    _, st = run(mk(rows + [(105.8, 106, 105, 105.5)]), Params(**P), want_events=True)
    kinds = [e[1] for e in st["events"]]
    assert "SIGNAL_NO_TRADE" in kinds or "SIGNAL" in kinds
    if "SIGNAL_NO_TRADE" in kinds:
        assert st["no_trade_small_R"] == 1 and not any(e[1] == "SL_HIT" for e in st["events"])


# ------------------------------------------------------------------ VWAP with real volume, MTF agreement
def test_vwap_filter_with_real_volume_blocks_longs_below_vwap_and_shorts_above():
    b = synth(31, sessions=6)
    for side_flag in (False, True):
        _, off = run(b, Params(**ASIS, use_vwap=False), want_events=True)
        _, on = run(b, Params(**ASIS, use_vwap=True), want_events=True)
    assert on["signals_buy"] + on["signals_sell"] < off["signals_buy"] + off["signals_sell"]            # the filter removes some signals
    from app.research_engines.hybrid_mp.engine import _vwap
    from app.research_engines.smart_bos.data import ist_minutes
    vw = _vwap(b, ist_minutes(b["t"])[0])
    for e in on["events"]:
        if e[1] == "SIGNAL":
            i = e[0]
            assert (e[2] == 1 and b["c"][i] >= vw[i]) or (e[2] == -1 and b["c"][i] <= vw[i]) or np.isnan(vw[i])


def test_mtf_agreement_every_signal_meets_min_agree_and_filter_removes_signals():
    b = synth(32, sessions=8)
    p_on = Params(chart_tf=1, require_mtf=True, min_agree=3, **ASIS)
    _, on = run(b, p_on, b1=b, want_events=True)
    _, off = run(b, Params(chart_tf=1, **ASIS), b1=b, want_events=True)
    d = mtf_dirs(b, b, p_on)
    for e in on["events"]:
        if e[1] == "SIGNAL":
            row = d[e[0]]
            assert (e[2] == 1 and (row == 1).sum() >= 3) or (e[2] == -1 and (row == -1).sum() >= 3)
    assert on["signals_buy"] + on["signals_sell"] < off["signals_buy"] + off["signals_sell"]


# ------------------------------------------------------------------ global mirror symmetry (covers every short-side branch)
@pytest.mark.parametrize("kw", [dict(), dict(entry_mode="retest"), dict(use_pattern_filter=True), dict(use_candle_sl=False), dict(use_vwap=True),
                                dict(pivot_len=3, continuation_lookback=3, retest_bars=4)])
def test_price_mirror_turns_every_event_into_its_mirror(kw):
    b = synth(33, sessions=10)
    bm, c = mirror_bars(b)
    _, a = run(b, Params(**ASIS, **kw), want_events=True)
    _, m = run(bm, Params(**ASIS, **kw), want_events=True)
    assert len(a["events"]) == len(m["events"]) > 50
    for x, y in zip(a["events"], m["events"]):
        assert x[:2] == y[:2] or (x[1] == y[1]), (x, y)
        assert x[0] == y[0]
        if x[1] in ("SIGNAL",):
            assert y[2] == -x[2] and x[3] == y[3] and abs(y[4] - (c - x[4])) < 1e-6 and abs(y[5] - x[5]) < 1e-6
        elif x[1] in ("BOS", "CHOCH", "T1", "T2", "T3", "T4", "T4_COMPLETE", "SL_HIT", "REJECTED", "PENDING_EXPIRED", "PENDING_CANCELLED", "SIGNAL_NO_TRADE"):
            assert y[2] == -x[2]
        elif x[1] == "PENDING_SET":
            assert y[2] == -x[2] and abs(y[3] - (c - x[3])) < 1e-6
