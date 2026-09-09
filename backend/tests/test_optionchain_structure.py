"""
app/optionchain/ Layer 2 (analytics) + Layer 3 (structure engine) -- offline.

  * bs.py          -- put-call parity, greek signs, IV round-trip
  * analytics.py   -- pcr / max_pain (true writer payout) / iv_skew / oi_walls /
                      gex / oi_change_vs_baseline
  * structure.py   -- OptionStructureState: full read, capability-degraded reads,
                      threshold config, to_dict / JSON round-trip
No network, no DB.
"""
import json
import math

import pytest

from app.optionchain import bs
from app.optionchain import analytics as A
from app.optionchain import structure as S
from app.optionchain.chain import OptionChain, StrikeRow, OptionLeg


# --------------------------------------------------------------------------- #
#  synthetic chains                                                            #
# --------------------------------------------------------------------------- #
def _chain(spot=23450.0, expiry="15SEP2026", *, lo=22500, hi=24400, step=50,
           sigma=0.12, ce_oi_fn=None, pe_oi_fn=None, with_greeks=True,
           with_oi=True, with_vol=True, ts="2026-09-09T06:00:00Z"):
    T = bs.year_fraction(expiry, __import__("datetime").datetime(2026, 9, 9,
                                                                 tzinfo=__import__("datetime").timezone.utc))
    ce_oi_fn = ce_oi_fn or (lambda k: 5000.0 + max(0.0, (k - spot)) * 4)
    pe_oi_fn = pe_oi_fn or (lambda k: 5000.0 + max(0.0, (spot - k)) * 4)
    rows = []
    for k in range(lo, hi + 1, step):
        kk = float(k)
        legs = {}
        for side, is_call, oif in (("ce", True, ce_oi_fn), ("pe", False, pe_oi_fn)):
            g = bs.greeks(spot, kk, T, sigma, is_call) if with_greeks else None
            legs[side] = OptionLeg(
                ltp=bs.price(spot, kk, T, sigma, is_call),
                oi=(oif(kk) if with_oi else None),
                oi_change=((oif(kk) * 0.1) if with_oi else None),
                volume=((oif(kk) * 2) if with_vol else None),
                iv=(sigma if with_greeks else None),
                delta=(g["delta"] if g else None), gamma=(g["gamma"] if g else None),
                theta=(g["theta"] if g else None), vega=(g["vega"] if g else None))
        rows.append(StrikeRow(strike=kk, ce=legs["ce"], pe=legs["pe"]))
    cap = {"has_greeks": with_greeks, "has_iv": with_greeks, "has_oi": with_oi,
           "greek_coverage": 1.0 if with_greeks else 0.0}
    return OptionChain(underlying="NIFTY", expiry=expiry, ts=ts, source="unit",
                       spot=spot, rows=rows, capability=cap,
                       quality={"dqs": 90.0, "verdict": "PASS"}).sort().compute_atm()


# --------------------------------------------------------------------------- #
#  bs.py                                                                       #
# --------------------------------------------------------------------------- #
def test_bs_put_call_parity_and_signs():
    S0, K, T, sig = 100.0, 100.0, 0.25, 0.2
    c = bs.price(S0, K, T, sig, True)
    p = bs.price(S0, K, T, sig, False)
    assert c == pytest.approx(p, abs=1e-9)                 # r=0, ATM -> C == P
    assert (c - p) == pytest.approx(S0 - K, abs=1e-6)      # parity
    gc = bs.greeks(S0, K, T, sig, True)
    gp = bs.greeks(S0, K, T, sig, False)
    assert 0 < gc["delta"] < 1 and -1 < gp["delta"] < 0
    assert gc["gamma"] > 0 and gc["gamma"] == pytest.approx(gp["gamma"])
    assert gc["theta"] < 0 and gc["vega"] > 0


def test_bs_implied_vol_roundtrip():
    S0, K, T = 23450.0, 23500.0, 0.05
    for is_call in (True, False):
        px = bs.price(S0, K, T, 0.14, is_call)
        got = bs.implied_vol(px, S0, K, T, is_call)
        assert got == pytest.approx(0.14, abs=1e-3)
    assert bs.implied_vol(-1, S0, K, T, True) is None       # bad price
    assert bs.year_fraction("garbage") is None


