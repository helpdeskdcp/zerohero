"""Read-only audit: run every duplicated ATR / EMA implementation on the SAME synthetic OHLC series and report which ones agree (final value + max difference over the tail). Evidence for choosing a canonical indicator module; nothing is wired to it."""
import importlib, json, sys, warnings
import numpy as np
warnings.filterwarnings("ignore"); sys.path.insert(0, ".")
rng = np.random.default_rng(7); n = 400
c = 100 + np.cumsum(rng.normal(0, 0.6, n)); o = c + rng.normal(0, 0.2, n); h = np.maximum(o, c) + rng.random(n) * 0.5; l = np.minimum(o, c) - rng.random(n) * 0.5
candles = [dict(open=float(o[i]), high=float(h[i]), low=float(l[i]), close=float(c[i]), volume=1000.0, t=i, time=i, o=float(o[i]), h=float(h[i]), l=float(l[i]), c=float(c[i]), v=1000.0) for i in range(n)]

def wilder(h, l, c, p):                                   # reference: TradingView ta.atr (RMA of true range, seeded with the SMA of the first p TRs)
    tr = np.r_[h[0] - l[0], np.maximum.reduce([h[1:] - l[1:], abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])])]
    out = np.full(len(tr), np.nan); out[p - 1] = tr[:p].mean()
    for i in range(p, len(tr)): out[i] = (out[i - 1] * (p - 1) + tr[i]) / p
    return out

def ema_ref(x, p):                                        # alpha = 2/(p+1), seeded with the first value (pandas adjust=False)
    out = np.empty(len(x)); out[0] = x[0]; a = 2 / (p + 1)
    for i in range(1, len(x)): out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out

def last(v): 
    v = np.asarray(v, float) if not isinstance(v, (int, float)) and v is not None else v
    return float(v[-1]) if hasattr(v, "__len__") else (None if v is None else float(v))

cases = []
def add(name, fn): cases.append((name, fn))
def imp(mod, attr): return getattr(importlib.import_module(mod), attr)
P = 14
ref_atr = wilder(h, l, c, P)[-1]; ref_ema = ema_ref(c, P)[-1]
add("atr smart_uturn.indicators.atr", lambda: last(imp("app.smart_uturn.indicators", "atr")(h, l, c, P)))
add("atr research_engines.orderflow.indicators.atr", lambda: last(imp("app.research_engines.orderflow.indicators", "atr")(h, l, c, P)))
add("atr candle_ai.patterns.atr", lambda: last(imp("app.candle_ai.patterns", "atr")(h, l, c, P)))
add("atr winners_scalper.strategy.atr_series", lambda: last(imp("app.winners_scalper.strategy", "atr_series")(h, l, c, P)))
add("atr zone_breakout.trap_features._atr", lambda: last(imp("app.zone_breakout.trap_features", "_atr")(h, l, c, P)))
add("atr scanner.dsl._atr", lambda: last(imp("app.scanner.dsl", "_atr")(h, l, c, P)))
add("atr research_engines.smart_bos.engine._atr", lambda: last(imp("app.research_engines.smart_bos.engine", "_atr")(h, l, c, P)))
add("atr smart_chart.analysis.atr", lambda: last(imp("app.smart_chart.analysis", "atr")(candles, P)))
add("atr nifty_direction.indicators.atr", lambda: last(imp("app.nifty_direction.indicators", "atr")(candles, P)))
add("atr reverse_engineering.regime.atr", lambda: last(imp("app.reverse_engineering.regime", "atr")(candles, P)))
add("atr research_engines.order_pressure.formulas.atr", lambda: last(imp("app.research_engines.order_pressure.formulas", "atr")(candles, P)))
add("ema smart_uturn.indicators.ema", lambda: last(imp("app.smart_uturn.indicators", "ema")(c, P)))
add("ema research_engines.orderflow.indicators.ema", lambda: last(imp("app.research_engines.orderflow.indicators", "ema")(list(c), P)))
add("ema candle_ai.features.ema", lambda: last(imp("app.candle_ai.features", "ema")(c, P)))
add("ema smart_chart.analysis.ema", lambda: last(imp("app.smart_chart.analysis", "ema")(list(c), P)))
add("ema nifty_direction.indicators.ema", lambda: last(imp("app.nifty_direction.indicators", "ema")(list(c), P)))
add("ema reverse_engineering.regime.ema", lambda: last(imp("app.reverse_engineering.regime", "ema")(list(c), P)))
add("ema liquidity_sweep.structure._ema", lambda: last(imp("app.liquidity_sweep.structure", "_ema")(list(c), P)))
add("ema zone_breakout.trap_features._ema", lambda: last(imp("app.zone_breakout.trap_features", "_ema")(c, P)))
add("ema engines.signal_engine._ema_series", lambda: last(imp("app.engines.signal_engine", "_ema_series")(c, P)))
rows = []
for name, fn in cases:
    ref = ref_atr if name.startswith("atr") else ref_ema
    try: v = fn(); rows.append({"impl": name, "value": None if v is None else round(v, 6), "ref": round(float(ref), 6), "match": bool(v is not None and abs(v - ref) < 1e-6), "rel_diff_pct": None if v is None else round(abs(v - ref) / ref * 100, 4)})
    except Exception as e: rows.append({"impl": name, "error": f"{type(e).__name__}: {str(e)[:70]}"})
print(json.dumps(rows, indent=0))
