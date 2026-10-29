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

/* the state line + session levels (an indicator with no signal must still say where it stands) */
const u2 = { ...u, uturn_state: { status: "Watching: the biggest move today is a down-thrust of 0.31% (needs 0.60% before a CALL idea can form)", is_today: true, day: "2026-10-29", session_high: 74152, session_low: 73249 } };
assert.strictEqual(T.tmUturnStatus(u2), "Safe U-turn: Watching: the biggest move today is a down-thrust of 0.31% (needs 0.60% before a CALL idea can form)");
assert.ok(T.tmUturnStatus({ uturn_state: { ...u2.uturn_state, is_today: false } }).endsWith("(last session 2026-10-29)"));
assert.strictEqual(T.tmUturnStatus({}), ""); assert.strictEqual(T.tmUturnStatus(null), "");
assert.deepStrictEqual(T.tmUturnLevels(u2), [{ price: 74152, color: "#c084fc", title: "session high" }, { price: 73249, color: "#38bdf8", title: "session low" }]);
assert.deepStrictEqual(T.tmUturnLevels({ uturn_state: { ...u2.uturn_state, is_today: false } }), []);                       // no lines for a past session
assert.ok(T.tmIndNote({ winners: true, uturn: true }, u2).includes("Safe U-turn: Watching:")); assert.ok(!T.tmIndNote({ winners: true, uturn: false }, u2).includes("Watching"));
console.log("arena_terminal_ind status ok");

/* volume strip + relative-volume text */
{
  const T = require("../static/js/arena_terminal.js"), assert = require("assert");
  const cs = []; for (let i = 0; i < 30; i++) cs.push({ time: i, open: 100, close: i % 2 ? 101 : 99, volume: 100 });
  cs.push({ time: 30, open: 100, close: 102, volume: 250 }); cs.push({ time: 31, open: 100, close: 99, volume: 310 }); cs.push({ time: 32, open: 100, close: 99, volume: 120 });
  const v = T.tmVolume(cs); assert.strictEqual(v.bars.length, 33); assert.ok(v.any);
  assert.deepStrictEqual(v.marks.map((m) => m.text), ["2.5x", "3x"]);                                  // 250/100 -> 2.5x, ~310/~106 -> 3x; 120 is below 2x
  assert.strictEqual(T.tmVolume(cs.map((c) => ({ ...c, volume: 0 }))).any, false);                     // an index without a future: no strip, no text
  assert.strictEqual(v.bars[30].color.includes("34,197,94"), true);
  console.log("volume ok");
}
{
  const T = require("../static/js/arena_terminal.js"), assert = require("assert");
  const v = T.tmCumToVolume([{ time: 1, cum: 1000 }, { time: 2, cum: 1000 }, { time: 3, cum: 1300 }, { time: 4, cum: 0 }, { time: 5, cum: 1350 }]).map((x) => x.volume);
  assert.deepStrictEqual(v, [0, 0, 300, 0, 50]);                                                       // first bar 0 (not the whole day), unchanged total 0, growth = volume, a missing print is skipped
  console.log("cum->volume ok");
}
