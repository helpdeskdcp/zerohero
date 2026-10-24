const test = require("node:test");
const assert = require("node:assert");
const { Logic } = require("../static/js/chartscan.js");

test("buildMarkers keeps only on-chart times, sorts, colours by side", () => {
  const candles = [{ time: "2026-10-20" }, { time: "2026-10-21" }, { time: "2026-10-22" }];
  const m = Logic.buildMarkers([{ time: "2026-10-22", side: "SHORT" }, { time: "2026-10-19", side: "LONG" }, { time: "2026-10-20", side: "LONG" }], candles);
  assert.deepStrictEqual(m.map((x) => x.time), ["2026-10-20", "2026-10-22"]);
  assert.strictEqual(m[0].shape, "arrowUp"); assert.strictEqual(m[0].position, "belowBar"); assert.strictEqual(m[1].shape, "arrowDown"); assert.strictEqual(m[1].position, "aboveBar");
  assert.deepStrictEqual(Logic.buildMarkers(null, candles), []);
});

test("volume colours, verdict classes, time and back-test rows", () => {
  const v = Logic.volumeData([{ time: 1, open: 1, close: 2, volume: 5 }, { time: 2, open: 2, close: 1, volume: 0 }]);
  assert.strictEqual(v[0].value, 5); assert.ok(v[0].color.includes("52,211,153")); assert.ok(v[1].color.includes("248,113,113"));
  assert.strictEqual(Logic.verdictClass("GO-SHADOW"), "ok"); assert.strictEqual(Logic.verdictClass("NO-GO"), "bad"); assert.strictEqual(Logic.verdictClass("INSUFFICIENT_SAMPLE"), "warn");
  assert.strictEqual(Logic.fmtTime("2026-10-22", "1d"), "2026-10-22"); assert.strictEqual(Logic.fmtTime(1792800000, "5m"), "2026-10-24 00:00");
  assert.deepStrictEqual(Logic.backtestRows({ horizons: { 3: { n: 10, mean_net_bps: 1.234, hit: 0.5 } } }), [{ h: "3", n: 10, mean: 1.2, hit: 50 }]); assert.deepStrictEqual(Logic.backtestRows(null), []);
});
