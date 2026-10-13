// Dependency-free checks for the live chart delta client. Run with: node frontend/tests/live_chart.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { ChartFeed } = require("../static/js/live_chart.js");

function fakeSeries() { return { data: [], updates: [], setData(d) { this.data = d; }, update(b) { this.updates.push(b); } }; }
const c = (t, o = 1) => ({ time: t, open: o, high: o + 1, low: o - 1, close: o, ts: t });
let resyncs = 0;
const s = fakeSeries();
const f = new ChartFeed({ series: s, instrument: "I", tf: 60, onResync: () => { resyncs++; } });

assert.equal(f.apply({ t: "candle", i: "I", tf: 60, seq: 1, c: c(100) }), "resync", "a delta before any snapshot must trigger a resync");
assert.equal(f.apply({ t: "snapshot", i: "I", tf: 60, seq: 5, candles: [c(40), c(70)] }), "snapshot");
assert.deepEqual(s.data.map((b) => b.time), [40, 70]);
assert.equal(f.apply({ t: "candle", i: "I", tf: 60, seq: 6, c: c(100) }), "applied");
assert.equal(f.apply({ t: "candle", i: "I", tf: 60, seq: 6, c: c(100) }), "ignored", "duplicate delivery is ignored, not applied twice");
assert.equal(s.updates.length, 1, "only ONE incremental update, no full redraw");
assert.equal(f.apply({ t: "candle", i: "I", tf: 60, seq: 9, c: c(160) }), "resync", "a seq gap must request a resync and not touch the chart");
assert.equal(s.updates.length, 1);
assert.equal(resyncs, 2);
assert.equal(f.apply({ t: "snapshot", i: "I", tf: 60, seq: 9, candles: [c(160)] }), "snapshot");
assert.equal(f.apply({ t: "candle", i: "I", tf: 60, seq: 10, c: c(190) }), "applied");
assert.equal(f.apply({ t: "candle", i: "OTHER", tf: 60, seq: 11, c: c(1) }), "ignored", "other instruments are ignored");
assert.equal(f.apply({ t: "candle", i: "I", tf: 5, seq: 11, c: c(1) }), "ignored", "other timeframes are ignored");
assert.equal(s.data.length, 1, "setData only on snapshots");

// vendored library: pinned, licensed, attribution kept
const src = fs.readFileSync(path.join(__dirname, "..", "static", "js", "live_chart.js"), "utf8");
assert.ok(!/attributionLogo\s*:\s*false/.test(src), "TradingView attribution must not be disabled");
const notices = fs.readFileSync(path.join(__dirname, "..", "static", "vendor", "THIRD_PARTY_NOTICES.md"), "utf8");
assert.match(notices, /5\.2\.1/); assert.match(notices, /Apache-2\.0/); assert.match(notices, /e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf/);
const crypto = require("node:crypto");
const lib = fs.readFileSync(path.join(__dirname, "..", "static", "vendor", "lightweight-charts", "lightweight-charts.standalone.production.js"));
assert.equal(crypto.createHash("sha256").update(lib).digest("hex"), "e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf", "vendored library checksum");
console.log("live_chart.test.js: all assertions passed");