# --------------------------------------------------------------------------- #
#  analytics -- PCR                                                            #
# --------------------------------------------------------------------------- #
def test_pcr_matches_hand_sum():
    c = _chain()
    tot_ce = sum(r.ce.oi for r in c.rows)
    tot_pe = sum(r.pe.oi for r in c.rows)
    p = A.pcr(c, atm_window=5)
    assert p.status == "ok"
    assert p.pcr_oi == pytest.approx(round(tot_pe / tot_ce, 4))
    assert p.atm_window == 5 and p.pcr_oi_atm is not None


def test_pcr_no_oi():
    assert A.pcr(_chain(with_oi=False)).status == "no_oi"


# --------------------------------------------------------------------------- #
#  analytics -- true max pain                                                  #
# --------------------------------------------------------------------------- #
def test_max_pain_is_writer_payout_argmin():
    # OI concentrated so the writer-payout minimum sits at 23000, not mid-chain
    c = _chain(spot=23000.0, lo=22600, hi=23400, step=100,
               ce_oi_fn=lambda k: 100.0 if k > 23000 else 10.0,
               pe_oi_fn=lambda k: 100.0 if k < 23000 else 10.0)
    mp = A.max_pain(c)
    assert mp.status == "ok" and mp.max_pain_strike == 23000.0
    # brute check: payout(mp) <= payout(any other K)
    pay = dict(mp.curve)
    assert pay[mp.max_pain_strike] == min(pay.values())


def test_max_pain_no_oi():
    assert A.max_pain(_chain(with_oi=False)).status == "no_oi"


# --------------------------------------------------------------------------- #
#  analytics -- IV skew                                                        #
# --------------------------------------------------------------------------- #
def test_iv_skew_detects_put_bid():
    # puts carry a higher IV than calls at every strike -> positive 25d RR
    c = _chain()
    for r in c.rows:
        r.pe.iv = 0.16
        r.ce.iv = 0.12
    sk = A.iv_skew(c)
    assert sk.status == "ok"
    assert sk.rr_25 is not None and sk.rr_25 > 0            # put_iv - call_iv
    assert sk.atm_iv == pytest.approx(0.14, abs=1e-6)


def test_iv_skew_slope_is_robust_to_wing_outliers():
    c = _chain()
    base = {r.strike: (r.ce.iv, r.pe.iv) for r in c.rows}
    for r in c.rows:                                        # flat skew ...
        r.ce.iv = r.pe.iv = 0.12
    c.rows[0].pe.iv = 5.0                                   # ... + one absurd wing point
    c.rows[-1].ce.iv = 5.0
    sk = A.iv_skew(c)
    assert sk.skew_slope is not None and abs(sk.skew_slope) < 0.5   # median-of-slopes shrugs it off


# --------------------------------------------------------------------------- #
#  analytics -- OI walls + baseline delta                                      #
# --------------------------------------------------------------------------- #
def test_oi_walls_pick_top_k_and_nearest():
    c = _chain(spot=23450.0)
    w = A.oi_walls(c, k=3)
    assert w.status == "ok" and len(w.resistance) == 3 and len(w.support) == 3
    assert w.ce_wall == max(r.strike for r in c.rows if r.ce.oi ==
                            max(x.ce.oi for x in c.rows))
    assert w.nearest_resistance >= 23450.0 >= w.nearest_support


def test_oi_change_vs_baseline():
    base = _chain(ts="2026-09-09T05:00:00Z")
    now = _chain(ts="2026-09-09T06:00:00Z")
    for r in now.rows:
        r.ce.oi += 1000.0
    d = A.oi_change_vs_baseline(now, base)
    assert d["status"] == "ok" and d["net_ce_doi"] == pytest.approx(1000.0 * len(now.rows))
    assert d["rows"][0]["ce_doi"] == pytest.approx(1000.0)


