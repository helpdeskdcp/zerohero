"""Conformance tests for the Pine runtime wrapper. Reference implementations below are written from the Pine Script spec
(independent of PineTS); every capability the registry calls "supported" must be exercised here."""
import math

import pytest

from app.smart_chart import capabilities, runner, service, symbols, templates
from tests import smart_chart_fakes as fk

pytestmark = pytest.mark.skipif(not runner.available()["ok"], reason="node/pinets not installed")


@pytest.fixture(autouse=True)
def _reset_rate():
    runner._rate.clear()
    yield


BARS = fk.candles_for_pine(400, 5)
CL = [b["close"] for b in BARS]
HI = [b["high"] for b in BARS]
LO = [b["low"] for b in BARS]


def run(src, bars=BARS, tf="5", inputs=None, props=None):
    r = runner.execute({"op": "run", "source": src, "candles": bars, "tf": tf, "symbol": "NIFTY", "inputs": inputs or {}, "props": props or {}}, "t")
    assert r["ok"], r.get("diagnostics")
    return r


def plots(src, **kw):
    return {p["title"]: p for p in run(src, **kw)["plots"]}


def close_to(a, b, tol=1e-6):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= tol * max(1.0, abs(b)))


def assert_series(got, ref, tol=1e-6, skip=0):
    assert len(got) == len(ref)
    for i, (g, r) in enumerate(zip(got, ref)):
        if i < skip:
            continue
        assert close_to(g, r, tol), f"bar {i}: pine={g} ref={r}"


# ---- independent references (Pine spec) ------------------------------------------------------
def ref_sma(x, n):
    return [None if i < n - 1 else sum(x[i - n + 1:i + 1]) / n for i in range(len(x))]


def ref_ema(x, n):
    out, a, prev = [None] * len(x), 2 / (n + 1), None
    for i in range(len(x)):
        if i == n - 1:
            prev = sum(x[:n]) / n
            out[i] = prev
        elif i >= n:
            prev = a * x[i] + (1 - a) * prev
            out[i] = prev
    return out


def ref_rma(x, n, start=0):
    out, prev = [None] * len(x), None
    for i in range(start, len(x)):
        if i == start + n - 1:
            prev = sum(x[start:start + n]) / n
            out[i] = prev
        elif i > start + n - 1:
            prev = (prev * (n - 1) + x[i]) / n
            out[i] = prev
    return out


def ref_rsi(x, n):
    up = [0.0] + [max(x[i] - x[i - 1], 0) for i in range(1, len(x))]
    dn = [0.0] + [max(x[i - 1] - x[i], 0) for i in range(1, len(x))]
    ru, rd = ref_rma(up, n, 1), ref_rma(dn, n, 1)
    return [None if ru[i] is None else (100.0 if rd[i] == 0 else 100 - 100 / (1 + ru[i] / rd[i])) for i in range(len(x))]


def ref_tr():
    return [HI[0] - LO[0]] + [max(HI[i] - LO[i], abs(HI[i] - CL[i - 1]), abs(LO[i] - CL[i - 1])) for i in range(1, len(CL))]


# ---- verified functions ---------------------------------------------------------------------
CASES = {}


def case(*names):
    def deco(fn):
        for n in names:
            CASES[n] = fn
        return fn
    return deco


@case("ta.sma", "plot")
def t_sma():
    p = plots('//@version=5\nindicator("t")\nplot(ta.sma(close, 20), "v")')["v"]
    assert_series(p["values"], ref_sma(CL, 20))
    assert sum(v is None for v in p["values"]) == 19                      # warm-up bars are null, not 0


@case("ta.ema")
def t_ema():
    assert_series(plots('//@version=5\nindicator("t")\nplot(ta.ema(close, 21), "v")')["v"]["values"], ref_ema(CL, 21))


@case("ta.rma")
def t_rma():
    assert_series(plots('//@version=5\nindicator("t")\nplot(ta.rma(close, 14), "v")')["v"]["values"], ref_rma(CL, 14), 1e-6)


@case("ta.rsi")
def t_rsi():
    assert_series(plots('//@version=5\nindicator("t")\nplot(ta.rsi(close, 14), "v")')["v"]["values"], ref_rsi(CL, 14), 1e-6)


