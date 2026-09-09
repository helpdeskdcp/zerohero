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
  "/api/autoscalp/universe": load("as_universe", {
    watchlist: ["NIFTY"],
    groups: {
      "NSE Index": ["BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"],
      "MCX": ["CRUDEOIL", "GOLD", "NATURALGAS", "SILVER"],
      "Equity (F&O)": ["RELIANCE", "TCS"],
    },
  }),
  "/api/autoscalp/signals": load("as_signals", []),
  "/api/autoscalp/snapshots": load("as_snapshots", []),
  "/api/orderflow/sessions": load("of_sessions", { symbol: "NIFTY", tf: "5m", sessions: ["2026-09-04", "2026-09-03"] }),
  "/api/orderflow/profile": load("of_profile", {
    symbol: "NIFTY", sessions: ["2026-09-04"], tf: "5m", bar_count: 3, status: "OK", vwap: 24050.1,
    volume_profile: {
      status: "OK", method: "OHLCV_RANGE_DISTRIBUTION", tick_size: 5, n_bins: 2,
      session_high: 24060, session_low: 24040, total_volume: 900,
      poc: 24052.5, vah: 24057.5, val: 24047.5, value_pct: 0.7,
      bins: [{ price: 24047.5, volume: 400 }, { price: 24052.5, volume: 500 }],
    },
    market_profile: {
      status: "OK", tick_size: 5, n_bins: 2, tpo_minutes: 30, n_brackets: 2,
      session_high: 24060, session_low: 24040, poc: 24052.5, vah: 24057.5, val: 24047.5,
      value_pct: 0.7, single_prints: [24047.5],
      bins: [{ price: 24047.5, tpo: 1, letters: "A" }, { price: 24052.5, tpo: 2, letters: "AB" }],
    },
  }),
  "/api/orderflow/backtest": load("of_bt", {
    status: "OK", method: "OHLCV_BARS", symbol: "NIFTY", tf: "5m", volume_mult: 2, rr: 3,
    basis: "premium",
    basis_coverage: { basis: "premium", resolved_priced: 5, premium_repriced: 4,
      premium_thin_quotes: 1, index_fallback: 1, index_only: 0, premium_coverage: 0.8 },
    sessions_scanned: 4, traded_sessions: 4, sessions: ["2026-09-01", "2026-09-04"],
    overall: {
      signals: 6, wins: 1, losses: 4, open: 1, win_rate: 0.2, resolved: 5,
      gross_win_points: 60, gross_loss_points: 80, net_points: -20,
      avg_win_points: 60, avg_loss_points: 20, expectancy_points: -4, profit_factor: 0.75,
      max_drawdown_points: -80, reliable: false,
      reliability_reason: "INSUFFICIENT SAMPLE: 5/20 resolved trades, 4/10 distinct sessions -- descriptive only, no edge claim",
      breakeven_win_rate: 0.25, min_sample: 20,
    },
    by_side: {
      BUY: { wins: 1, losses: 2, open: 0, win_rate: 0.333 },
      SELL: { wins: 0, losses: 2, open: 1, win_rate: 0 },
    },
    by_session: {}, trades: [
      { session: "2026-09-04", candle_ts: "2026-09-04T04:00:00Z", side: "BUY", entry: 24060, stop_loss: 24040,
        target: 24120, risk_points: 20, reward_points: 60, rr: 3, breakout_bar: "x", resolved_bar: "y",
        result: "WIN", points: 60, exit_price: 24120, volume_x_avg: 3.3 },
      { session: "2026-09-04", candle_ts: "2026-09-04T04:00:00Z", side: "SELL", entry: 24040, stop_loss: 24060,
        target: 23980, risk_points: 20, reward_points: 60, rr: 3, breakout_bar: "x", resolved_bar: "y",
        result: "LOSS", points: -20, exit_price: 24060, volume_x_avg: 3.3 },
    ],
  }),
  "/api/orderflow/smart-money": load("of_sm", {
    symbol: "NIFTY", sessions: ["2026-09-04"], tf: "5m", status: "OK", method: "OHLCV_BARS",
    session_avg_volume: 300, volume_mult: 2, rr: 3, spike_count: 1,
    setups: [{
      candle: { bar_start: "2026-09-04T04:00:00Z", o: 24040, h: 24060, l: 24040, c: 24055, v: 1000 },
      volume_x_avg: 3.33, range_points: 20,
      buy: { side: "BUY", entry: 24060, stop_loss: 24040, target: 24120, risk_points: 20, reward_points: 60, rr: 3,
             breakout_bar: "2026-09-04T04:05:00Z", outcome: { status: "TARGET_HIT", resolved_bar: "2026-09-04T04:20:00Z" } },
      sell: { side: "SELL", entry: 24040, stop_loss: 24060, target: 23980, risk_points: 20, reward_points: 60, rr: 3,
              breakout_bar: null, outcome: { status: "PENDING", resolved_bar: null } },
    }],
  }),
  "/api/orderflow/h1h7-state": load("of_h1h7", {
    symbol: "NIFTY", sessions: ["2026-09-04"], tf: "5m", mode: "SHADOW_OBSERVATION",
    live_trading: false, bar_count: 70, eligible_bars: 59,
    summary: { H7_TRAP: 1, H1_CONT_OBSERVE: 1, AMBIGUOUS: 2 },
    events: [
      { symbol: "NIFTY", timestamp: "2026-09-04T06:10:00Z", mode: "SHADOW_OBSERVATION", live_trading: false,
        spike_direction: "SHORT", spike_range: 22.0, range_pctile: 0.96, range_x: 3.1, atr: 9.0,
        disp_atr: 1.4, body_fraction: 0.61, broken_level: 24010.0, broken_level_kind: "session_low",
        reclaim_distance: 18.0, reclaim_distance_ratio: 0.82, reclaimed_within_3: true, available_R: null,
        n1_agreement: false, acc1: false, acc2: false, state: "H7_TRAP", action: "AVOID",
        research_status: "SUPPORTED", reason: "hard trap; AVOID -- fading H7 is REJECTED (Stage-8).",
        unobservable_orderflow: ["aggressor_volume", "trade_delta"], unobservable_note: "DATA NOT AVAILABLE" },
      { symbol: "NIFTY", timestamp: "2026-09-04T07:30:00Z", mode: "SHADOW_OBSERVATION", live_trading: false,
        spike_direction: "LONG", spike_range: 15.0, range_pctile: 0.94, range_x: 2.6, atr: 8.0,
        disp_atr: 1.2, body_fraction: 0.58, broken_level: 24040.0, broken_level_kind: "prior_bar_high",
        reclaim_distance: 0.0, reclaim_distance_ratio: 0.0, reclaimed_within_3: false, available_R: 2.1,
        n1_agreement: true, acc1: true, acc2: true, state: "H1_CONT_OBSERVE", action: "NO_ACTION",
        research_status: "NOT_VALIDATED", reason: "NIFTY continuation is NOT_VALIDATED -- observe only.",
        unobservable_orderflow: ["aggressor_volume", "trade_delta"], unobservable_note: "DATA NOT AVAILABLE" },
    ],
    research_status_legend: {
      SUPPORTED: "H7 reclaim-distance boundary as an AVOIDANCE classifier only -- fading H7 is REJECTED.",
      RESEARCH_ONLY_PROMISING: "Small positive expectancy on the UNDERLYING for CRUDEOIL. Never PROVEN.",
      NOT_VALIDATED: "The H1 gate matched but this symbol's edge did not hold across splits.",
      NO_ESTABLISHED_EDGE: "No research edge attaches to this state.",
      UNOBSERVABLE: "DATA NOT AVAILABLE -- no aggressor tick / L2 depth feed.",
      PROVEN: "Not used. Nothing in this research is PROVEN.",
    },
  }),
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
  "/api/smart-scalper/profiles": load("ss_profiles", { profiles: { BALANCED: { name: "BALANCED" } }, default: "BALANCED" }),
  "/api/smart-scalper/ranking": load("ss_ranking", {
    engine: "SMART_INDEX_SCALPER", ranked: [],
    not_eligible: [{ index: "NIFTY", failed: ["acceptable_confidence"], status: "OK", score: 33 }],
    selection: { primary: null }, calibration: "UNCALIBRATED — selection weights are defaults",
  }),
  "/api/smart-scalper/paper/journal": load("ss_journal", {
    overall: { n: 0, status: "NO_TRADES" }, by_profile: {}, by_instrument: {}, by_market_regime: {},
    note: "UNCALIBRATED research journal — no profitability claim.",
  }),
  "/api/smart-scalper/replay": load("ss_replay", {
    status: "INSUFFICIENT_SAMPLE", sample: { sessions: 3, trades: 0, min_sessions: 8, min_trades: 20 },
    metrics: { overall: { n: 0, status: "NO_TRADES" }, by_profile: {}, by_instrument: {}, by_market_regime: {} },
    calibration: { status: "INSUFFICIENT_SAMPLE" }, note: "no profitability claim (spec section 26)",
  }),
  "/api/mathematics/market-map": load("math_map", {
    market_map: [{
      instrument: "NIFTY", spot: 23870, status: "OK", pivot: 23871, gann_balance: 23850,
      nearest_support: 23786, nearest_resistance: 23921, market_regime: "NEUTRAL",
      direction: "PE", confluence_score: 35.8, confidence: 32, signal: "NO_TRADE",
    }], note: "",
  }),
  "/api/mathematics/signal": load("math_signal", {
    engine: "X", status: "OK", spot: 23870, direction: "PE", signal_type: "NO_TRADE",
    confidence: 32, confluence_score: 35.8,
    score_breakdown: { mathematical: { raw: 20, out_of: 20 }, oi: { raw: 1.3, out_of: 20 } },
    market_regime: "NEUTRAL", nearest_support: { center: 23786 }, nearest_resistance: { center: 23921 },
    confluence_zones: [{ center: 23853, evidence_count: 5 }],
    oi_matrix: { walls: {}, battle_zone: false }, risk_reward: null,
    reason_codes: ["<img src=x onerror=alert(1)>"], no_trade_reason: "confluence score below threshold",
    mathematical_levels: { pivots: { pivot: 23871, r1: 23957, s1: 23829 }, gann: { gann_balance: 23850, gann_up_1: 23882, gann_down_1: 23818 } },
    support_level: 23786, resistance_level: 23921,
  }),
  "/api/mathematics/oi": load("math_oi", {
    status: "OK", rows: [{ strike: 23800, ce_oi: 60645, ce_ltp: 354, pe_oi: 2159430, pe_ltp: 17, support_score: 1.7, resistance_score: 0, battle_score: 0 }],
    walls: {}, battle_zone: false, pcr: 1.2,
  }),
  "/api/mathematics/levels": load("math_levels", {
    instrument: "NIFTY", pivots: { pivot: 23871, r1: 23957, s1: 23829 }, gann: { gann_balance: 23850, gann_up_1: 23882 },
  }),
  "/api/optionchain/underlyings": load("oc_underlyings", {
    underlyings: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "CRUDEOIL", "NATURALGAS"],
    groups: { NSE: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"], BSE: ["SENSEX", "BANKEX"], MCX: ["CRUDEOIL", "NATURALGAS"] },
    default: "NIFTY", expiries: ["AUTO", "NEXT", "LATEST"],
  }),
  "/api/optionchain/NIFTY": load("oc_nifty", {
    status: "OK", underlying: "NIFTY", expiry: "15SEP2026", ts: "2026-09-09T07:00:00Z",
    source: "angelone_captured", spot: 23450, atm_strike: 23450,
    expiry_ctx: { phase: "EXPIRY_DAY", dte: 0, is_expiry_day: true, next_expiry: "22SEP2026",
                  oi_signal_reliability: "LOW" },
    available_expiries: ["15SEP2026", "22SEP2026"],
    chain: {
      underlying: "NIFTY", expiry: "15SEP2026", source: "angelone_captured", spot: 23450,
      atm_strike: 23450, n_strikes: 3, notes: ["greek spine snap_key=x", "<img src=x onerror=alert(1)>"],
      rows: [
        { strike: 23400, ce: { oi: 1.8e6, oi_change: 1.2e5, volume: 9e5, iv: 0.115, delta: 0.57, ltp: 168.6 },
          pe: { oi: 5.2e6, oi_change: -3e4, volume: 8e5, iv: 0.099, delta: -0.42, ltp: 91.5 } },
        { strike: 23450, ce: { oi: 2.4e6, oi_change: 2e5, volume: 1.1e6, iv: 0.112, delta: 0.51, ltp: 138.5 },
          pe: { oi: 2.7e6, oi_change: 5e4, volume: 1.0e6, iv: 0.096, delta: -0.49, ltp: 111.1 } },
        { strike: 23500, ce: { oi: 8.0e6, oi_change: 4e5, volume: 2e6, iv: 0.109, delta: 0.45, ltp: 111.0 },
          pe: { oi: 8.3e6, oi_change: 1e5, volume: 2e6, iv: 0.094, delta: -0.55, ltp: 135.2 } },
      ],
      capability: { has_greeks: true, has_oi: true, cadence_sec: 39, oi_stale: true, oi_change_source: "derived_prev_session_close", oi_change_baseline_date: "2026-09-08", oi_change_coverage: 0.81 },
    },
    analytics: {
      pcr: { status: "ok", pcr_oi: 0.76, pcr_vol: 1.06 },
      max_pain: { status: "ok", max_pain_strike: 23550, distance_pct: 0.51 },
      iv_skew: { status: "ok", atm_iv: 0.104, rr_25: 0.0005, skew_slope: 0.7 },
      oi_walls: { status: "ok", ce_wall: 24000, pe_wall: 23000, nearest_resistance: 23700, nearest_support: 23400 },
      gex: { status: "ok", regime_sign: 0, total_shape: 3168, flip_strike: 23507, pin_strike: 23700 },
      strike_step: 50,
    },
    structure: {
      underlying: "NIFTY", summary: "max-pain 23550 (UP 0.51%, MODERATE); PCR 0.76 -> NEUTRAL",
      notes: ["5/5 structure blocks resolved", "<b>x</b>"],
      max_pain: { status: "ok", max_pain_strike: 23550, magnet_dir: "UP", magnet_pull: "MODERATE", distance_pct: 0.51 },
      pcr_regime: { status: "ok", pcr_oi: 0.76, regime: "NEUTRAL" },
      oi_walls: { status: "ok", nearest_resistance: 23700, nearest_support: 23400, boxed: false },
      iv_skew_bias: { status: "ok", bias: "NEUTRAL", rr_25: 0.0005 },
      iv_vs_realized: { status: "ok", state: "FAIR" },
      gex_regime: { status: "ok", regime: "GAMMA_BALANCED", flip_strike: 23507 },
    },
    quality: { dqs: 72.6, verdict: "WARN" },
    qualification: {
      verdict: "WATCH", direction: "LONG",
      structure_bias: { bias: "LONG", net: 1.0 }, regime_context: "NEUTRAL",
      ann_p_win: null, blocking: [], watch_reasons: ["G_DQ: data quality WARN", "G_DIRECTION: structure-only LONG"],
      gates: [], notes: ["research-only -- not wired to any order path"],
    },
    capability: { has_greeks: true, has_oi: true, cadence_sec: 39, oi_stale: true, oi_change_source: "derived_prev_session_close", oi_change_baseline_date: "2026-09-08", oi_change_coverage: 0.81 },
  }),
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
global.__urls = [];
global.fetch = async (url) => {
  global.__urls.push(String(url));
  return { ok: true, status: 200, json: async () => matchPayload(String(url)), text: async () => "" };
};
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
                      "loadMonitor", "loadMathScalp", "loadOrderflow", "loadOptionchain"]) {
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

  // math-scalper view rendered its tables + escaped hostile reason codes
  const msRank = elFor("#msRankTable tbody").innerHTML;
  assert.ok(/NIFTY/.test(msRank), "math-scalper ranking table should render, got: " + msRank.slice(0, 160));
  const msMap = elFor("#msMapTable tbody").innerHTML;
  assert.ok(/NIFTY/.test(msMap), "math-scalper market-map table should render");
  const msWhy = elFor("#msWhy").innerHTML;
  assert.ok(/&lt;img src=x/.test(msWhy) && !/<img\s/i.test(msWhy), "math-scalper reason codes must be entity-escaped: " + msWhy.slice(0, 160));
  // ---- Focus index combobox (custom; replaced the flaky native <datalist>) ----
  // 1. defaults to NIFTY, input reflects it
  assert.equal(chk.msSelectedFocus(), "NIFTY", "Focus defaults to NIFTY");
  assert.equal(elFor("#msFocus").value, "NIFTY", "Focus input shows the selected index");
  // 2/3/4. local case-insensitive partial-match filtering
  assert.deepEqual(chk.msFilterUniverse("bank").sort(), ["BANKEX", "BANKNIFTY"], "'bank' -> BANKNIFTY + BANKEX");
  assert.deepEqual(chk.msFilterUniverse("fin"), ["FINNIFTY"], "'fin' -> FINNIFTY");
  assert.deepEqual(chk.msFilterUniverse("  MiD "), ["MIDCPNIFTY"], "trimmed + case-insensitive -> MIDCPNIFTY");
  // NSE / BSE / MCX all represented (like Auto-Scalp)
  assert.equal(chk.msExchOf("SENSEX"), "BSE"); assert.equal(chk.msExchOf("NATURALGAS"), "MCX"); assert.equal(chk.msExchOf("NIFTY"), "NSE");
  assert.ok(chk.msFilterUniverse("nat").includes("NATURALGAS"), "MCX index searchable");
  assert.ok(chk.msFilterUniverse("gold").includes("GOLD"), "MCX universe merged from /api/autoscalp/universe");
  assert.ok(chk.msFilterUniverse("sense").includes("SENSEX"), "BSE index searchable");
  assert.ok(!chk.msFilterUniverse("reli").includes("RELIANCE"), "single stocks are NOT pulled into the index selector");
  // 5. selecting an index updates the one canonical state + refreshes the input + triggers a scan
  const nBefore = global.__urls.length;
  assert.equal(chk.msCommitFocus("BANKNIFTY"), true, "commit of a supported index succeeds");
  assert.equal(chk.msSelectedFocus(), "BANKNIFTY", "selecting BANKNIFTY changes selectedFocusIndex");
  assert.equal(elFor("#msFocus").value, "BANKNIFTY", "input reflects the new selection");
  await flush(); await flush(); await flush();
  assert.ok(global.__urls.slice(nBefore).some(u => /symbol=BANKNIFTY/.test(u)),
    "selection triggers a fresh scan for BANKNIFTY: " + global.__urls.slice(nBefore).join(", "));
  // 6. stale-response guard: an older request token is superseded by a newer selection
  const tok = chk.msReqToken();
  chk.msCommitFocus("NIFTY");
  assert.ok(chk.msIsStale(tok), "a captured request token is stale after a newer Focus change");
  await flush(); await flush();
  // 7. unsupported typed value is rejected, selection unchanged, message shown
  chk.msCommitFocus("NIFTY");                         // known baseline
  assert.equal(chk.msCommitFocus("NOTANINDEX"), false, "unsupported index rejected");
  assert.equal(chk.msSelectedFocus(), "NIFTY", "rejected input does not change the selection");
  assert.match(chk.msFocusMsg(), /supported index/i, "validation message for unsupported index");
  assert.equal(elFor("#msFocus").value, "NIFTY", "input reverts to the last valid selection");
  // 8. empty Focus does not call the ranking API
  const nEmpty = global.__urls.length;
  assert.equal(chk.msCommitFocus(""), false, "empty Focus rejected");
  assert.match(chk.msFocusMsg(), /select an index/i, "validation message for empty Focus");
  await flush();
  assert.equal(global.__urls.length, nEmpty, "empty Focus makes no API calls");
  // 9. Refresh preserves the selected Focus
  chk.msCommitFocus("BANKNIFTY"); await flush(); await flush();
  elFor("#msRefresh").click(); await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), "BANKNIFTY", "Refresh preserves Focus");
  // 10. Profile change preserves the selected Focus
  const pf = elFor("#msProfile"); pf.value = "AGGRESSIVE";
  (pf._handlers.change || []).forEach(fn => fn.call(pf, { target: pf }));
  await flush(); await flush();
  assert.equal(chk.msSelectedFocus(), "BANKNIFTY", "Profile change preserves Focus");
  // 11/12. universe is never shrunk; static seed works with the APIs unavailable
  assert.ok(chk.msUniverseList().length >= 8, "Focus universe only ever grows, got: " + chk.msUniverseList().length);
  assert.ok(chk.msFilterUniverse("nifty").length >= 1, "static seed universe is searchable regardless of API state");

  // ---- Order Flow view ----
  const ofVp = elFor("#ofVp").innerHTML;
  assert.ok(/of-row/.test(ofVp) && /24052\.5/.test(ofVp), "volume profile rows rendered: " + ofVp.slice(0, 200));
  assert.ok(/is-poc/.test(ofVp), "POC row is marked in the volume profile");
  const ofMp = elFor("#ofMp").innerHTML;
  assert.ok(/of-letters/.test(ofMp), "TPO letters rendered in the market profile");
  const ofSm = elFor("#ofSmTable tbody").innerHTML;
  assert.ok(/TARGET_HIT/.test(ofSm) && /of-oc/.test(ofSm), "smart-money signals table rendered with outcome: " + ofSm.slice(0, 200));
  const ofBtSum = elFor("#ofBtSummary").innerHTML;
  assert.ok(/winning points/i.test(ofBtSum) && /SL-hit points/i.test(ofBtSum), "backtest summary shows winning + SL-hit points: " + ofBtSum.slice(0, 200));
  assert.ok(/option premium/i.test(ofBtSum) && /thin/i.test(ofBtSum), "backtest summary shows premium basis + coverage: " + ofBtSum.slice(0, 200));
  assert.ok(/INSUFFICIENT/.test(elFor("#ofBtBadge").textContent + elFor("#ofBtBadge").innerHTML), "backtest badge flags an insufficient sample");
  const ofBt = elFor("#ofBtTable tbody").innerHTML;
  assert.ok(/WIN/.test(ofBt) && /LOSS/.test(ofBt), "backtest per-trade table renders wins and losses: " + ofBt.slice(0, 200));
  // ---- H1/H7 structural-state shadow panel ----
  const ofH1H7 = elFor("#ofH1H7Table tbody").innerHTML;
  assert.ok(/H7_TRAP/.test(ofH1H7) && /AVOID/.test(ofH1H7), "H1/H7 panel renders an H7_TRAP -> AVOID row: " + ofH1H7.slice(0, 200));
  assert.ok(/H1_CONT_OBSERVE/.test(ofH1H7) && /NOT_VALIDATED/.test(ofH1H7), "H1/H7 panel keeps NIFTY continuation as observe-only");
  assert.ok(/UNOBSERVABLE/.test(ofH1H7), "H1/H7 panel shows available_R as UNOBSERVABLE when absent (never fabricated)");
  const ofH1H7Meta = elFor("#ofH1H7Meta").textContent;
  assert.ok(/SHADOW_OBSERVATION/.test(ofH1H7Meta) && /live_trading false/.test(ofH1H7Meta), "H1/H7 panel meta states shadow mode + live_trading false: " + ofH1H7Meta);
  const ofH1H7Leg = elFor("#ofH1H7Legend").innerHTML;
  assert.ok(/SUPPORTED/.test(ofH1H7Leg) && /PROVEN/.test(ofH1H7Leg) && /Not used/.test(ofH1H7Leg), "H1/H7 legend renders research-confidence tiers incl. PROVEN=not used");
  // order-flow index picker: independent of Math Scalper's, covers NSE/BSE/MCX
  assert.equal(chk.ofSelected(), "NIFTY", "Order Flow index defaults to NIFTY");
  assert.deepEqual(chk.ofFilter("bank").sort(), ["BANKEX", "BANKNIFTY"], "OF picker: 'bank' -> BANKNIFTY + BANKEX");
  assert.ok(chk.ofFilter("nat").includes("NATURALGAS"), "OF picker: MCX index searchable");
  assert.ok(chk.ofFilter("sense").includes("SENSEX"), "OF picker: BSE index searchable");
  assert.equal(chk.ofCommitSymbol("NOTANINDEX"), false, "OF picker rejects unsupported symbol");
  assert.equal(chk.ofSelected(), "NIFTY", "OF picker selection unchanged after a bad commit");

  // ---- Option Chain view ----
  const ocTbl = elFor("#ocTable tbody").innerHTML;
  assert.ok(/23450/.test(ocTbl) && /is-atm/.test(ocTbl), "option-chain table renders strikes with the ATM row marked: " + ocTbl.slice(0, 200));
  assert.ok(/oc-oibar-ce/.test(ocTbl) && /oc-oibar-pe/.test(ocTbl), "OI-profile split bar rendered per strike");
  // change-in-OI (Δ) cells render numeric values with a pos/neg tint, not "—"
  assert.ok(/<span class="pos">1\.2L<\/span>/.test(ocTbl) && /<span class="neg">-30k<\/span>/.test(ocTbl),
    "option-chain Δ (change-in-OI) cells render signed values: " + ocTbl.slice(0, 300));
  const ocMeta = elFor("#ocMeta").textContent;
  assert.ok(/Δ vs 2026-09-08 close \(81% of strikes\)/.test(ocMeta),
    "meta line states the Δ baseline + coverage: " + ocMeta);
  const ocStrip = elFor("#ocStrip").innerHTML;
  assert.ok(/PCR \(OI\)/.test(ocStrip) && /Max Pain/.test(ocStrip) && /GEX/.test(ocStrip) && /DQ/.test(ocStrip),
    "option-chain header strip shows PCR / Max Pain / GEX / DQ: " + ocStrip.slice(0, 200));
  const ocExp = elFor("#ocExpiryBanner").innerHTML;
  assert.ok(/EXPIRY DAY/.test(ocExp) && /0 DTE/.test(ocExp), "expiry-day banner renders on 0 DTE: " + ocExp.slice(0, 160));
  assert.ok(/oc-nextexp/.test(ocExp) && /View next expiry/.test(ocExp), "expiry-day banner offers the next-expiry switch");
  assert.ok(elFor("#ocExpiryBanner").hidden === false, "expiry-day banner is shown, not hidden");
  const ocQual = elFor("#ocQual").innerHTML;
  assert.ok(/oc-verdict/.test(ocQual) && /WATCH/.test(ocQual), "qualification verdict pill rendered: " + ocQual.slice(0, 160));
  assert.ok(/not wired to any order path/.test(ocQual), "qualification carries the research-only note");
  const ocNote = elFor("#ocNote").textContent + elFor("#ocTable tbody").innerHTML + elFor("#ocNote").innerHTML;
  assert.ok(/&lt;img src=x/.test(elFor("#ocNote").textContent + ocQual + ocStrip + ocTbl) || !/<img\s|<b>x<\/b>/i.test(ocTbl + ocStrip + ocQual + elFor("#ocNote").innerHTML),
    "hostile markup from option-chain notes is not injected as live tags");
  assert.equal(chk.ocSelected(), "NIFTY", "Option Chain underlying defaults to NIFTY");
  assert.equal(chk.ocCommitSymbol("BANKNIFTY"), true, "Option Chain accepts a supported underlying");
  assert.equal(chk.ocSelected(), "BANKNIFTY", "Option Chain selection updates");
  assert.equal(chk.ocCommitSymbol(""), false, "Option Chain rejects an empty underlying");
  const ocSymOpts = elFor("#ocSymbol").innerHTML;
  assert.ok(/optgroup label="MCX"/.test(ocSymOpts) && /CRUDEOIL/.test(ocSymOpts) && /NATURALGAS/.test(ocSymOpts),
    "Option Chain underlying picker lists the MCX group: " + ocSymOpts.slice(0, 240));
  assert.ok(/optgroup label="BSE"/.test(ocSymOpts) && /SENSEX/.test(ocSymOpts),
    "Option Chain underlying picker lists the BSE group");
  assert.equal(chk.ocCommitSymbol("CRUDEOIL"), true, "Option Chain accepts an MCX underlying");

  console.log("render smoke: 12 view loaders + feed renderer OK, Focus combobox (12 checks) + Order Flow + Option Chain view OK, no runtime errors, output escaped");
  process.exit(0);
})().catch(e => { console.error("render smoke FAILED:", e && e.stack || e); process.exit(1); });