# --------------------------------------------------------------------------- #
#  analytics -- GEX                                                            #
# --------------------------------------------------------------------------- #
def test_gex_sign_and_flip():
    # calls dominate OI everywhere -> shape = gamma*(ce_oi - pe_oi) > 0 -> net long
    c = _chain(ce_oi_fn=lambda k: 20000.0, pe_oi_fn=lambda k: 1000.0)
    g = A.gex(c)
    assert g.status == "ok" and g.regime_sign == 1 and g.total_shape > 0
    assert g.pin_strike is not None
    # symmetric OI -> shape crosses zero -> a flip strike exists near ATM
    c2 = _chain(ce_oi_fn=lambda k: 8000.0 + max(0.0, k - 23450.0),
                pe_oi_fn=lambda k: 8000.0 + max(0.0, 23450.0 - k))
    g2 = A.gex(c2)
    assert g2.status == "ok" and g2.flip_strike is not None
    assert 22500.0 <= g2.flip_strike <= 24400.0


def test_gex_needs_spot_and_greeks_path():
    c = _chain(with_greeks=False)                # no chain IV -> solves off LTP
    g = A.gex(c)
    assert g.status == "ok" and g.sigma_src == "bs_solve_atm"
    c.spot = None
    assert A.gex(c).status == "no_spot"


# --------------------------------------------------------------------------- #
#  structure engine                                                            #
# --------------------------------------------------------------------------- #
def test_structure_full_read_has_all_blocks():
    c = _chain(spot=23440.0)
    st = S.analyze(c, realized_vol=0.10)
    assert st.capability["structure_blocks_ok"] == 5
    assert st.capability["structure_complete"] is True
    assert st.max_pain["magnet_dir"] in ("UP", "DOWN", "AT")
    assert st.pcr_regime["regime"] in ("BULLISH", "BEARISH", "NEUTRAL") or \
        st.pcr_regime["regime"].startswith("MIXED")
    assert st.oi_walls["status"] == "ok"
    assert st.iv_skew_bias["bias"] in ("PUT_FEAR", "CALL_CHASE", "NEUTRAL")
    assert st.iv_vs_realized["state"] in ("IV_RICH", "IV_CHEAP", "FAIR")
    assert st.gex_regime["regime"] in ("NET_LONG_GAMMA", "NET_SHORT_GAMMA", "GAMMA_BALANCED")
    assert st.summary and "insufficient" not in st.summary
    json.dumps(st.to_dict())                     # must be JSON-serialisable


def test_structure_degrades_without_oi():
    c = _chain(with_oi=False)
    st = S.analyze(c)
    for blk in (st.max_pain, st.pcr_regime, st.oi_walls, st.gex_regime):
        assert blk.get("status") not in ("ok",)
        assert "UNKNOWN" in (blk.get("magnet_dir", ""), blk.get("regime", ""), blk.get("status", "")) \
            or blk["status"] in ("no_oi", "thin")
    assert st.capability["structure_complete"] is False
    # skew still works off chain IV
    assert st.iv_skew_bias["status"] == "ok"


def test_structure_degrades_without_greeks_or_iv():
    c = _chain(with_greeks=False)
    # strip LTP too -> skew cannot solve
    for r in c.rows:
        r.ce.ltp = r.pe.ltp = None
    st = S.analyze(c)
    assert st.iv_skew_bias["bias"] == "UNKNOWN"
    assert st.gex_regime["regime"] == "UNKNOWN"
    assert st.iv_vs_realized["state"] == "UNKNOWN"
    # OI blocks still fine
    assert st.max_pain["status"] == "ok" and st.pcr_regime["status"] == "ok"


def test_structure_config_override_moves_thresholds():
    c = _chain(spot=23450.0)
    # force max-pain "AT" by widening the band to 100%
    st = S.analyze(c, cfg={"mp_at_pct": 100.0})
    assert st.max_pain["magnet_dir"] == "AT" and st.max_pain["magnet_pull"] == "AT_MAGNET"


def test_structure_max_pain_direction_semantics():
    # max pain well above spot -> price must rally to reach it -> "UP"
    c = _chain(spot=23000.0, lo=22600, hi=23600, step=100,
               ce_oi_fn=lambda k: 10.0, pe_oi_fn=lambda k: 500.0 if k >= 23400 else 10.0)
    st = S.analyze(c)
    if st.max_pain["status"] == "ok" and st.max_pain["magnet_dir"] != "AT":
        assert (st.max_pain["max_pain_strike"] > c.spot) == (st.max_pain["magnet_dir"] == "UP")