@case("ta.atr")
def t_atr():
    got = plots('//@version=5\nindicator("t")\nplot(ta.atr(14), "v")')["v"]["values"]
    assert_series(got, ref_rma(ref_tr(), 14), 1e-6)


@case("ta.highest", "ta.lowest")
def t_hl():
    p = plots('//@version=5\nindicator("t")\nplot(ta.highest(high, 10), "h")\nplot(ta.lowest(low, 10), "l")')
    assert_series(p["h"]["values"], [None if i < 9 else max(HI[i - 9:i + 1]) for i in range(len(HI))])
    assert_series(p["l"]["values"], [None if i < 9 else min(LO[i - 9:i + 1]) for i in range(len(LO))])


@case("ta.change", "math.abs", "math.max")
def t_change():
    p = plots('//@version=5\nindicator("t")\nplot(ta.change(close), "c")\nplot(math.abs(ta.change(close)), "a")\nplot(math.max(open, close), "m")')
    ref = [None] + [CL[i] - CL[i - 1] for i in range(1, len(CL))]
    assert_series(p["c"]["values"], ref)
    assert_series(p["a"]["values"], [None if v is None else abs(v) for v in ref])
    assert_series(p["m"]["values"], [max(b["open"], b["close"]) for b in BARS])


@case("ta.crossover", "ta.crossunder", "plotshape")
def t_cross():
    p = plots('//@version=5\nindicator("t")\nf = ta.sma(close, 5)\ns = ta.sma(close, 20)\nplotshape(ta.crossover(f, s), "up", shape.triangleup)\nplotshape(ta.crossunder(f, s), "dn", shape.triangledown)')
    f, s = ref_sma(CL, 5), ref_sma(CL, 20)
    up = [i for i in range(1, len(CL)) if None not in (f[i], s[i], f[i - 1], s[i - 1]) and f[i] > s[i] and f[i - 1] <= s[i - 1]]
    dn = [i for i in range(1, len(CL)) if None not in (f[i], s[i], f[i - 1], s[i - 1]) and f[i] < s[i] and f[i - 1] >= s[i - 1]]
    got_up = [i for i, v in enumerate(p["up"]["values"]) if v is True]
    got_dn = [i for i, v in enumerate(p["dn"]["values"]) if v is True]
    assert got_up == up and got_dn == dn and len(up) > 3 and len(dn) > 3         # markers only on true crossings


@case("hline")
def t_hline():
    p = plots('//@version=5\nindicator("t")\nhline(70, "OB")')["OB"]
    assert p["style"] == "hline" and all(v == 70 for v in p["values"] if v is not None)


@case("input.int")
def t_input():
    src = '//@version=5\nindicator("t")\nlen = input.int(10, "Length", minval=2)\nplot(ta.sma(close, len), "v")'
    d = plots(src)["v"]["values"]
    assert_series(d, ref_sma(CL, 10))
    assert_series(plots(src, inputs={"len": 30})["v"]["values"], ref_sma(CL, 30))        # override by variable id
    r = run(src)
    assert r["inputs"][0]["key"] == "len" and r["inputs"][0]["defval"] == 10 and r["inputs"][0]["minval"] == 2


@case("indicator")
def t_decl_ind():
    assert run('//@version=5\nindicator("My Ind", overlay=true)\nplot(close)')["kind"] == "indicator"


@case("strategy", "strategy.entry", "strategy.close")
def t_strategy_fill_next_open():
    src = ('//@version=5\nstrategy("t", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)\n'
           'if ta.crossover(close, ta.sma(close, 20))\n    strategy.entry("L", strategy.long)\nif ta.crossunder(close, ta.sma(close, 20))\n    strategy.close("L")')
    st = run(src)["strategy"]
    sma = ref_sma(CL, 20)
    sigs_in = [i for i in range(1, len(CL)) if sma[i] is not None and sma[i - 1] is not None and CL[i] > sma[i] and CL[i - 1] <= sma[i - 1]]
    assert st["closed_trades"] or st["open_trades"]
    trades = sorted(st["closed_trades"] + st["open_trades"], key=lambda t: t["entry_bar"])
    for t in trades:
        sig = t["entry_bar"] - 1
        assert sig in sigs_in, "entry must come the bar AFTER a signal bar"
        # fill = next bar's open, rounded to the 0.05 tick (PineTS rounds fills to syminfo.mintick)
        assert abs(t["entry_price"] - BARS[t["entry_bar"]]["open"]) <= 0.025 + 1e-9, "fill at the next bar's open"
        assert abs(t["entry_price"] / 0.05 - round(t["entry_price"] / 0.05)) < 1e-6
        assert t["entry_time"] == BARS[t["entry_bar"]]["time"]
    for t in st["closed_trades"]:
        assert abs(t["exit_price"] - BARS[t["exit_bar"]]["open"]) <= 0.025 + 1e-9 and t["exit_bar"] > t["entry_bar"]


