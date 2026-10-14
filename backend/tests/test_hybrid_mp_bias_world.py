"""Mechanics check against an independent tick-level evaluator (scripts/hybrid_mp_bias_audit.py): when the bars are built from EVERY step of
the true path (stride 1), each engine trade's R must equal the R obtained by walking the same trade along the true path. This validates
entry timing, stop/target touch logic, gap/EOD handling and the one-position bookkeeping with no OHLC discretisation involved."""
import importlib.util
import os

import numpy as np
import pytest

from app.research_engines.hybrid_mp.engine import Params, run

_spec = importlib.util.spec_from_file_location("bias_audit", os.path.join(os.path.dirname(__file__), "..", "scripts", "hybrid_mp_bias_audit.py"))
BA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BA)


@pytest.mark.parametrize("real,kw", [
    (False, dict(fill="close", gap_fills=False, eod_flat=False, exit_model="PINE")),
    (True, dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="PINE")),
])
def test_engine_r_equals_true_path_r_when_bars_see_every_step(real, kw):
    P, t = BA.make_world(4242, 3)
    chart = BA.observed_bars(P, t, 1)                      # stride 1 => bar extremes ARE the true path extremes
    tr, _ = run(chart, Params(pivot_len=5, **kw))
    flat = P.reshape(-1)
    usable = []
    for x in tr:
        d, m = divmod(x.sig_i, BA.BARS)
        if real and m == BA.BARS - 1:
            continue
        if real:
            entry = flat[d * BA.ROW + (m + 1) * BA.STEPS]
            sl = chart["c"][x.sig_i] - x.side * x.risk
            if (x.side == 1 and entry <= sl) or (x.side == -1 and entry >= sl):
                continue
        usable.append(x)
    rt = BA.eval_true(flat, usable, chart, "PINE", real)
    ok = ~np.isnan(rt)
    assert ok.sum() > 15
    assert np.allclose(np.array([x.r for x in usable])[ok], rt[ok], atol=1e-6), "engine and independent path walker disagree"
