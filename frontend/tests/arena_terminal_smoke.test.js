"use strict";
// Runs the Terminal tab script against stubbed DOM / Lightweight-Charts / fetch: a runtime error (ReferenceError, wrong API use) anywhere in the live -> select -> chart -> tick flow fails this test.
const test = require("node:test"), assert = require("node:assert"), path = require("node:path");
function el(id) {
  return { _id: id, dataset: {}, style: {}, children: [], hidden: false, textContent: "", innerHTML: "", className: "", clientWidth: 800, clientHeight: 500, onclick: null,
    classList: { toggle() {}, add() {}, remove() {} }, append(c) { this.children.push(c); }, replaceChildren() { this.children = []; }, insertBefore(c) { this.children.push(c); }, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {}, setAttribute() {}, getAttribute() { return null; } };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const body = async () => {
  const created = [], posts = [], els = {}, getEl = (id) => (els[id] = els[id] || el(id)), calls = { setData: 0, update: 0, lines: 0, markers: 0, series: 0, fetched: [] }, tabBtns = [], viewBtns = [];
  const series = () => { calls.series++; return { setData() { calls.setData++; }, update() { calls.update++; }, createPriceLine() { calls.lines++; return {}; }, removePriceLine() {} }; };
  const chartApi = { timeScale: () => ({ fitContent() {}, subscribeVisibleLogicalRangeChange() {}, applyOptions() {}, options: () => ({ barSpacing: 6 }) }), addSeries: series, removeSeries() {}, applyOptions() {}, priceScale: () => ({ applyOptions() {} }) };
  const rowsNow = [{ id: "H1", underlying: "NATURALGAS", expiry: "2026-11-20", strike: 250, option_type: "CE", label: "NATURALGAS 250 CE (20/11)", state: "ACTIVE", entry: 2.4, fill: 2.45, sl: 1.3, targets: [3.5, 4.6, 5.7], ltp: 2.6, bid: 2.55, ask: 2.65, pnl_rs: 1500, lots: 1, posted_ist: "09:20:00", posted_ts: Math.floor(Date.now() / 1000) - 600, quote_age_s: 2 },
    { id: "H2", underlying: "NATURALGAS", expiry: "2026-11-20", strike: 255, option_type: "PE", label: "NATURALGAS 255 PE (20/11)", state: "WAITING", entry_mode: "LIMIT", entry: 3, sl: 2, targets: [4], ltp: 3.4, bid: 3.3, ask: 3.5, lots: 1, posted_ist: "09:21:00" },
    { id: "H3", underlying: "NIFTY", expiry: "2026-10-27", strike: 24000, option_type: "CE", label: "NIFTY 24000 CE", state: "CLOSED", entry: 100, fill: 101, exit: 120, why: "TARGET", pnl_rs: 900, lots: 1, posted_ist: "09:16:00" }];
  const now = Math.floor(Date.now() / 1000) + 19800, candles = Array.from({ length: 40 }, (_, i) => ({ time: now - (40 - i) * 900, open: 250 + i * 0.1, high: 251 + i * 0.1, low: 249 + i * 0.1, close: 250.5 + i * 0.1 }));
  const viewBtn = { dataset: { tmview: "underlying" }, classList: { toggle() {} }, onclick: null }; viewBtns.push(viewBtn);
  global.document = { hidden: false, getElementById: getEl, querySelector: (s) => getEl(s), querySelectorAll: (s) => (s === "[data-tab]" ? tabBtns : s === "[data-tmview]" ? viewBtns : []), createElement: () => { const e = el("new"); created.push(e); return e; }, addEventListener() {} };
  global.window = { TM_EXIT_DELAY_MS: 150, LightweightCharts: { createChart: () => chartApi, CandlestickSeries: 1, LineSeries: 2, createSeriesMarkers: () => ({ setMarkers() { calls.markers++; } }) } };
  getEl("tmList").querySelector = (sel) => { const m = /data-id="([^"]+)"/.exec(sel); return m ? created.slice().reverse().find((e) => e.dataset && e.dataset.id === m[1] && e.className === "tm-row") || null : null; };   // like the real DOM: an existing row is found and reused
  global.CSS = { escape: (s) => s }; global.ResizeObserver = class { observe() {} }; global.sessionStorage = { getItem: () => "tok" };
  global.fetch = async (url, opts) => {
    calls.fetched.push(url); if (opts && opts.method === "POST") { posts.push({ url, body: JSON.parse(opts.body), headers: opts.headers }); return { ok: true, json: async () => ({ id: "E1", action: "EXIT_NOW" }) }; } const j = (o) => ({ ok: true, json: async () => o });
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
  // ---- one-tap exit: ACTIVE rows carry the button, CLOSED rows do not; tap -> countdown -> POST /amend EXIT_NOW; tapping during the countdown cancels
  const rowEl = (id) => created.slice().reverse().find((e) => e.dataset && e.dataset.id === id), tap = (id) => rowEl(id).onclick({ target: { closest: () => ({}) }, stopPropagation() {} });
  assert.ok(rowEl("H1") && rowEl("H1").innerHTML.includes('data-exit="H1"'), "ACTIVE row has the EXIT button"); assert.ok(!/data-exit/.test(rowEl("H1").innerHTML.replace('data-exit="H1"', "")), "only one button");
  tap("H1"); tap("H1"); await sleep(400); assert.strictEqual(posts.length, 0, "second tap during the countdown cancels: nothing sent");
  tap("H1"); await sleep(120); assert.strictEqual(posts.length, 0, "not sent before the countdown ends"); assert.ok(rowEl("H1").innerHTML.includes("tap to CANCEL"), "countdown label shown");
  await sleep(400); assert.strictEqual(posts.length, 1, "sent exactly once after the countdown"); assert.strictEqual(posts[0].url, "/api/arena/public/amend"); assert.deepStrictEqual({ s: posts[0].body.signal_id, a: posts[0].body.action }, { s: "H1", a: "EXIT_NOW" }); assert.strictEqual(posts[0].headers["X-Arena-Token"], "tok");
  await sleep(300); assert.strictEqual(posts.length, 1, "no duplicate send"); assert.ok(rowEl("H1").innerHTML.includes("EXIT SENT"), "row shows EXIT SENT while the trade settles"); tap("H1"); await sleep(300); assert.strictEqual(posts.length, 1, "tap after sent is ignored");
  assert.ok(!getEl("tmNote").textContent.startsWith("Exit failed"), "no error surfaced");
  assert.ok(!/^(live|chart):/.test(getEl("tmNote").textContent), "no error surfaced in the note: " + getEl("tmNote").textContent);
  process.exit(0);
};
test("terminal script: live rows -> select -> chart -> ticks -> one-tap exit, no runtime error", async () => { try { await body(); } catch (e) { console.error("SMOKE FAIL:", e && e.message); process.exit(1); } });