@case("strategy.exit")
def t_strategy_exit_bracket():
    src = ('//@version=5\nstrategy("t", initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=1)\n'
           'a = ta.atr(14)\nif ta.crossover(close, ta.sma(close, 20))\n    strategy.entry("L", strategy.long)\n    strategy.exit("X", "L", stop=close - 2 * a, limit=close + 3 * a)')
    st = run(src)["strategy"]
    assert st["closed_trades"]
    for t in st["closed_trades"]:
        b = BARS[t["exit_bar"]]
        assert b["low"] - 0.026 <= t["exit_price"] <= b["high"] + 0.026                  # a bracket fill lies inside the bar's range (+ tick rounding)


def test_verified_registry_is_backed_by_tests():
    missing = sorted(set(capabilities.VERIFIED) - set(CASES))
    assert not missing, f"VERIFIED claims without a conformance test: {missing}"
    for name, fn in CASES.items():
        fn()


# ---- series semantics -----------------------------------------------------------------------
def test_historical_indexing_and_persistent_var_state():
    p = plots('//@version=5\nindicator("t")\nvar int n = 0\nn += 1\nplot(n, "n")\nplot(close[1], "prev")\nplot(close[3] - close[2], "d")')
    assert [v for v in p["n"]["values"]] == list(range(1, len(BARS) + 1))                      # var persists across bars
    assert p["prev"]["values"][0] is None and p["prev"]["values"][1:] == CL[:-1]               # [1] is the previous bar; na on bar 0
    assert all(v is None for v in p["d"]["values"][:3])


def test_missing_values_na_propagate_as_null():
    p = plots('//@version=5\nindicator("t")\nplot(na, "n")\nplot(close > open ? close : na, "c")')
    assert all(v is None for v in p["n"]["values"])
    assert [v is None for v in p["c"]["values"]] == [not (b["close"] > b["open"]) for b in BARS]


def test_request_security_same_symbol_higher_timeframe_and_rejections():
    r = plots('//@version=5\nindicator("t")\nplot(request.security(syminfo.tickerid, "60", close), "h")')["h"]["values"]
    assert sum(v is not None for v in r) > len(BARS) * 0.9
    for bad in ('request.security("NSE:RELIANCE", "D", close)', 'request.security(syminfo.tickerid, "1", close)'):
        res = runner.execute({"op": "run", "source": f'//@version=5\nindicator("t")\nplot({bad})', "candles": BARS, "tf": "5", "symbol": "NIFTY"}, "t")
        assert res["ok"] is False and res["diagnostics"][0]["code"] in ("UNSUPPORTED_SYMBOL", "UNSUPPORTED_TIMEFRAME")


# ---- diagnostics ----------------------------------------------------------------------------
@pytest.mark.parametrize("src,code,line", [
    ('//@version=5\nindicator("x")\nplot(ta.sma(close,)', "SYNTAX_ERROR", 3),
    ('//@version=5\nindicator("x")\nplot(foo(close))', "UNDECLARED_IDENTIFIER", 3),
    ('//@version=5\nindicator("x")\nplot(ta.nonexistent(close, 3))', "UNSUPPORTED_FUNCTION", 3),
])
def test_diagnostics_have_codes_and_line_numbers(src, code, line):
    c = service.compile_script("t", src)
    errs = [d for d in c["diagnostics"] if d["severity"] == "error"]
    assert not c["ok"] and errs[0]["code"] == code and errs[0]["line"] == line and errs[0]["column"]


def test_pine_v4_and_empty_and_oversize_rejected():
    c = service.compile_script("t", '//@version=4\nstudy("x")\nplot(sma(close, 3))')
    assert not c["ok"] and c["diagnostics"][0]["code"] == "UNSUPPORTED_VERSION"
    with pytest.raises(service.ServiceError) as e:
        service.compile_script("t", "   ")
    assert e.value.code == "EMPTY_SCRIPT"
    with pytest.raises(service.ServiceError) as e:
        service.compile_script("t", "//@version=5\n" + "x" * 70_000)
    assert e.value.status == 413


