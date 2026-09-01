// Executes app.js in a stub DOM against REAL captured API payloads and asserts
// the view renderers do not throw and produce sane HTML. Dependency-free.
// Run: node frontend/tests/render_smoke.test.js  (needs $FEAUDIT dir of payloads
// or falls back to inline fixtures)
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "js", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

// ---- minimal DOM ------------------------------------------------------------
function makeEl(tag = "div") {
  const el = {
    tagName: (tag || "div").toUpperCase(), children: [], dataset: {}, style: {},
    _html: "", _text: "", classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      toggle(c, f) { f === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c)) : (f ? this._s.add(c) : this._s.delete(c)); },
      contains(c) { return this._s.has(c); },
    },
    hidden: false, value: "", disabled: false, checked: false, placeholder: "",
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get className() { return [...this.classList._s].join(" "); },
    set className(v) { this.classList._s = new Set(String(v).split(/\s+/).filter(Boolean)); },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    _handlers: {},
    addEventListener(type, fn) { (this._handlers[type] = this._handlers[type] || []).push(fn); },
    removeEventListener() {},
    click() { (this._handlers.click || []).forEach(fn => { try { fn.call(this, { preventDefault() {}, target: this }); } catch (e) { errors.push("click handler: " + e.message); } }); },
    appendChild(c) { this.children.push(c); },
    prepend(c) { this.children.unshift(c); }, removeChild(c) { this.children = this.children.filter(x => x !== c); },
    querySelector() { return makeEl(); }, querySelectorAll() { return []; },
    closest() { return null; }, focus() {}, reset() {}, remove() {},
  };
  return el;
}
const REG = new Map();
function elFor(sel) {
  if (!REG.has(sel)) {
    let el = makeEl();
    // a <form> is accessed as f.fieldName -> return a stub input for anything
    if (/form/i.test(sel)) {
      el = new Proxy(el, {
        get(t, p) {
          if (p in t) return t[p];
          if (typeof p === "string") { const i = makeEl("input"); return i; }
          return undefined;
        },
      });
    }
    REG.set(sel, el);
  }
  return REG.get(sel);
}
// pre-register every id referenced in index.html so $("#x") is never null
for (const m of HTML.matchAll(/id="([^"]+)"/g)) elFor("#" + m[1]);

const document = {
  querySelector: (s) => elFor(s),
  querySelectorAll: () => [],
  createElement: (t) => makeEl(t),
  getElementById: (id) => elFor("#" + id),
  addEventListener() {},
  body: makeEl("body"),
};

// ---- fixtures (real captured payloads if present) --------------------------
const FE = process.env.FEAUDIT || "/root/.claude/jobs/ba6f2282/tmp/feaudit";
const load = (name, fallback) => {
  try { return JSON.parse(fs.readFileSync(path.join(FE, name + ".json"), "utf8")); }
  catch { return fallback; }
};
const P = {
  "/api/health": load("health", { status: "ok", live_trading: false, paper_mode: true }),
  "/api/env-check": load("envcheck", { ANGEL_API_KEY: true, TELEGRAM_BOT_TOKEN: false }),
  "/api/autoscalp/selfcheck": load("as_selfcheck", { ok: true, market_open: false, segments: {}, checks: {}, bars_ready: {}, config_warnings: [], entry_blocks: {} }),
  "/api/autoscalp/report": load("as_report", { day_ist: "2026-09-01", totals: { trades: 0 }, per_symbol: {}, zero_to_hero: [], note: "PAPER" }),
  "/api/autoscalp/status": load("as_status", { paper_mode: true, safeguards: {}, config: {} }),
  "/api/autoscalp/universe": load("as_universe", { watchlist: [], groups: {} }),
  "/api/autoscalp/signals": load("as_signals", []),
  "/api/autoscalp/snapshots": load("as_snapshots", []),
  "/api/research": load("research", { signals: { by_decision: {}, by_market_regime: {} }, paper_trades: {}, by_strategy: {} }),
  "/api/monitor": load("monitor", { ts: Date.now(), runner: {}, feed: {}, positions: [], scalps: [], combos: [], reversals: [], turning_points: [], execution: {}, recent_signals: [] }),
  "/api/scalp/status": load("scalp_status", { feed: {}, config: {} }),
  "/api/scalp/trades": load("signals", []),
  "/api/positions": [],
  "/api/signals": load("signals", []),
  "/api/trades": load("trades", []),
  "/api/market/calendar": load("calendar", { segments: {} }),
  "/api/instruments": { instruments: [] },
  "/api/market-instruments": { instruments: [], data_status: "DATA_UNAVAILABLE" },
};
function matchPayload(url) {
  const base = url.split("?")[0];
  if (P[base] !== undefined) return P[base];
  if (base in P) return P[base];
  // prefix
  for (const k of Object.keys(P)) if (base === k) return P[k];
  return P[base] ?? {};
}
const errors = [];
global.fetch = async (url) => ({
  ok: true, status: 200, json: async () => matchPayload(String(url)),
  text: async () => "",
});
global.WebSocket = function () { this.readyState = 0; this.close = () => {}; };
global.WebSocket.prototype = {};
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.location = { protocol: "http:", host: "x", href: "http://x/", reload() {} };
global.prompt = () => null; global.confirm = () => false; global.alert = () => {};
global.navigator = { clipboard: { writeText: async () => {} } };
let timers = 0;
global.setInterval = () => { timers++; return timers; };
global.setTimeout = (fn) => { return 0; };   // don't actually run deferred
global.clearInterval = () => {}; global.clearTimeout = () => {};
global.document = document;
global.window = global;
global.__CHK_TEST__ = true;
global.FormData = function () { this.get = () => ""; };

