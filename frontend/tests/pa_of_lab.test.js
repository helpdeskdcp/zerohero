const test = require("node:test"), assert = require("node:assert");
const L = require("../static/js/pa_of_lab.js");
const P = (o = {}) => ({ meta: { tf_sec: 300, price_source: "CLASSIFIED_PROXY" }, status: { state: "OK" }, bars: [{ t: 1000, o: 1, h: 2, l: 0.5, c: 1.5, closed: true }, { t: 1300, o: 1.5, h: 2, l: 1, c: 1.8, closed: false }],
  pa: { pivots: [{ t: 1000, type: "SH", label: "HH", price: 2, rule: "swing", status: "CONFIRMED", confirmed_t: 1900, id: "a" }], provisional: [{ t: 1300, type: "SL", price: 1, rule: "r", status: "PROVISIONAL", id: "p" }],
    events: [{ t: 1300, type: "BOS_UP", price: 2, rule: "x", status: "CONFIRMED", confirmed_t: 1600, id: "b" }, { t: 1300, type: "REJECTION_BULL", price: 1, rule: "r", status: "CONFIRMED", confirmed_t: 1600, id: "c" }],
    signals: [{ id: "s", t: 1300, idx: 1, side: 1, entry: 2, stop: 1, t1: 3, t2: 4, rule: "r", status: "CONFIRMED", confirmed_t: 1600 }] }, of: { signals: [] }, combined: [{ idx: 1, state: "PRICE ACTION CONFIRMED", of_status: "ORDER FLOW UNVERIFIED" }],
  footprint: { available: true, bars: [{ t: 1000, cvd_segment: 5, delta: 5, dq: { status: "OK" } }, { t: 1600, cvd_segment: 9, delta: 4, dq: { status: "DEGRADED" } }] }, ...o });

test("states", () => {
  assert.equal(L.describeState(null).kind, "loading"); assert.equal(L.describeState(null, new Error("x")).kind, "error");
  assert.equal(L.describeState(P({ status: { state: "NO_DATA", message: "none" } })).kind, "nodata"); assert.equal(L.describeState(P({ status: { state: "STALE" } })).kind, "stale"); assert.equal(L.describeState(P()).kind, "ok");
});
test("candles shift to IST and mark open bar", () => { const c = L.candleData(P()); assert.equal(c[0].time, 1000 + 19800); assert.ok(!c[0].color); assert.ok(c[1].color); });
test("markers: confirmed vs provisional never merged, honour filters, sorted", () => {
  const m = L.buildMarkers(P(), { swing: true, structure: true, liquidity: true, rejection: false, signal: true });
  assert.deepEqual(m.map((x) => x.text).sort(), ["BOS UP", "HH", "PA L", "SL?"]);
  assert.ok(m.some((x) => x.text === "SL?" && x.color === "#94a3b8")); assert.ok(!m.some((x) => x.text === "rej+")); assert.ok(m.every((x, i, a) => !i || a[i - 1].time <= x.time));
});
test("every plotted event exposes timestamp, price, rule, status", () => { for (const e of L.allEvents(P())) for (const k of ["t", "price", "rule", "status"]) assert.ok(e[k] !== undefined, k); });
test("signal filter + combined join", () => { const s = L.filterSignals(P(), "ALL", 0); assert.equal(s[0].combined.of_status, "ORDER FLOW UNVERIFIED"); assert.equal(L.filterSignals(P(), "OF", 0).length, 0); assert.equal(L.filterSignals(P(), "PA", -1).length, 0); });
test("footprint cells: none fabricated when levels absent, imbalance flagged", () => {
  assert.deepEqual(L.footprintCells({ levels: null }, (p) => p, 0.1), []); assert.deepEqual(L.footprintCells(null, (p) => p, 0.1), []);
  const c = L.footprintCells({ levels: [[100, 40, 10], [100.1, 5, 50], [100.2, 3, 3]] }, (p) => 1000 - p * 10, 0.1);
  assert.deepEqual(c.map((x) => x.imbalance), [1, -1, 0]); assert.ok(c[0].h > 0);
});
test("cvd line breaks (whitespace point) across a gap", () => { const s = L.cvdSeries(P()); assert.equal(s.length, 3); assert.equal(s[1].value, undefined); });
test("delta bars grey when data quality not OK", () => { const d = L.deltaSeries(P()); assert.equal(d[1].color, "#64748b"); assert.equal(d[0].color, "#22c55e"); });

test("footprint bins are lossless sums and imbalance is judged per display bin", () => {
  const lv = [[100.0, 10, 1], [100.1, 10, 1], [100.2, 1, 20], [100.3, 2, 30]];
  const one = L.footprintCells({ levels: lv }, (p) => 1000 - p * 10, 0.1, 1), two = L.footprintCells({ levels: lv }, (p) => 1000 - p * 10, 0.1, 2);
  const sum = (c) => [c.reduce((a, x) => a + x.buy, 0), c.reduce((a, x) => a + x.sell, 0)];
  assert.deepEqual(sum(one), [23, 52]); assert.deepEqual(sum(two), [23, 52]); assert.equal(two.length, 2);
  assert.equal(two[0].imbalance, 1); assert.equal(two[1].imbalance, -1);
});
