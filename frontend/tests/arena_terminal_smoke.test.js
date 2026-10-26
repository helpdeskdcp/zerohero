"use strict";
// Runs the Terminal tab script against stubbed DOM / Lightweight-Charts / fetch: a runtime error (ReferenceError, wrong API use) anywhere in the live -> select -> chart -> tick flow fails this test.
const test = require("node:test"), assert = require("node:assert"), path = require("node:path");
function el(id) {
  return { _id: id, dataset: {}, style: {}, children: [], hidden: false, textContent: "", innerHTML: "", className: "", clientWidth: 800, clientHeight: 500, onclick: null,
    classList: { toggle() {}, add() {}, remove() {} }, append(c) { this.children.push(c); }, replaceChildren() { this.children = []; }, insertBefore(c) { this.children.push(c); }, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {}, setAttribute() {}, getAttribute() { return null; } };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
test("terminal script: live rows -> select -> option chart with price lines -> ticks, no runtime error", async () => {
  const els = {}, getEl = (id) => (els[id] = els[id] || el(id)), calls = { setData: 0, update: 0, lines: 0, markers: 0, series: 0, fetched: [] }, tabBtns = [], viewBtns = [];
  const series = () => { calls.series++; return { setData() { calls.setData++; }, update() { calls.update++; }, createPriceLine() { calls.lines++; return {}; }, removePriceLine() {} }; };
  const chartApi = { timeScale: () => ({ fitContent() {}, subscribeVisibleLogicalRangeChange() {}, applyOptions() {}, options: () => ({ barSpacing: 6 }) }), addSeries: series, removeSeries() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const rowsNow = [{ id: "H1", underlying: "NATURALGAS", expiry: "2026-11-20", strike: 250, option_type: "CE", label: "NATURALGAS 250 CE (20/11)", state: "ACTIVE", entry: 2.4, fill: 2.45, sl: 1.3, targets: [3.5, 4.6, 5.7], ltp: 2.6, bid: 2.55, ask: 2.65, pnl_rs: 1500, lots: 1, posted_ist: "09:20:00", posted_ts: Math.floor(Date.now() / 1000) - 600, quote_age_s: 2 },
    { id: "H2", underlying: "NATURALGAS", expiry: "2026-11-20", strike: 255, option_type: "PE", label: "NATURALGAS 255 PE (20/11)", state: "WAITING", entry_mode: "LIMIT", entry: 3, sl: 2, targets: [4], ltp: 3.4, bid: 3.3, ask: 3.5, lots: 1, posted_ist: "09:21:00" },
    { id: "H3", underlying: "NIFTY", expiry: "2026-10-27", strike: 24000, option_type: "CE", label: "NIFTY 24000 CE", state: "CLOSED", entry: 100, fill: 101, exit: 120, why: "TARGET", pnl_rs: 900, lots: 1, posted_ist: "09:16:00" }];
  const now = Math.floor(Date.now() / 1000) + 19800, candles = Array.from({ length: 40 }, (_, i) => ({ time: now - (40 - i) * 900, open: 250 + i * 0.1, high: 251 + i * 0.1, low: 249 + i * 0.1, close: 250.5 + i * 0.1 }));
  const viewBtn = { dataset: { tmview: "underlying" }, classList: { toggle() {} }, onclick: null }; viewBtns.push(viewBtn);
  global.document = { hidden: false, getElementById: getEl, querySelector: (s) => getEl(s), querySelectorAll: (s) => (s === "[data-tab]" ? tabBtns : s === "[data-tmview]" ? viewBtns : []), createElement: () => el("new"), addEventListener() {} };
  global.window = { LightweightCharts: { createChart: () => chartApi, CandlestickSeries: 1, LineSeries: 2, createSeriesMarkers: () => ({ setMarkers() { calls.markers++; } }) } };
  global.CSS = { escape: (s) => s }; global.ResizeObserver = class { observe() {} }; global.sessionStorage = { getItem: () => "tok" };
  global.fetch = async (url) => {
    calls.fetched.push(url); const j = (o) => ({ ok: true, json: async () => o });
    if (url.includes("/live")) return j({ now_ist: "09:30:00", rows: rowsNow, total_open_pnl_rs: 1500, realised_today_rs: 900 });
    if (url.includes("/chart/underlying")) return j({ candles, stop: candles.map((c, i) => ({ time: c.time, value: c.low - 1, up: i % 7 < 4 })), ma: candles.map((c) => ({ time: c.time, value: c.close })), markers: [{ time: candles[10].time, side: "BUY", strong: true }, { time: candles[20].time, side: "SELL", strong: false }], spot: 255.5, source: "test" });
    if (url.includes("/chart")) return j({ candles, last: { ltp: 2.6 }, tf: 1 });
    return { ok: false, status: 404, json: async () => ({}) };
  };
  const tabBtn = { dataset: { tab: "terminal" }, addEventListener(ev, cb) { tabBtn.cb = cb; } }; tabBtns.push(tabBtn);
  delete require.cache[require.resolve(path.join(__dirname, "../static/js/arena_terminal.js"))]; require("../static/js/arena_terminal.js");
  assert.ok(tabBtn.cb, "tab listener registered"); tabBtn.cb(); await sleep(400);
  assert.ok(calls.fetched.some((u) => u.includes("/live")), "live polled"); assert.ok(calls.series >= 1 && calls.setData >= 1, "option candles drawn"); assert.ok(calls.lines >= 5, "entry / fill / SL / targets lines drawn: " + calls.lines);
  assert.ok(calls.markers >= 1, "pine / order markers set"); assert.ok(calls.fetched.some((u) => u.includes("/chart?underlying=NATURALGAS") && u.includes("option_type=CE")), "premium chart requested for the first active position");
  assert.ok(String(getEl("tmCntPos").textContent) === "1" && String(getEl("tmCntOrders").textContent) === "1" && String(getEl("tmCntClosed").textContent) === "1", "bucket counts"); assert.ok(getEl("tmTitle").textContent.includes("NATURALGAS 250 CE"));
  await sleep(1700); assert.ok(calls.fetched.filter((u) => u.includes("/live")).length >= 2, "live keeps refreshing"); assert.ok(calls.update >= 1, "chart updates incrementally");
  const setBefore = calls.setData; viewBtn.onclick(); await sleep(300);                                           // switch to Underlying + Pine
  assert.ok(calls.fetched.some((u) => u.includes("/chart/underlying?underlying=NATURALGAS&tf=15")), "underlying view requested"); assert.ok(calls.setData > setBefore + 2, "candles + stop lines + MA drawn");
  await sleep(1700); assert.ok(calls.update >= 2, "underlying forming candle updated from the spot");
  assert.ok(!/^(live|chart):/.test(getEl("tmNote").textContent), "no error surfaced in the note: " + getEl("tmNote").textContent);
  process.exit(0);
});
