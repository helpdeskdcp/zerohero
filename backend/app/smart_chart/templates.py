"""Starter Pine scripts shown in the editor. Every template is compiled and executed by tests/test_smart_chart_pine.py."""
from __future__ import annotations

TEMPLATES = [
    {"id": "ema-ribbon", "name": "EMA Ribbon", "kind": "indicator", "description": "Three EMAs with a trend-colour fill-free ribbon.",
     "source": """//@version=5
indicator("EMA Ribbon", overlay=true)
fastLen = input.int(9, "Fast", minval=1)
midLen  = input.int(21, "Mid", minval=1)
slowLen = input.int(50, "Slow", minval=1)
fast = ta.ema(close, fastLen)
mid  = ta.ema(close, midLen)
slow = ta.ema(close, slowLen)
plot(fast, "Fast", color=color.new(color.aqua, 0), linewidth=1)
plot(mid,  "Mid",  color=color.new(color.orange, 0), linewidth=2)
plot(slow, "Slow", color=color.new(color.red, 0), linewidth=2)
bgcolor(fast > mid and mid > slow ? color.new(color.green, 90) : fast < mid and mid < slow ? color.new(color.red, 90) : na)
"""},
    {"id": "rsi-signals", "name": "RSI with overbought/oversold markers", "kind": "indicator",
     "description": "RSI in its own pane; markers only where the RSI crosses back out of the bands (confirmed on bar close).",
     "source": """//@version=5
indicator("RSI Signals", overlay=false)
len = input.int(14, "Length", minval=2)
ob  = input.int(70, "Overbought")
os_ = input.int(30, "Oversold")
r = ta.rsi(close, len)
plot(r, "RSI", color=color.new(color.purple, 0), linewidth=2)
hline(ob, "OB")
hline(os_, "OS")
buyCond  = ta.crossover(r, os_)
sellCond = ta.crossunder(r, ob)
plotshape(buyCond,  "Buy",  shape.triangleup,   location.bottom, color.green, text="BUY")
plotshape(sellCond, "Sell", shape.triangledown, location.top,    color.red,   text="SELL")
"""},
    {"id": "orb-levels", "name": "Opening range + prior-day levels", "kind": "indicator",
     "description": "Overlay of the day's high/low so far and the previous bar's close, using persistent var state.",
     "source": """//@version=5
indicator("Day Range", overlay=true)
var float dayHigh = na
var float dayLow  = na
newDay = ta.change(time("D")) != 0
dayHigh := newDay ? high : math.max(nz(dayHigh, high), high)
dayLow  := newDay ? low  : math.min(nz(dayLow, low), low)
plot(dayHigh, "Day high", color=color.new(color.green, 20), linewidth=1)
plot(dayLow,  "Day low",  color=color.new(color.red, 20), linewidth=1)
"""},
    {"id": "ema-cross-strategy", "name": "EMA cross strategy", "kind": "strategy",
     "description": "Long when the fast EMA crosses above the slow EMA, flat on the opposite cross. Fills at next bar open.",
     "source": """//@version=5
strategy("EMA Cross", overlay=true, initial_capital=100000, default_qty_type=strategy.percent_of_equity, default_qty_value=90, commission_type=strategy.commission.percent, commission_value=0.03)
fastLen = input.int(9, "Fast", minval=1)
slowLen = input.int(21, "Slow", minval=2)
fast = ta.ema(close, fastLen)
slow = ta.ema(close, slowLen)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, comment="Buy")
if ta.crossunder(fast, slow)
    strategy.close("Long", comment="Exit")
plot(fast, "Fast", color=color.aqua)
plot(slow, "Slow", color=color.orange)
"""},
    {"id": "rsi-atr-strategy", "name": "RSI bounce with ATR stop", "kind": "strategy",
     "description": "Long on RSI crossing up through 30 with an ATR-based stop and 2R target (bracket evaluated on bar OHLC).",
     "source": """//@version=5
strategy("RSI ATR", overlay=true, initial_capital=100000, default_qty_type=strategy.percent_of_equity, default_qty_value=90, commission_type=strategy.commission.percent, commission_value=0.03, slippage=2)
len = input.int(14, "RSI length")
atrMult = input.float(1.5, "ATR stop x", step=0.1)
r = ta.rsi(close, len)
a = ta.atr(14)
if ta.crossover(r, 30)
    strategy.entry("Long", strategy.long, comment="Buy")
    strategy.exit("Exit", "Long", stop=close - atrMult * a, limit=close + 2 * atrMult * a, comment="Stop/Target")
plot(a, "ATR", display=display.none)
"""},
]


def _load_pine_file(name: str) -> str:
    import os
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pine_scripts", name), encoding="utf-8") as f: return f.read()


TEMPLATES.append({"id": "zerohero-smc-v134", "name": "ZeroHero AI SMC Intelligence Engine v1.3.4", "kind": "indicator",
                  "description": "23-module SMC engine: BUY CE / BUY PE markers with Entry / SL / T1-T4 lines, exit markers, tiny compact dashboard. Works on index charts AND on option CE / PE premium charts (Auto from the ticker). Signals on confirmed 5-minute bars; costs not modelled.",
                  "source": _load_pine_file("zerohero_smc_v1_3_4.pine")})


TEMPLATES.append({"id": "zhx-v1", "name": "ZeroHero ZHX v1 | Liquidity + Stop-Hunt Engine", "kind": "indicator",
                  "description": "ONE script for index charts and option CE / PE premium charts: liquidity pools (PDH/PDL, equal highs/lows, opening range), stop-hunt sweep + displacement, BUY CE / BUY PE with SL and T1-T3 at the next pools, exit markers, tiny dashboard. Price-only: OI / IV / bid-ask are added by Safe AI. Validation report in data/research/zhx/ (no net edge demonstrated).",
                  "source": _load_pine_file("zhx_v1.pine")})
