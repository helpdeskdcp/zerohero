/* Run: node tests/arena_terminal_ind.test.js — the two Pine scripts on the underlying chart switch on / off like indicators, and the choice is remembered. */
const assert = require("assert");
const T = require("../static/js/arena_terminal.js");
assert.deepStrictEqual(T.TM_INDICATORS.map((x) => x.key), ["winners", "uturn"]);
assert.deepStrictEqual(T.tmIndLoad(null), { winners: true, uturn: true });                                    // nothing stored: both on
assert.deepStrictEqual(T.tmIndLoad('{"winners":false}'), { winners: false, uturn: true });                    // stored: kept
assert.deepStrictEqual(T.tmIndLoad('{"winners":"no","uturn":0,"x":1}'), { winners: true, uturn: true });      // wrong types ignored
assert.deepStrictEqual(T.tmIndLoad("{not json"), { winners: true, uturn: true });                              // unreadable: all on
const times = [100, 200, 300, 400];
const u = { markers: [{ time: 210, side: "BUY", strong: true }, { time: 390, side: "SELL", strong: false }], uturn: [{ time: 305, side: "CE", thrust_pct: 0.62, base_rate: { n: 20, target_hit: 0.65 } }, { time: 150, side: "PE", base_rate: null }],
  uturn_calibration: { overall: { n: 67, target_hit: 0.6 } } };
const order = { time: 300, position: "belowBar", shape: "circle", text: "ORDER" };
const both = T.tmBuildMarkers(times, u, { winners: true, uturn: true }, order);
assert.deepStrictEqual(both.map((m) => m.text), ["U-turn PE", "BUY", "sell", "U-turn CE · 65% (n=20)", "ORDER"]);   // all kinds present, in time order (those snapped to the same candle keep winners, U-turn, order)
assert.strictEqual(both.length, 5); assert.ok(both.every((m, i) => i === 0 || both[i - 1].time <= m.time));
const w = T.tmBuildMarkers(times, u, { winners: true, uturn: false }, null); assert.deepStrictEqual(w.map((m) => m.text), ["BUY", "sell"]);
const ut = T.tmBuildMarkers(times, u, { winners: false, uturn: true }, null); assert.deepStrictEqual(ut.map((m) => m.text), ["U-turn PE", "U-turn CE · 65% (n=20)"]);
assert.strictEqual(ut[1].shape, "arrowUp"); assert.strictEqual(ut[0].shape, "arrowDown");                      // CE idea = up arrow below the bar, PE idea = down arrow above it
assert.deepStrictEqual(T.tmBuildMarkers(times, u, { winners: false, uturn: false }, order).map((m) => m.text), ["ORDER"]);   // both off: only the order marker stays
assert.deepStrictEqual(T.tmBuildMarkers(times, null, { winners: true, uturn: true }, order).map((m) => m.text), ["ORDER"]);  // no underlying data (equity): same
assert.ok(T.tmIndNote({ winners: true, uturn: true }, u).includes("Winners Scalper + Safe U-turn") && T.tmIndNote({ winners: true, uturn: true }, u).includes("60% of 67") && T.tmIndNote({ winners: true, uturn: true }, u).includes("no proven edge"));
assert.ok(!T.tmIndNote({ winners: true, uturn: false }, u).includes("U-turn history")); assert.ok(T.tmIndNote({ winners: false, uturn: false }, u).startsWith("Indicators: none"));
console.log("arena_terminal_ind ok");