process.on("unhandledRejection", (e) => errors.push("unhandledRejection: " + (e && e.message || e)));

// ---- run --------------------------------------------------------------------
try {
  vm.runInThisContext(SRC, { filename: "app.js" });
} catch (e) {
  console.error("app.js threw on load:", e);
  process.exit(1);
}

(async () => {
  const chk = global.__chk;
  assert.ok(chk, "test seam window.__chk must be present");
  const flush = () => new Promise(r => setImmediate(r));

  // drive every view loader against the REAL captured payloads
  for (const name of ["loadOverview", "loadSignals", "loadTrades", "loadScalp",
                      "loadResearch", "loadSystem", "loadReport", "loadAutoscalp",
                      "loadMonitor"]) {
    try { await chk[name](); await flush(); await flush(); }
    catch (e) { errors.push(name + " threw: " + e.message); }
  }

  // exercise the WS feed renderer with a few shapes incl. missing fields
  for (const sig of [
    { direction: "BUY", underlying: "NIFTY", decision: "BUY_CE", market_regime: "TRENDING_UP", probability: 0.61, created_ts: new Date().toISOString() },
    { direction: "SELL", symbol: "CRUDEOIL", decision: "NO_TRADE", probability: null },
    {},
    { underlying: "<img src=x onerror=alert(1)>", decision: "<b>x</b>", direction: "BUY" },
  ]) { try { chk.prependFeed(sig); } catch (e) { errors.push("prependFeed threw: " + e.message); } }

  assert.ok(errors.length === 0, "runtime errors:\n  " + errors.join("\n  "));

  // the new health + report panels actually rendered content
  const banner = elFor("#healthBanner").textContent || elFor("#healthBanner").innerHTML;
  assert.ok(/HEALTHY|ATTENTION/.test(banner), "health banner should render a state, got: " + banner);
  const rb = elFor("#reportBody").innerHTML;
  assert.ok(rb.length > 0, "report body should render");
  const sg = elFor("#sysGrid").innerHTML;
  assert.ok(/DISABLED ✓|ENABLED ⚠/.test(sg), "system grid must show explicit live-trading text");

  // the hostile payload must be entity-escaped, not a live tag
  const feedHtml = elFor("#liveFeed").children.map(c => c.innerHTML).join("");
  assert.ok(/&lt;img src=x/.test(feedHtml), "hostile markup should be entity-escaped");
  assert.ok(!/<img\s|<script|<b>x<\/b>/i.test(feedHtml), "no live tag from feed data: " + feedHtml.slice(0, 240));

  console.log("render smoke: 9 view loaders + feed renderer OK against real payloads, no runtime errors, output escaped");
  process.exit(0);
})().catch(e => { console.error("render smoke FAILED:", e && e.stack || e); process.exit(1); });