def test_infinite_loop_is_stopped_and_timeout_kills_worker():
    r = runner.execute({"op": "run", "source": '//@version=5\nindicator("x")\nx=0.0\nfor i=0 to 2000000000\n    x += 1\nplot(x)', "candles": BARS, "tf": "5", "symbol": "N"}, "t")
    assert r["ok"] is False and "Loop exceeded" in r["diagnostics"][0]["message"]
    big = [dict(b, time=b["time"] + i * 10_000_000_000) for i in range(30) for b in BARS]
    with pytest.raises(runner.RunnerError) as e:
        runner.execute({"op": "run", "source": '//@version=5\nindicator("x")\nx=0.0\nfor i=0 to 900\n    for j=0 to 900\n        x += math.sqrt(i*j)\nplot(x)', "candles": big, "tf": "5", "symbol": "N"}, "t", timeout=2)
    assert e.value.kind == "timeout"


def test_not_listed_functions_warn_statically():
    src = '//@version=5\nindicator("x")\nplot(request.currency_rate("USD","INR"))'
    c = service.compile_script("t", src)
    assert any(d["code"] == "NOT_LISTED_AS_SUPPORTED" for d in c["diagnostics"])


@pytest.mark.parametrize("t", templates.TEMPLATES, ids=lambda t: t["id"])
def test_every_starter_template_compiles_and_runs(t):
    c = service.compile_script("t", t["source"])
    assert c["ok"] and c["kind"] == t["kind"], c["diagnostics"]
    r = run(t["source"])
    assert r["kind"] == t["kind"] and (r["plots"] or r.get("strategy"))
    assert not [d for d in c["diagnostics"] if d["severity"] == "warning"], c["diagnostics"]


def test_large_results_are_not_truncated_by_the_pipe():
    """Regression: process.exit() right after a >64 KiB stdout write truncated the JSON at the pipe buffer size."""
    many = '\n'.join(f'plot(ta.sma(close, {n}), "p{n}")' for n in range(2, 14))
    bars = fk.candles_for_pine(2000, 1)
    r = run(f'//@version=5\nindicator("big")\n{many}', bars=bars, tf="1")
    assert len(r["plots"]) == 12 and len(r["plots"][0]["values"]) == 2000
    assert len(__import__("json").dumps(r)) > 200_000


STRAT = ('//@version=5\nstrategy("t", overlay=true, initial_capital=100000, default_qty_type=strategy.fixed, default_qty_value=10)\n'
         'if bar_index == 5\n    strategy.entry("L", strategy.long)\nif bar_index == 20\n    strategy.close("L")\n')


def n_trades(src, props=None):
    st = run(src, props=props)["strategy"]
    return len(st["closed_trades"]) + len(st["open_trades"]), st["config"]


def test_margin_defaults_to_100_percent_so_server_matches_chart_and_tradingview():
    """Regression: PineTS defaults //@version=5 strategies to no margin model, so an order bigger than equity FILLED on the server
    but was rejected by the in-chart engine. The worker now defaults to 100% unless the script/host says otherwise."""
    n, cfg = n_trades(STRAT)                              # 10 units x ~20,000 > 100,000 capital
    assert n == 0 and cfg["margin_long"] == 100 and cfg["margin_short"] == 100
    n, cfg = n_trades(STRAT.replace("initial_capital=100000", "initial_capital=100000, margin_long=0, margin_short=0"))
    assert n == 1 and cfg["margin_long"] == 0             # a script that declares margin keeps it
    n, _ = n_trades(STRAT, props={"margin_long": 0, "margin_short": 0})
    assert n == 1                                         # explicit host override respected
    n, _ = n_trades(STRAT.replace("strategy.fixed", "strategy.percent_of_equity").replace("default_qty_value=10", "default_qty_value=90"))
    assert n == 1                                         # affordable size fills
    n, _ = n_trades(STRAT.replace("version=5", "version=6"))
    assert n == 0                                         # v6 behaves the same


def test_indicators_are_unaffected_by_the_margin_default():
    assert run('//@version=5\nindicator("x")\nplot(close)')["kind"] == "indicator"
