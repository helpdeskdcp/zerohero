"""Drift guard: every duplicated ATR / EMA copy must keep matching the canonical app.indicators on the same series."""
import importlib

import numpy as np
import pytest

from app import indicators as I

rng = np.random.default_rng(7); N = 400
C = 100 + np.cumsum(rng.normal(0, 0.6, N)); O = C + rng.normal(0, 0.2, N)
Hh = np.maximum(O, C) + rng.random(N) * 0.5; Ll = np.minimum(O, C) - rng.random(N) * 0.5
CANDLES = [dict(open=float(O[i]), high=float(Hh[i]), low=float(Ll[i]), close=float(C[i]), volume=1000.0, t=i, time=i, o=float(O[i]), h=float(Hh[i]), l=float(Ll[i]), c=float(C[i]), v=1000.0) for i in range(N)]
P = 14


def _last(v): return float(np.asarray(v, float)[-1]) if hasattr(v, "__len__") else float(v)


def f(mod, attr): return getattr(importlib.import_module(mod), attr)


ATR_ARRAY = [("app.smart_uturn.indicators", "atr"), ("app.research_engines.orderflow.indicators", "atr"), ("app.candle_ai.patterns", "atr"), ("app.winners_scalper.strategy", "atr_series"),
             ("app.zone_breakout.trap_features", "_atr"), ("app.research_engines.smart_bos.engine", "_atr")]
ATR_CANDLES = [("app.smart_chart.analysis", "atr"), ("app.nifty_direction.indicators", "atr")]
EMA_ARRAY = [("app.smart_uturn.indicators", "ema"), ("app.candle_ai.features", "ema"), ("app.zone_breakout.trap_features", "_ema"), ("app.engines.signal_engine", "_ema_series")]
EMA_LIST = [("app.research_engines.orderflow.indicators", "ema"), ("app.smart_chart.analysis", "ema"), ("app.nifty_direction.indicators", "ema"), ("app.reverse_engineering.regime", "ema"),
            ("app.liquidity_sweep.structure", "_ema")]


@pytest.mark.parametrize("mod,attr", ATR_ARRAY)
def test_atr_array_copies_match_the_canonical(mod, attr): assert _last(f(mod, attr)(Hh, Ll, C, P)) == pytest.approx(I.atr(Hh, Ll, C, P)[-1], abs=1e-9)


@pytest.mark.parametrize("mod,attr", ATR_CANDLES)
def test_atr_candle_copies_match_the_canonical(mod, attr): assert _last(f(mod, attr)(CANDLES, P)) == pytest.approx(I.atr(Hh, Ll, C, P)[-1], abs=1e-9)


@pytest.mark.parametrize("mod,attr", EMA_ARRAY)
def test_ema_array_copies_match_the_canonical(mod, attr): assert _last(f(mod, attr)(C, P)) == pytest.approx(I.ema(C, P)[-1], abs=1e-9)


@pytest.mark.parametrize("mod,attr", EMA_LIST)
def test_ema_list_copies_match_the_canonical(mod, attr): assert _last(f(mod, attr)(list(C), P)) == pytest.approx(I.ema(C, P)[-1], abs=1e-9)


def test_short_series_and_empty_input_do_not_crash():
    assert np.isnan(I.atr([1, 2], [0, 1], [1, 1], 14)).all() and len(I.ema([], 5)) == 0
