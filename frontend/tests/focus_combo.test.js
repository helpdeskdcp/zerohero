/**
 * Deterministic DOM interaction test for the Math Scalper Focus combobox.
 *
 * Rendered-browser verification via Claude-in-Chrome is unavailable from this
 * environment, so this drives the REAL app.js event handlers through a compact
 * functional DOM (elements, class lists, data-* datasets, event dispatch with
 * bubbling to document, activeElement) and performs the full acceptance flow:
 * load -> click Focus -> type -> matching suggestions -> click/keyboard select
 * -> input + active context update -> Escape / outside-click close -> Refresh
 * preserves Focus -> and the market-map A..E cases (full / partial / empty /
 * failed / slow) all keep search working on the fallback universe.
 *
 * Run: node frontend/tests/focus_combo.test.js
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "js", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
const ERRORS = [];

// ---------------------------------------------------------------- compact DOM
function classList() {
  return {
    _s: new Set(),
    add(...c) { c.forEach(x => this._s.add(x)); },
    remove(...c) { c.forEach(x => this._s.delete(x)); },
    toggle(c, f) { const on = f === undefined ? !this._s.has(c) : !!f; on ? this._s.add(c) : this._s.delete(c); },
    contains(c) { return this._s.has(c); },
  };
}
function parseLis(html, parent) {
  const out = [];
  const re = /<li\b([^>]*)>([\s\S]*?)<\/li>/g;
  let m;
  while ((m = re.exec(html))) {
    const li = makeEl("li");
    li.parent = parent;
    const attrs = m[1];
    const cls = /class="([^"]*)"/.exec(attrs);
    if (cls) li.className = cls[1];
    const id = /id="([^"]*)"/.exec(attrs);
    if (id) li._attrs.id = id[1];
    const di = /data-idx="([^"]*)"/.exec(attrs);
    if (di) { li._attrs["data-idx"] = di[1]; li.dataset.idx = di[1]; }
    const as = /aria-selected="([^"]*)"/.exec(attrs);
    if (as) li._attrs["aria-selected"] = as[1];
    li._text = m[2].replace(/<[^>]+>/g, "");
    out.push(li);
  }
  return out;
}
const DOC = { activeElement: null, _listeners: {}, _byId: new Map() };
function makeEl(tag = "div") {
  const el = {
    tagName: (tag || "div").toUpperCase(), _attrs: {}, _children: [], parent: null,
    classList: classList(), _listeners: {}, _text: "", _html: "", _value: "", dataset: {},
    style: {}, disabled: false,
    getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; },
    setAttribute(k, v) {
      this._attrs[k] = String(v);
      if (k.indexOf("data-") === 0) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v);
    },
    removeAttribute(k) { delete this._attrs[k]; },
    get id() { return this._attrs.id || ""; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this._html = ""; this._children = []; },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); this._children = parseLis(this._html, this); },
    get hidden() { return this._attrs.hidden === "" || this._attrs.hidden === "true"; },
    set hidden(b) { if (b) this._attrs.hidden = ""; else delete this._attrs.hidden; },
    get value() { return this._value; },
    set value(v) { this._value = v == null ? "" : String(v); },
    get className() { return [...this.classList._s].join(" "); },
    set className(v) { this.classList._s = new Set(String(v).split(/\s+/).filter(Boolean)); },
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
    removeEventListener() {},
    select() {},
    focus() { DOC.activeElement = this; this._dispatch("focus"); },
    blur() { if (DOC.activeElement === this) DOC.activeElement = null; this._dispatch("blur"); },
    contains(node) { let p = node; while (p) { if (p === this) return true; p = p.parent; } return false; },
    closest(sel) {
      let p = this;
      while (p) {
        if (sel[0] === "." && p.classList && p.classList.contains(sel.slice(1))) return p;
        if (sel[0] === "#" && p._attrs && p._attrs.id === sel.slice(1)) return p;
        p = p.parent;
      }
      return null;
    },
    querySelectorAll(sel) {
      if (sel[0] === "." ) return this._children.filter(c => c.classList && c.classList.contains(sel.slice(1)));
      return [];
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || makeEl(); },
    appendChild(c) { c.parent = this; this._children.push(c); },
    scrollIntoView() {},
    click() { this._dispatch("click"); },
    _dispatch(type, extra) {
      const base = { type, target: this, preventDefault() {}, stopPropagation() {} };
      let node = this;
      while (node) {
        (node._listeners[type] || []).forEach(fn => {
          try { fn.call(node, Object.assign({}, base, extra, { currentTarget: node })); }
          catch (e) { ERRORS.push(type + " on <" + (node.tagName || "?") + ">: " + e.message); }
        });
        node = node.parent;
      }
      (DOC._listeners[type] || []).forEach(fn => {
        try { fn.call(DOC, Object.assign({}, base, extra, { currentTarget: DOC })); }
        catch (e) { ERRORS.push("document " + type + ": " + e.message); }
      });
    },
  };
  return el;
}
// register every id in index.html
for (const m of HTML.matchAll(/id="([^"]+)"/g)) {
  if (!DOC._byId.has(m[1])) DOC._byId.set(m[1], makeEl());
}
// wire the combobox subtree so contains()/closest()/bubbling behave
const combo = DOC._byId.get("msFocusCombo");
const inp = DOC._byId.get("msFocus");
const menu = DOC._byId.get("msFocusMenu");
inp._attrs.id = "msFocus"; menu._attrs.id = "msFocusMenu"; combo._attrs.id = "msFocusCombo";
combo.appendChild(inp); combo.appendChild(menu);
DOC._byId.get("msFocusMsg")._attrs.id = "msFocusMsg";

DOC.querySelector = (sel) => {
  if (sel[0] === "#") {
    const id = sel.slice(1).split(" ")[0];
    if (!DOC._byId.has(id)) DOC._byId.set(id, makeEl());
    return DOC._byId.get(id);
  }
  return makeEl();
};
DOC.querySelectorAll = () => [];
DOC.getElementById = (id) => DOC.querySelector("#" + id);
DOC.createElement = (t) => makeEl(t);
DOC.addEventListener = (t, fn) => { (DOC._listeners[t] = DOC._listeners[t] || []).push(fn); };
DOC.body = makeEl("body");

// ---------------------------------------------------------------- fetch stub (per-case)
let MARKET_MAP_MODE = "full";      // full | partial | empty | fail | slow
const FULL_MAP = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY", "BANKEX"];
const PARTIAL_MAP = ["NIFTY", "BANKNIFTY", "SENSEX"];
function mapPayload() {
  const list = MARKET_MAP_MODE === "partial" ? PARTIAL_MAP
    : MARKET_MAP_MODE === "empty" ? []
    : FULL_MAP;
  return { market_map: list.map(s => ({ instrument: s, spot: 100, status: "OK", pivot: 100, gann_balance: 100, nearest_support: 99, nearest_resistance: 101, market_regime: "NEUTRAL", direction: "PE", confluence_score: 30, confidence: 30, signal: "NO_TRADE" })), note: "" };
}
const FIX = {
  "/api/smart-scalper/profiles": { profiles: { BALANCED: { name: "BALANCED" } }, default: "BALANCED" },
  "/api/smart-scalper/ranking": { engine: "SMART_INDEX_SCALPER", universe: FULL_MAP, ranked: [], not_eligible: [{ index: "NIFTY", failed: ["x"], status: "OK", score: 1 }], selection: { primary: null }, calibration: "UNCALIBRATED" },
  "/api/smart-scalper/paper/journal": { overall: { n: 0 }, by_profile: {}, by_instrument: {}, by_market_regime: {}, note: "" },
  "/api/autoscalp/universe": { watchlist: ["NIFTY"], groups: { "NSE Index": ["BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"], "MCX": ["CRUDEOIL", "NATURALGAS"] } },
  "/api/mathematics/signal": { engine: "X", status: "OK", spot: 100, direction: "PE", signal_type: "NO_TRADE", confidence: 30, confluence_score: 30, score_breakdown: {}, market_regime: "NEUTRAL", nearest_support: { center: 99 }, nearest_resistance: { center: 101 }, confluence_zones: [], oi_matrix: { walls: {}, battle_zone: false }, risk_reward: null, reason_codes: [], no_trade_reason: "x", mathematical_levels: { pivots: {}, gann: {} }, support_level: 99, resistance_level: 101 },
  "/api/mathematics/oi": { status: "OK", rows: [], walls: {}, battle_zone: false, pcr: 1 },
  "/api/mathematics/levels": { instrument: "NIFTY", pivots: {}, gann: {} },
  // quiet the app.js boot sequence (setView("overview") + loadInstruments)
  "/api/trades": [], "/api/signals": [], "/api/research": { signals: {}, paper_trades: {}, by_strategy: {} },
  "/api/instruments": { instruments: [] }, "/api/health": { status: "ok", live_trading: false, paper_mode: true },
};
global.__urls = [];
let FAIL_PATHS = new Set();   // exact base paths whose fetch rejects
let HANG_PATHS = new Set();   // exact base paths whose fetch never settles
global.fetch = async (url) => {
  const u = String(url); global.__urls.push(u);
  const base = u.split("?")[0];
  if (HANG_PATHS.has(base)) return new Promise(() => {});
  if (FAIL_PATHS.has(base)) throw new Error("network");
  if (base === "/api/mathematics/market-map") {
    if (MARKET_MAP_MODE === "fail") throw new Error("network");
    if (MARKET_MAP_MODE === "slow") { await new Promise(r => setTimeout(r, 400)); }
    return { ok: true, status: 200, json: async () => mapPayload(), text: async () => "" };
  }
  return { ok: true, status: 200, json: async () => (FIX[base] || {}), text: async () => "" };
};

// ---------------------------------------------------------------- globals + run
global.WebSocket = function () { this.readyState = 0; this.close = () => {}; };
global.WebSocket.prototype = {};
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.location = { protocol: "https:", host: "x", href: "https://x/", reload() {} };
global.navigator = { clipboard: { writeText: async () => {} } };
global.setInterval = () => 0; global.clearInterval = () => {};
const _realTimeout = setTimeout;
global.setTimeout = (fn, ms) => _realTimeout(fn, ms);
global.clearTimeout = () => {};
global.document = DOC; global.window = global; global.__CHK_TEST__ = true;
global.FormData = function () { this.get = () => ""; };
process.on("unhandledRejection", (e) => ERRORS.push("unhandledRejection: " + (e && e.message || e)));

vm.runInThisContext(SRC, { filename: "app.js" });

const chk = global.__chk;
const flush = () => new Promise(r => setImmediate(r));
const wait = (ms) => new Promise(r => _realTimeout(r, ms));
const items = () => menu._children.filter(c => c.classList.contains("ms-combo-item")).map(c => c.dataset.idx);
function typeInto(el, str) { el.value = str; el._dispatch("input"); }
function press(el, key) { el._dispatch("keydown", { key }); }

(async () => {
  assert.ok(chk && chk.msCommitFocus, "test seam present");
  await chk.loadMathScalp();
  await flush(); await flush();

  // --- (b) placeholder / initial state ---
  assert.equal(/placeholder="([^"]*)"/.exec(HTML.match(/id="msFocus"[\s\S]*?\/>/)[0])[1], "Search index…",
    "Focus input has a search placeholder in the markup");
  assert.equal(chk.msSelectedFocus(), "NIFTY", "(b) defaults to NIFTY");
  assert.equal(inp.value, "NIFTY", "(b) input reflects the selection, not left blank");

  // --- (c) click Focus opens the FULL list ---
  inp.focus();
  assert.equal(menu.hidden, false, "(c) focusing opens the dropdown");
  assert.ok(items().length >= 6, "(c) the whole universe is shown on open, not just current-value matches: " + items());

  // --- (d)(e) type 'NA' -> matching suggestions appear ---
  typeInto(inp, "NA");
  assert.equal(menu.hidden, false, "(e) dropdown stays open while typing");
  assert.ok(items().length >= 1 && items().every(s => s.includes("NA")), "(e) 'NA' filters to matches: " + items());
  assert.ok(items().includes("NATURALGAS"), "(e) 'NA' matches NATURALGAS");

  // 'BANK' -> BANKNIFTY + BANKEX
  typeInto(inp, "BANK");
  assert.deepEqual(items().sort(), ["BANKEX", "BANKNIFTY"], "'BANK' -> BANKNIFTY + BANKEX: " + items());
  // 'NIFTY' -> NIFTY, FINNIFTY, MIDCPNIFTY, BANKNIFTY
  typeInto(inp, "nifty");
  assert.ok(["NIFTY", "FINNIFTY", "MIDCPNIFTY", "BANKNIFTY"].every(s => items().includes(s)),
    "'nifty' (case-insensitive) -> all *NIFTY* indices: " + items());

  // --- (f)(g)(h) select NIFTY by clicking the option ---
  typeInto(inp, "");
  const liNifty = menu._children.find(c => c.dataset.idx === "NIFTY");
  assert.ok(liNifty, "NIFTY option present");
  liNifty._dispatch("click");
  await flush(); await flush();
  assert.equal(inp.value, "NIFTY", "(g) input value becomes NIFTY");
  assert.equal(chk.msSelectedFocus(), "NIFTY", "(h) active focus becomes NIFTY");
  assert.equal(menu.hidden, true, "menu closes after selection");

  // --- (i)(j)(k) change to BANK, select BANKNIFTY, active focus updates + a scan fires ---
  inp.focus(); typeInto(inp, "BANK");
  const nBefore = global.__urls.length;
  menu._children.find(c => c.dataset.idx === "BANKNIFTY")._dispatch("click");
  await flush(); await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), "BANKNIFTY", "(k) active focus becomes BANKNIFTY");
  assert.equal(inp.value, "BANKNIFTY", "(k) input shows BANKNIFTY");
  assert.ok(global.__urls.slice(nBefore).some(u => /symbol=BANKNIFTY/.test(u)),
    "(k) selecting BANKNIFTY triggers a fresh scan for it: " + global.__urls.slice(nBefore).join(", "));

  // --- keyboard selection: focus, ArrowDown x2, Enter ---
  inp.focus();
  assert.equal(menu.hidden, false, "keyboard: menu open on focus");
  press(inp, "ArrowDown"); press(inp, "ArrowDown"); press(inp, "ArrowDown");
  const active = menu._children.find(c => c.classList.contains("is-active"));
  assert.ok(active, "keyboard: ArrowDown highlights an option");
  const kbPick = active.dataset.idx;
  press(inp, "Enter");
  await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), kbPick, "keyboard: Enter selects the highlighted option (" + kbPick + ")");

  // --- Escape closes + reverts ---
  inp.focus(); typeInto(inp, "SEN");
  assert.equal(menu.hidden, false);
  press(inp, "Escape");
  assert.equal(menu.hidden, true, "Escape closes the dropdown");
  assert.equal(inp.value, chk.msSelectedFocus(), "Escape reverts the input to the current selection");

  // --- outside click closes ---
  inp.focus();
  assert.equal(menu.hidden, false);
  DOC.body._dispatch("click");
  assert.equal(menu.hidden, true, "clicking outside closes the dropdown");

  // --- (l)/(m) Refresh preserves Focus ---
  chk.msCommitFocus("BANKNIFTY"); await flush(); await flush();
  DOC._byId.get("msRefresh")._dispatch("click");
  await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), "BANKNIFTY", "(l) Refresh preserves the selected Focus");
  assert.equal(inp.value, "BANKNIFTY", "(l) Refresh leaves the input on BANKNIFTY");

  // --- back to NIFTY: active + ranking context switch back ---
  const nBack = global.__urls.length;
  chk.msCommitFocus("NIFTY");
  await flush(); await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), "NIFTY", "(m) switching back sets active context to NIFTY");
  assert.ok(global.__urls.slice(nBack).some(u => /symbol=NIFTY/.test(u)), "(m) NIFTY context refetched");

  // --- validation: unsupported + empty ---
  chk.msCommitFocus("NIFTY");
  assert.equal(chk.msCommitFocus("NOPE"), false, "unsupported index rejected");
  assert.equal(chk.msSelectedFocus(), "NIFTY", "unsupported does not change selection");
  assert.match(chk.msFocusMsg(), /supported index/i);
  const nEmpty = global.__urls.length;
  assert.equal(chk.msCommitFocus(""), false, "empty rejected");
  assert.match(chk.msFocusMsg(), /select an index/i);
  await flush();
  assert.equal(global.__urls.length, nEmpty, "empty Focus makes no API call");

  // --- market-map CASE A..E: search always works on the fallback universe ---
  for (const mode of ["full", "partial", "empty", "fail", "slow"]) {
    MARKET_MAP_MODE = mode;
    const before = global.__urls.length;
    const p = chk.loadMathScalp({ force: true });
    // search must be usable IMMEDIATELY, before the (possibly slow/failed) map resolves
    const f = chk.msFilterUniverse("bank").sort();
    assert.deepEqual(f, ["BANKEX", "BANKNIFTY"], "CASE " + mode + ": fallback search works without waiting for market-map: " + f);
    assert.ok(chk.msFilterUniverse("fin").includes("FINNIFTY"), "CASE " + mode + ": FINNIFTY searchable");
    await p; await flush(); await flush();
    if (mode === "slow") await wait(500);
    assert.ok(global.__urls.length > before, "CASE " + mode + ": the view still issued its requests");
    assert.ok(chk.msUniverseList().length >= 6, "CASE " + mode + ": universe never shrinks below the fallback");
  }
  MARKET_MAP_MODE = "full";

  // --- STALL GUARD: an auto-refresh poll that hits failing/hanging endpoints
  //     must NOT wedge the busy flag — the next poll still issues fresh requests
  chk.msCommitFocus("NIFTY"); await flush(); await flush();
  FAIL_PATHS = new Set(["/api/mathematics/signal", "/api/mathematics/market-map"]);
  await chk.loadMathScalp();                   // poll while endpoints reject
  await flush(); await flush();
  const afterFail = global.__urls.length;
  FAIL_PATHS = new Set();
  await chk.loadMathScalp();                   // the NEXT auto-refresh poll
  await flush(); await flush();
  assert.ok(global.__urls.length > afterFail,
    "after a failed-endpoint poll the next poll still runs — busy flag released, no stall");
  // and a forced reload (Refresh) always runs even mid-hang
  HANG_PATHS = new Set(["/api/mathematics/signal"]);
  const p = chk.loadMathScalp({ force: true }); // returns after the 12s _msFetch race — don't await
  await flush(); await wait(50);
  const afterHang = global.__urls.length;
  HANG_PATHS = new Set();
  await chk.loadMathScalp({ force: true });     // force bypasses the guard regardless
  await flush(); await flush();
  assert.ok(global.__urls.length > afterHang, "a forced reload runs even while a previous request is hung");

  assert.equal(ERRORS.length, 0, "no runtime errors:\n  " + ERRORS.join("\n  "));
  console.log("focus combobox: acceptance flow (b..m) + keyboard + Escape + outside-click + validation + market-map A..E + stall-guard — all OK, no runtime errors");
  process.exit(0);
})().catch(e => { console.error("focus combobox FAILED:", e && e.stack || e); process.exit(1); });
