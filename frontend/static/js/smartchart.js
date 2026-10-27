// Smart Chart UI: Vela workspace + PineTS (browser worker) + ZeroHero/Upstox data provider + script library, editor, backtest,
// paper trading and analysis panels. Paper only: nothing here can place an order.
(function () {
  "use strict";
  const C = window.SmartChartCore, V = window.SmartChartVendor;
  const { esc, fmtNum, fmtTime, cls } = C;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  const st = {
    ws: null, provider: null, scripts: [], templates: [], cur: null, dirty: false, handles: new Map(), health: null, meta: null,
    lastMeta: null, lastErr: null, tfSec: 300, symbols: [], diags: [], inputsSchema: [], analysis: null, levelsHandle: null,
    paper: { trades: null }, onChart: [], paperTimer: null,
  };

  // ---------------------------------------------------------------- API
  async function api(path, opts) {
    opts = opts || {};
    const method = opts.method || "GET";
    const headers = {};
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET") headers["X-SmartChart-CSRF"] = "1";
    const r = await fetch("/api/smart-chart" + path, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, credentials: "same-origin" });
    let j = null;
    try { j = await r.json(); } catch (_) { /* non-JSON error page */ }
    if (!r.ok) {
      const env = j && (j.error || (j.detail && j.detail.error));
      const e = new Error(r.status === 401 ? "Not signed in — reload the dashboard and log in again" : env ? env.message : "request failed (HTTP " + r.status + ")");
      e.status = r.status; e.code = env && env.code; e.details = env && env.details;
      throw e;
    }
    return j;
  }
  const showErr = (id, e) => { const el = $(id); if (el) el.textContent = e ? (e.message || String(e)) : ""; };

  // ---------------------------------------------------------------- data status pill
  function renderPill() {
    const s = C.statusFromMeta(st.lastMeta, Date.now(), st.tfSec, st.lastErr);
    const el = $("#pillData");
    el.className = "pill " + s.state;
    el.innerHTML = "<i></i><span>" + esc(s.label) + "</span>";
    el.title = s.detail;
  }
  function onMeta(meta, ctx) {
    st.lastMeta = meta; st.lastErr = null;
    if (ctx && ctx.tf) st.tfSec = C.TF_SEC[ctx.tf] || st.tfSec;
    renderPill();
  }

  // ---------------------------------------------------------------- live poller (REST; never described as a socket)
  class LivePoller {
    constructor(symbol, tf, onBar) { this.symbol = symbol; this.tf = tf; this.onBar = onBar; this.gate = new C.BarGate(); this.fail = 0; this.stopped = false; this.timer = null; }
    start() { this.schedule(1500); }
    stop() { this.stopped = true; clearTimeout(this.timer); }
    schedule(ms) { if (!this.stopped) this.timer = setTimeout(() => this.tick(), ms); }
    async tick() {
      if (this.stopped) return;
      if (document.hidden) return this.schedule(15000);
      try {
        const since = this.gate.last ? this.gate.last.time : 0;
        const j = await api("/live?" + new URLSearchParams({ symbol: this.symbol, tf: this.tf, since }));
        this.fail = 0;
        onMeta(j.meta, { tf: this.tf });
        for (const b of j.candles) { const k = this.gate.accept(b); if (k === "new" || k === "update") this.onBar(b); }
        this.schedule(j.meta.market_open ? (j.meta.live_ttl_sec || 3) * 1000 : 30000);
      } catch (e) {
        this.fail += 1;
        st.lastErr = e.message; renderPill();
        this.schedule(Math.min(30000, 3000 * 2 ** Math.min(this.fail, 4)));
      }
    }
  }

  // ---------------------------------------------------------------- Vela data provider (Upstox via ZeroHero)
  class UpstoxProvider {
    async listSymbols() {
      if (!st.symbols.length) st.symbols = (await api("/symbols/all")).symbols;
      return st.symbols;
    }
    async getSymbolInfo(ticker) {
      const d = (await this.listSymbols()).find((s) => s.ticker === ticker) || {};
      return { ticker, description: d.description || ticker, type: d.type || "index", exchange: d.prefix || "NSE", currency: "INR", timezone: "Asia/Kolkata",
        session: "0915-1530", mintick: 0.05, pricescale: 20, minmove: 1, pointvalue: 1, mincontract: 1 };   // same sizing rules as the server worker (whole units)
    }
    async getBars(ticker, timeframe, range) {
      const tf = C.tfToApi(timeframe);
      if (!tf) throw new Error("timeframe " + timeframe + " is not available from this data source (use 1m, 3m, 5m, 15m, 30m, 1h or 1D)");
      range = range || {};
      const q = new URLSearchParams({ symbol: C.symbolToApi(ticker), tf, limit: String(Math.min(range.limit || 1500, 10000)) });
      if (range.to && range.to < Date.now() - 2 * C.TF_SEC[tf] * 1000) q.set("before", String(range.to + 1));
      const j = await api("/candles?" + q);
      onMeta(j.meta, { tf });
      return range.from ? j.candles.filter((b) => b.time >= range.from) : j.candles;
    }
    subscribe(ticker, timeframe, onBar) {
      const tf = C.tfToApi(timeframe);
      if (!tf) return function () {};
      const p = new LivePoller(C.symbolToApi(ticker), tf, onBar);
      p.start();
      return function () { p.stop(); };
    }
  }

  // ---------------------------------------------------------------- chart
  function bootChart() {
    if (!V || !V.VelaWorkspace) { fatal("The chart library failed to load (smartchart-vendor.js)."); return; }
    try {
      st.provider = new UpstoxProvider();
      st.ws = new V.VelaWorkspace("#chart", {
        layout: false, symbol: "NSE:NIFTY", timeframe: "5", live: true, theme: matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark",
        providers: { upstox: () => st.provider }, engines: { pine: () => new V.PineWorkerEngine() }, defaultLanguage: "pine",
        timezone: "Asia/Kolkata", persist: "zerohero-smartchart",
      });
    } catch (e) { fatal("Chart failed to start: " + e.message); console.error(e); return; }
    setInterval(reconcileChart, 2000);
    restoreOnChart();
  }
  function fatal(msg) { const el = $("#chartErr"); el.hidden = false; el.textContent = msg; }
  const chart = () => (st.ws ? st.ws.chart : null);
  function market() { const c = chart(); const m = c && c.market; return m ? { symbol: String(m.symbol || "NIFTY").replace(/^[A-Za-z]+:/, ""), timeframe: m.timeframe } : { symbol: "NIFTY", timeframe: "5" }; }
  function ctxApi() { const m = market(); return { symbol: C.symbolToApi(m.symbol), tf: C.tfToApi(m.timeframe) || "5m" }; }

  function reconcileChart() {
    const c = chart();
    if (!c || !c.indicators) return;
    let ids;
    try { ids = c.indicators().map((h) => h.id).filter((i) => String(i).startsWith("zh-")); } catch (_) { return; }
    for (const [sid, h] of Array.from(st.handles)) if (!ids.includes(h.id)) st.handles.delete(sid);
    const onChart = Array.from(st.handles.keys());
    if (JSON.stringify(onChart) !== JSON.stringify(st.onChart)) { st.onChart = onChart; api("/prefs/onchart", { method: "PUT", body: { value: onChart } }).catch(() => {}); renderScripts(); }
    const m = C.tfToApi(market().timeframe);
    if (m && C.TF_SEC[m] !== st.tfSec) { st.tfSec = C.TF_SEC[m]; renderPill(); }
    $("#btCtx").textContent = "Chart: " + ctxApi().symbol + " · " + ctxApi().tf;
  }

  async function restoreOnChart() {
    try {
      const prefs = await api("/prefs");
      const want = Array.isArray(prefs.onchart) ? prefs.onchart : [];
      const c = chart();
      if (c && c.ready) await c.ready();
      // list items carry no source: always fetch the full script (a deleted one is simply skipped)
      for (const sid of want) { const s = await api("/scripts/" + sid).catch(() => null); if (s) addToChart(s, true); }
    } catch (e) { console.warn("restore failed", e); }
  }

  function addToChart(script, quiet) {
    const c = chart();
    if (!c) { showErr("#scErr", new Error("chart is not ready")); return null; }
    const old = st.handles.get(script.id);
    if (old) { try { old.remove(); } catch (_) { /* already gone */ } st.handles.delete(script.id); }
    try {
      const h = c.addIndicator(script.source, { id: "zh-" + script.id, title: script.name, inputs: script.inputs || {} });
      st.handles.set(script.id, h);
      h.on("error", (ev) => { if (st.cur && st.cur.id === script.id) { st.diags = [{ severity: "error", code: "CHART_RUNTIME", message: (ev.error && ev.error.message) || String(ev.error), line: null }]; renderDiag(); } });
      renderScripts();
      return h;
    } catch (e) { if (!quiet) showErr("#scErr", e); return null; }
  }
  function removeFromChart(id) { const h = st.handles.get(id); if (h) { try { h.remove(); } catch (_) { /* ignore */ } st.handles.delete(id); renderScripts(); } }

  // ---------------------------------------------------------------- tabs
  function selectTab(name) {
    $$("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $$(".panel").forEach((p) => p.classList.toggle("on", p.id === "p-" + name));
    if (name === "backtest") { fillScriptSelects(); loadBtHistory(); }
    if (name === "paper") { fillScriptSelects(); loadPaper(); }
    if (name === "ai") fillScriptSelects();
    if (name === "about") loadAbout();
  }

  // ---------------------------------------------------------------- scripts library
  async function loadScripts() {
    try {
      const q = new URLSearchParams({ limit: "200" });
      if ($("#scKind").value) q.set("kind", $("#scKind").value);
      if ($("#scQ").value.trim()) q.set("q", $("#scQ").value.trim());
      st.scripts = (await api("/scripts?" + q)).items; showErr("#scErr", null);
    } catch (e) { showErr("#scErr", e); }
    renderScripts(); fillScriptSelects();
  }
  function renderScripts() {
    const el = $("#scList");
    if (!st.scripts.length) { el.innerHTML = '<div class="empty">No saved scripts yet. Pick a template above and press <b>New</b>, or open the Editor and write your own Pine v5/v6 script.</div>'; return; }
    el.innerHTML = st.scripts.map((s) => {
      const on = st.handles.has(s.id);
      return '<div class="card" data-id="' + esc(s.id) + '"><div class="row" style="margin:0;align-items:center"><b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">' + (s.favorite ? "★ " : "") + esc(s.name) + "</b>" +
        '<span class="badge">' + esc(s.kind) + '</span><span class="badge ' + esc(s.status) + '">' + esc(s.status.toLowerCase()) + '</span><span class="badge">v' + esc(s.version) + "</span></div>" +
        (s.description ? '<div class="mut" style="font-size:12px;margin:2px 0">' + esc(s.description) + "</div>" : "") +
        '<div class="row" style="margin-bottom:0">' +
        (s.kind === "indicator"
          ? (on ? '<button class="sm" data-act="remove">Remove from chart</button>' : '<button class="sm pri" data-act="add">Add to chart</button>')
          : '<button class="sm pri" data-act="run">Run strategy</button>' + (on ? '<button class="sm" data-act="remove">Remove from chart</button>' : "")) +
        '<button class="sm" data-act="edit">Edit</button><button class="sm" data-act="fav">' + (s.favorite ? "Unpin" : "Pin") + '</button><button class="sm" data-act="del">Delete</button></div></div>';
    }).join("");
  }
  async function scriptAction(id, act, btn) {
    const s = st.scripts.find((x) => x.id === id);
    if (!s) return;
    try {
      if (act === "edit") { await openEditor(id); selectTab("editor"); }
      else if (act === "add") { const full = await api("/scripts/" + id); addToChart(full); }
      else if (act === "remove") removeFromChart(id);
      else if (act === "run") { const full = await api("/scripts/" + id); addToChart(full); selectTab("backtest"); $("#btScript").value = id; runBacktest(); }
      else if (act === "fav") { await api("/scripts/" + id, { method: "PUT", body: { favorite: !s.favorite } }); await loadScripts(); }
      else if (act === "del") {
        if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm delete"; setTimeout(() => { if (btn.isConnected) { btn.dataset.armed = ""; btn.textContent = "Delete"; } }, 3500); return; }
        removeFromChart(id);
        await api("/scripts/" + id, { method: "DELETE" });
        if (st.cur && st.cur.id === id) { st.cur = null; loadEditor(newBlank()); }
        await loadScripts();
      }
    } catch (e) { showErr("#scErr", e); }
  }

  // ---------------------------------------------------------------- editor
  const newBlank = () => ({ id: null, name: "Untitled", description: "", kind: "indicator", version: 0, status: "UNCOMPILED", inputs: {}, source: '//@version=5\nindicator("My indicator", overlay=true)\nplot(ta.sma(close, 20), "SMA 20")\n' });

  function loadEditor(s) {
    st.cur = s; st.dirty = false; st.diags = []; st.inputsSchema = [];
    $("#edName").value = s.name; $("#edDesc").value = s.description || ""; $("#edKind").value = s.kind; $("#edTa").value = s.source;
    renderEditorMirror(); renderDiag(); renderInputs(); renderEdMeta();
    $("#edFav").textContent = s.favorite ? "★" : "☆";
    fillVersions(s);
  }
  async function openEditor(id) {
    const s = await api("/scripts/" + id);
    loadEditor(s);
    if (s.last_compile && s.last_compile.version === s.version) { st.diags = s.last_compile.diagnostics; st.inputsSchema = s.last_compile.inputs; renderDiag(); renderInputs(); }
  }
  function renderEdMeta() {
    const s = st.cur;
    $("#edMeta").textContent = !s || !s.id ? "unsaved new script" : "v" + s.version + " · " + s.status.toLowerCase() + (st.dirty ? " · unsaved changes" : "");
  }
  function renderEditorMirror() {
    const ta = $("#edTa"), src = ta.value;
    $("#edPre").innerHTML = C.highlight(src) + "\n";
    const errLines = new Set(st.diags.filter((d) => d.severity === "error" && d.line).map((d) => d.line));
    const n = src.split("\n").length;
    let g = "";
    for (let i = 1; i <= n; i++) g += (errLines.has(i) ? '<span class="ln-err">' + i + "</span>" : i) + "\n";
    $("#edGut").innerHTML = g;
    syncScroll();
  }
  function syncScroll() { const ta = $("#edTa"); $("#edPre").scrollTop = ta.scrollTop; $("#edPre").scrollLeft = ta.scrollLeft; $("#edGut").scrollTop = ta.scrollTop; }
  function renderDiag() {
    const el = $("#edDiag");
    if (!st.diags.length) { el.innerHTML = ""; return; }
    el.innerHTML = st.diags.map((d, i) => '<div class="diag ' + esc(d.severity) + '" data-i="' + i + '"><b>' + esc(d.severity) + "</b> " + esc(d.code || "") + (d.line ? " · line " + esc(d.line) + (d.column ? ":" + esc(d.column) : "") : "") + "<br>" + esc(d.message) + "</div>").join("");
    renderEditorMirror();
  }
  function gotoLine(line, col) {
    const ta = $("#edTa"), lines = ta.value.split("\n");
    while (line > 1 && !(lines[line - 1] || "").trim()) { line -= 1; col = 1; }      // EOF errors point past the last code line
    let pos = 0; for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
    ta.focus(); ta.setSelectionRange(pos + Math.max(0, (col || 1) - 1), pos + (lines[line - 1] || "").length);
    ta.scrollTop = Math.max(0, (line - 4) * 19); syncScroll();
  }
  function renderInputs() {
    const card = $("#edInputsCard");
    if (!st.inputsSchema.length) { card.hidden = true; return; }
    card.hidden = false;
    const saved = (st.cur && st.cur.inputs) || {};
    $("#edInputs").innerHTML = '<div class="grid2">' + st.inputsSchema.map((i) => {
      const v = saved[i.key] !== undefined ? saved[i.key] : i.defval;
      const id = 'data-key="' + esc(i.key) + '" data-type="' + esc(i.type) + '"';
      let ctl;
      if (i.options && i.options.length) ctl = "<select " + id + ">" + i.options.map((o) => '<option value="' + esc(o) + '"' + (String(o) === String(v) ? " selected" : "") + ">" + esc(o) + "</option>").join("") + "</select>";
      else if (i.type === "bool") ctl = "<input " + id + ' type="checkbox"' + (v ? " checked" : "") + ">";
      else if (i.type === "int" || i.type === "float") ctl = "<input " + id + ' type="number" value="' + esc(v) + '"' + (i.minval != null ? ' min="' + esc(i.minval) + '"' : "") + (i.maxval != null ? ' max="' + esc(i.maxval) + '"' : "") + (i.step != null ? ' step="' + esc(i.step) + '"' : i.type === "float" ? ' step="any"' : "") + ">";
      else ctl = "<input " + id + ' value="' + esc(v) + '">';
      return '<label class="f" title="' + esc(i.tooltip || "") + '">' + esc(i.name) + ctl + "</label>";
    }).join("") + "</div>";
  }
  function readInputs() {
    const out = {};
    $$("#edInputs [data-key]").forEach((el) => {
      const t = el.dataset.type; let v;
      if (el.type === "checkbox") v = el.checked;
      else if (t === "int") v = parseInt(el.value, 10);
      else if (t === "float") v = parseFloat(el.value);
      else v = el.value;
      if (typeof v === "number" && !Number.isFinite(v)) return;
      out[el.dataset.key] = v;
    });
    return out;
  }
  function fillVersions(s) {
    const sel = $("#edVer");
    sel.innerHTML = "";
    if (!s.id) return;
    api("/scripts/" + s.id + "/versions").then((j) => { sel.innerHTML = j.versions.map((v) => '<option value="' + esc(v.version) + '">v' + esc(v.version) + " · " + esc(fmtTime(v.created_ts * 1000)) + "</option>").join(""); }).catch(() => {});
  }
  function readForm() { return { name: $("#edName").value.trim() || "Untitled", description: $("#edDesc").value, kind: $("#edKind").value, source: $("#edTa").value }; }

  async function saveScript(asNew) {
    const f = readForm();
    try {
      let s;
      if (!asNew && st.cur && st.cur.id) s = await api("/scripts/" + st.cur.id, { method: "PUT", body: { ...f, inputs: st.cur.inputs || {} } });
      else {
        if (asNew && st.cur && st.cur.id && f.name === st.cur.name) f.name += " (copy)";
        s = await api("/scripts", { method: "POST", body: { ...f, inputs: asNew ? {} : (st.cur && st.cur.inputs) || {} } });
      }
      s.last_compile = null; st.cur = s; st.dirty = false; $("#edName").value = s.name;
      renderEdMeta(); fillVersions(s); await loadScripts();
      await compile(true);
      const h = st.handles.get(s.id);
      if (h && h.updateCode) h.updateCode(s.source);
      return s;
    } catch (e) { st.diags = [{ severity: "error", code: e.code || "ERROR", message: e.message, line: null }]; renderDiag(); return null; }
  }
  async function compile(quiet) {
    const src = $("#edTa").value;
    try {
      let res;
      if (st.cur && st.cur.id && !st.dirty) res = await api("/scripts/" + st.cur.id + "/compile", { method: "POST" });
      else res = await api("/compile", { method: "POST", body: { source: src } });
      st.diags = res.diagnostics; st.inputsSchema = res.inputs || [];
      if (st.cur && st.cur.id && !st.dirty) { st.cur.status = res.ok ? "OK" : "ERROR"; renderEdMeta(); loadScripts(); }
      renderDiag(); renderInputs();
      if (!quiet && !st.diags.length) { st.diags = [{ severity: "info", code: "OK", message: "Compiled: " + (res.kind || "script") + (res.title ? ' "' + res.title + '"' : "") + " · Pine v" + (res.pine_version || "?"), line: null }]; renderDiag(); }
      return res;
    } catch (e) { st.diags = [{ severity: "error", code: e.code || "ERROR", message: e.message, line: null }]; renderDiag(); return null; }
  }
  async function addCurrentToChart() {
    let s = st.cur;
    if (!s || !s.id || st.dirty) { s = await saveScript(false); if (!s) return; }
    addToChart(s);
  }
  async function applyInputs() {
    if (!st.cur || !st.cur.id) { st.diags = [{ severity: "warning", code: "SAVE_FIRST", message: "Save the script first, then apply inputs.", line: null }]; renderDiag(); return; }
    const vals = readInputs();
    try {
      const s = await api("/scripts/" + st.cur.id, { method: "PUT", body: { inputs: vals } });
      st.cur = { ...st.cur, inputs: s.inputs };
      const h = st.handles.get(s.id);
      if (h && h.setInputs) h.setInputs(vals); else addToChart({ ...s, source: $("#edTa").value });
    } catch (e) { st.diags = [{ severity: "error", code: e.code || "ERROR", message: e.message, line: null }]; renderDiag(); }
  }

  // ---------------------------------------------------------------- selects shared by backtest / paper / ai
  function fillScriptSelects() {
    const strat = st.scripts.filter((s) => s.kind === "strategy"), all = st.scripts;
    const opts = (rows, none) => (none ? '<option value="">' + none + "</option>" : "") + rows.map((s) => '<option value="' + esc(s.id) + '">' + esc(s.name) + " (v" + esc(s.version) + ")</option>").join("");
    const keep = (sel, html) => { const old = sel.value; sel.innerHTML = html; if (old && Array.from(sel.options).some((o) => o.value === old)) sel.value = old; };
    keep($("#btScript"), strat.length ? opts(strat) : '<option value="">(save a strategy first)</option>');
    keep($("#ppScript"), strat.length ? opts(strat) : '<option value="">(save a strategy first)</option>');
    keep($("#aiScript"), opts(all, "(none — trend/levels only)"));
  }

  // ---------------------------------------------------------------- backtest
  async function runBacktest() {
    const id = $("#btScript").value;
    if (!id) { showErr("#btErr", new Error("Save a strategy script first (Scripts → New → pick a strategy template).")); return; }
    const c = ctxApi();
    showErr("#btErr", null); $("#btRun").disabled = true; $("#btOut").innerHTML = '<div class="empty">Running on ' + esc(c.symbol) + " " + esc(c.tf) + " … (compiles, simulates, and re-runs on cut history to test for look-ahead)</div>";
    try {
      const j = await api("/backtests", { method: "POST", body: { script_id: id, symbol: c.symbol, tf: c.tf, bars: +$("#btBars").value || 3000, initial_capital: +$("#btCap").value, commission_pct: +$("#btComm").value, slippage_ticks: +$("#btSlip").value, causality_check: $("#btCaus").value === "1" } });
      renderBacktest(j); loadBtHistory();
    } catch (e) {
      $("#btOut").innerHTML = "";
      showErr("#btErr", e);
      if (e.details && Array.isArray(e.details)) $("#btOut").innerHTML = e.details.map((d) => '<div class="diag error">' + esc(d.message) + (d.line ? " (line " + esc(d.line) + ")" : "") + "</div>").join("");
    } finally { $("#btRun").disabled = false; }
  }
  function renderBacktest(b) {
    const s = b.stats, stat = (k, v, c) => '<div class="stat"><small>' + esc(k) + "</small><b class=\"" + (c || "") + '">' + v + "</b></div>";
    const eq = (b.equity || []).map((p) => p.equity);
    const caus = b.causality;
    const trades = (b.trades || []).slice().sort((a, z) => z.entry_time - a.entry_time).slice(0, 100);
    $("#btOut").innerHTML =
      '<div class="card"><h3>' + esc(b.symbol || "") + " " + esc(b.tf || "") + " · " + esc(b.range ? fmtTime(b.range.from_ms) + " → " + fmtTime(b.range.to_ms) : "") + " · " + esc(s.trades) + " trades</h3>" +
      '<div class="grid3">' + stat("Net profit", fmtNum(s.net_profit), cls(s.net_profit)) + stat("Return", fmtNum(s.return_pct) + "%", cls(s.return_pct)) + stat("Max drawdown", fmtNum(s.max_drawdown_pct) + "%") +
      stat("Win rate", s.win_rate_pct == null ? "—" : fmtNum(s.win_rate_pct, 1) + "%") + stat("Profit factor", s.profit_factor == null ? "—" : fmtNum(s.profit_factor)) + stat("Avg trade", fmtNum(s.avg_trade), cls(s.avg_trade)) +
      stat("Buy & hold", fmtNum(s.buy_hold_pct) + "%", cls(s.buy_hold_pct)) + stat("Exposure", fmtNum(s.exposure_pct, 1) + "%") + stat("Open P&L", fmtNum(s.open_pnl), cls(s.open_pnl)) + "</div>" +
      '<svg class="eq" viewBox="0 0 400 90" preserveAspectRatio="none" role="img" aria-label="Equity curve"><path d="' + C.sparkPath(eq, 400, 90) + '"/></svg>' +
      (s.warnings && s.warnings.length ? '<ul class="plain warn">' + s.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>" : "") +
      (caus ? '<div style="margin-top:6px"><span class="badge ' + (caus.ok ? "OK" : "ERROR") + '">look-ahead check: ' + (caus.ok ? "passed" : "FAILED") + "</span> <span class=\"mut\" style=\"font-size:11px\">" + esc(caus.cuts_tested) + " cuts · " + esc(caus.note) + "</span></div>" : "") +
      (s.reconciliation ? '<div class="mut" style="font-size:11px;margin-top:4px">net profit reconciles with PineTS: ' + (s.reconciliation.ok ? "yes" : "NO (diff " + esc(s.reconciliation.difference) + ")") + "</div>" : "") +
      (b.data_meta ? '<div class="mut" style="font-size:11px;margin-top:4px">data: ' + esc(b.data_meta.source) + " " + esc(b.data_meta.native_interval) + (b.data_meta.aggregated ? " aggregated" : "") + (b.data_meta.volume_available === false ? " · no volume (index)" : "") + "</div>" : "") + "</div>" +
      (b.assumptions ? '<details class="card"><summary>Assumptions &amp; limits (read before trusting this)</summary><ul class="plain">' + b.assumptions.map((a) => "<li>" + esc(a) + "</li>").join("") + "</ul></details>" : "") +
      '<div class="card"><h3>Trades (latest ' + trades.length + ")</h3><div class=\"tw\"><table><thead><tr><th>Entry</th><th>Dir</th><th>Qty</th><th>In</th><th>Out</th><th>P&amp;L</th></tr></thead><tbody>" +
      (trades.map((t) => "<tr><td>" + esc(fmtTime(t.entry_time)) + "</td><td>" + esc(t.direction) + "</td><td>" + esc(Math.abs(t.size)) + "</td><td>" + fmtNum(t.entry_price) + "</td><td>" + (t.exit_price == null ? "open" : fmtNum(t.exit_price)) + "</td><td class=\"" + cls(t.profit) + '">' + (t.profit == null ? "—" : fmtNum(t.profit)) + "</td></tr>").join("") || '<tr><td colspan="6" class="empty">No trades in this window.</td></tr>') + "</tbody></table></div></div>";
  }
  async function loadBtHistory() {
    try {
      const j = await api("/backtests?limit=15");
      $("#btHist").innerHTML = j.backtests.length ? '<div class="tw"><table><tbody>' + j.backtests.map((b) => '<tr data-id="' + esc(b.id) + '" style="cursor:pointer"><td>' + esc(fmtTime(b.created_ts * 1000)) + "</td><td>" + esc(b.symbol) + " " + esc(b.tf) + "</td><td>" + esc(b.stats.trades) + ' tr</td><td class="' + cls(b.stats.net_profit) + '">' + fmtNum(b.stats.net_profit) + "</td></tr>").join("") + "</tbody></table></div>" : '<div class="empty">No backtests yet.</div>';
    } catch (e) { $("#btHist").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; }
  }

  // ---------------------------------------------------------------- paper trading
  async function loadPaper() {
    try {
      const j = await api("/paper");
      const el = $("#ppList");
      el.innerHTML = j.portfolios.length ? j.portfolios.map((p) => {
        const s = p.state || {};
        return '<div class="card" data-id="' + esc(p.id) + '"><div class="row" style="margin:0;align-items:center"><b style="flex:1;min-width:0">' + esc(p.name) + '</b><span class="badge ' + esc(p.status) + '">' + esc(p.status) + "</span></div>" +
          '<div class="kv"><span>Equity</span><span>' + fmtNum(s.equity != null ? s.equity : p.capital) + '</span><span>Realized</span><span class="' + cls(s.realized) + '">' + fmtNum(s.realized || 0) + '</span><span>Open P&amp;L</span><span class="' + cls(s.open_pnl) + '">' + fmtNum(s.open_pnl || 0) + '</span><span>Day P&amp;L</span><span class="' + cls(s.day_pnl) + '">' + fmtNum(s.day_pnl || 0) + "</span><span>Trades</span><span>" + esc(s.trades || 0) + "</span><span>Last bar</span><span>" + esc(fmtTime(p.last_bar_ms)) + "</span></div>" +
          (p.halted_reason ? '<div class="err">' + esc(p.halted_reason) + "</div>" : "") +
          ((s.skipped || []).length ? '<div class="mut" style="font-size:11px">' + esc(s.skipped.length) + " entries skipped by the daily loss limit</div>" : "") +
          '<div class="row" style="margin-bottom:0">' + (p.status === "RUNNING" ? '<button class="sm" data-act="stop">Stop</button>' : p.status === "HALTED" ? "" : '<button class="sm pri" data-act="start">Start</button>') +
          '<button class="sm" data-act="step">Step now</button><button class="sm" data-act="trades">Trades</button><button class="sm" data-act="del">Delete</button></div></div>';
      }).join("") : '<div class="empty">No paper portfolios. Create one from a saved strategy above. It trades forward from the moment it is created.</div>';
      const running = j.portfolios.some((p) => p.status === "RUNNING");
      if (running && !st.paperTimer) st.paperTimer = setInterval(() => { if (!document.hidden) stepRunning(); }, 30000);
      if (!running && st.paperTimer) { clearInterval(st.paperTimer); st.paperTimer = null; }
    } catch (e) { $("#ppList").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; }
  }
  async function stepRunning() {
    try { const j = await api("/paper"); for (const p of j.portfolios) if (p.status === "RUNNING") await api("/paper/" + p.id + "/step", { method: "POST" }).catch(() => {}); loadPaper(); } catch (_) { /* next tick */ }
  }
  async function paperAction(id, act, btn) {
    try {
      if (act === "start") await api("/paper/" + id + "/start", { method: "POST" });
      else if (act === "stop") await api("/paper/" + id + "/stop", { method: "POST" });
      else if (act === "step") await api("/paper/" + id + "/step", { method: "POST" });
      else if (act === "del") {
        if (btn.dataset.armed !== "1") { btn.dataset.armed = "1"; btn.textContent = "Confirm delete"; setTimeout(() => { if (btn.isConnected) { btn.dataset.armed = ""; btn.textContent = "Delete"; } }, 3500); return; }
        await api("/paper/" + id, { method: "DELETE" }); $("#ppTradesCard").hidden = true;
      } else if (act === "trades") {
        const j = await api("/paper/" + id + "/trades?limit=200");
        $("#ppTradesCard").hidden = false; $("#ppTradesFor").textContent = "";
        $("#ppTrades").innerHTML = "<table><thead><tr><th>Entry</th><th>Dir</th><th>Qty</th><th>In</th><th>Out</th><th>P&amp;L</th><th></th></tr></thead><tbody>" +
          (j.trades.map((t) => "<tr><td>" + esc(fmtTime(t.entry_ms)) + "</td><td>" + esc(t.direction) + "</td><td>" + esc(t.qty) + "</td><td>" + fmtNum(t.entry_price) + "</td><td>" + (t.exit_price == null ? "—" : fmtNum(t.exit_price)) + '</td><td class="' + cls(t.pnl) + '">' + (t.pnl == null ? "—" : fmtNum(t.pnl)) + "</td><td>" + esc(t.status.toLowerCase()) + "</td></tr>").join("") || '<tr><td colspan="7" class="empty">No simulated trades yet.</td></tr>') + "</tbody></table>";
      }
      showErr("#ppErr", null);
    } catch (e) { showErr("#ppErr", e); }
    loadPaper();
  }
  async function createPaper() {
    const id = $("#ppScript").value;
    if (!id) { showErr("#ppErr", new Error("Save a strategy script first.")); return; }
    const c = ctxApi();
    try {
      await api("/paper", { method: "POST", body: { script_id: id, symbol: c.symbol, tf: c.tf, capital: +$("#ppCap").value, commission_pct: +$("#ppComm").value, slippage_ticks: +$("#ppSlip").value, daily_loss_limit: +$("#ppLoss").value } });
      showErr("#ppErr", null); loadPaper();
    } catch (e) { showErr("#ppErr", e); }
  }

  // ---------------------------------------------------------------- AI / analysis
  async function runAnalysis() {
    const c = ctxApi();
    showErr("#aiErr", null); $("#aiRun").disabled = true; $("#aiOut").innerHTML = '<div class="empty">Analysing ' + esc(c.symbol) + " " + esc(c.tf) + "…</div>";
    try {
      const j = await api("/analysis", { method: "POST", body: { symbol: c.symbol, tf: c.tf, script_id: $("#aiScript").value || null, ai: $("#aiOn").value === "1" } });
      st.analysis = j; renderAnalysis(j); $("#aiLevels").disabled = !(j.levels || j.reference);
    } catch (e) { $("#aiOut").innerHTML = ""; showErr("#aiErr", e); }
    $("#aiRun").disabled = false;
  }
  function renderAnalysis(a) {
    if (a.error) { $("#aiOut").innerHTML = '<div class="err">' + esc(a.error) + "</div>"; return; }
    const kv = (rows) => '<div class="kv">' + rows.map((r) => "<span>" + esc(r[0]) + "</span><span>" + r[1] + "</span>").join("") + "</div>";
    const ref = a.reference, ai = a.ai || {};
    $("#aiOut").innerHTML =
      '<div class="card"><h3>' + esc(a.symbol) + " " + esc(a.tf) + " · as of " + esc(fmtTime(a.as_of_ms)) + " · " + esc(a.bars_used) + " closed bars</h3>" +
      kv([["Source", esc((a.data || {}).source) + " " + esc((a.data || {}).native_interval) + ((a.data || {}).aggregated ? " (aggregated)" : "") + " · " + esc((a.data || {}).transport)], ["Trend", "<b>" + esc((a.trend || {}).direction) + "</b> — " + esc((a.trend || {}).basis || "")], ["Structure", esc((a.structure || {}).state)], ["Breakout", esc((a.breakout || {}).state) + " (20-bar range " + fmtNum((a.breakout || {}).range_low_20) + " – " + fmtNum((a.breakout || {}).range_high_20) + ")"], ["ATR(14)", fmtNum((a.volatility || {}).atr14) + " (" + fmtNum((a.volatility || {}).atr_pct, 3) + "%)"], ["Support", esc(((a.levels || {}).support || []).map((x) => fmtNum(x)).join(" · ") || "—")], ["Resistance", esc(((a.levels || {}).resistance || []).map((x) => fmtNum(x)).join(" · ") || "—")]]) + "</div>" +
      (ref ? '<div class="card"><h3>Reference levels (' + esc(ref.bias) + ' bias) — arithmetic, not advice</h3>' + kv([["Entry ref", fmtNum(ref.entry_ref)], ["Stop ref", fmtNum(ref.stop_ref)], ["T1 / T2 / T3", fmtNum(ref.t1) + " / " + fmtNum(ref.t2) + " / " + fmtNum(ref.t3)], ["Risk/R:R", fmtNum(ref.risk_per_unit) + " per unit · 1R / 2R / 3R"]]) + '<div class="mut" style="font-size:11px;margin-top:4px">' + esc(ref.basis) + "</div></div>" : '<div class="card"><h3>Reference levels</h3><div class="empty">No directional bias (trend is not clearly up or down) — no entry/stop/target is produced.</div></div>') +
      ((a.signals || []).length ? '<div class="card"><h3>Script signals (conditions on closed bars, not orders)</h3><ul class="plain">' + a.signals.map((s) => "<li>" + esc(s.plot) + " · " + esc(s.text || "") + " · " + esc(fmtTime(s.time)) + " (" + esc(s.bars_ago) + " bars ago)</li>").join("") + "</ul></div>" : "") +
      (a.validation ? '<div class="card"><h3>Historical validation</h3>' + kv([["Backtest trades", esc(a.validation.trades)], ["Win rate", a.validation.win_rate_pct == null ? "—" : fmtNum(a.validation.win_rate_pct, 1) + "%"], ["Profit factor", a.validation.profit_factor == null ? "—" : fmtNum(a.validation.profit_factor)], ["Net profit", fmtNum(a.validation.net_profit)]]) + '<div class="mut" style="font-size:11px">' + esc(a.validation.note) + "</div></div>" : "") +
      (ai.status === "OK" && ai.narrative ? '<div class="card"><h3>AI commentary</h3><p style="margin:2px 0">' + esc(ai.narrative.summary) + '</p><p class="mut" style="margin:2px 0">' + esc(ai.narrative.signal_explanation) + "</p>" + ((ai.narrative.risks || []).length ? '<ul class="plain">' + ai.narrative.risks.map((r) => "<li>" + esc(r) + "</li>").join("") + "</ul>" : "") + '<div class="mut" style="font-size:11px">Model confidence ' + esc(ai.model_confidence) + "/100 — " + esc(ai.confidence_kind) + " · " + esc(ai.model || "") + (ai.cached ? " · cached" : "") + "</div></div>" :
        ai.status && ai.status !== "NOT_REQUESTED" ? '<div class="card"><h3>AI commentary</h3><div class="empty">Unavailable (' + esc(ai.status) + (ai.reason ? ": " + esc(ai.reason) : "") + "). Everything above is deterministic and unaffected.</div></div>" : "") +
      ((a.warnings || []).length ? '<ul class="plain warn">' + a.warnings.map((w) => "<li>" + esc(w) + "</li>").join("") + "</ul>" : "") +
      '<details class="card"><summary>Not provided by this data source (never invented)</summary><ul class="plain">' + (a.unavailable || []).map((u) => "<li><b>" + esc(u.item) + "</b> — " + esc(u.reason) + "</li>").join("") + "</ul></details>";
  }
  function plotLevels() {
    const a = st.analysis, c = chart();
    if (!a || !c) return;
    if (st.levelsHandle) { try { st.levelsHandle.remove(); } catch (_) { /* gone */ } st.levelsHandle = null; }
    const lines = [];
    const add = (arr, name, col) => (arr || []).slice(0, 3).forEach((p, i) => lines.push('hline(' + Number(p) + ', "' + name + (i + 1) + '", color=color.' + col + ")"));
    add((a.levels || {}).support, "S", "green"); add((a.levels || {}).resistance, "R", "red");
    if (a.reference) { lines.push("hline(" + Number(a.reference.entry_ref) + ', "Entry", color=color.blue)'); lines.push("hline(" + Number(a.reference.stop_ref) + ', "Stop", color=color.orange)'); ["t1", "t2", "t3"].forEach((k) => lines.push("hline(" + Number(a.reference[k]) + ', "' + k.toUpperCase() + '", color=color.aqua)')); }
    if (!lines.length) return;
    try { st.levelsHandle = c.addIndicator('//@version=5\nindicator("Analysis levels", overlay=true)\n' + lines.join("\n") + "\n", { id: "zh-levels", title: "Analysis levels" }); } catch (e) { showErr("#aiErr", e); }
  }

  // ---------------------------------------------------------------- runtime / capabilities / health
  async function loadHealth() {
    try {
      st.health = await api("/health");
      const el = $("#pillRuntime"), ok = st.health.runtime && st.health.runtime.ok;
      el.className = "pill " + (ok ? "OK" : "DOWN");
      el.innerHTML = "<i></i><span>Pine runtime: " + (ok ? "ready" : "unavailable") + "</span>";
      el.title = ok ? "Node " + (st.health.runtime.node_version || "") + " · PineTS installed" : "Compile/backtest need Node + pinets on the server";
      if (!st.health.upstox.ok) { st.lastErr = "Upstox: " + (st.health.upstox.note || "not reachable"); renderPill(); }
    } catch (e) { const el = $("#pillRuntime"); el.className = "pill DOWN"; el.innerHTML = "<i></i><span>Pine runtime: " + esc(e.message) + "</span>"; }
  }
  async function loadAbout() {
    try {
      const [m, h, reg] = await Promise.all([api("/meta"), api("/health"), api("/capabilities")]);
      const t = reg.tiers || {};
      $("#abOut").innerHTML =
        '<div class="card"><h3>Runtime</h3><div class="kv"><span>Pine runtime</span><span>PineTS ' + esc(reg.pinets_version) + " (Node " + esc((h.runtime || {}).node_version || "?") + ") — isolated process per run</span><span>Data source</span><span>Upstox · " + (h.upstox.ok ? "authenticated" : "<b class=\"neg\">" + esc(h.upstox.note || "not OK") + "</b>") + " · live = REST poll</span><span>Execution</span><span><b>" + esc(h.execution.mode) + "</b> — no broker/order path</span><span>Limits</span><span>script ≤ " + esc(h.limits.max_script_bytes) + " B · ≤ " + esc(h.limits.max_candles) + " bars · " + esc(h.limits.exec_timeout_sec) + " s timeout · " + esc(h.limits.runs_per_minute) + " runs/min</span><span>Cache</span><span>" + esc(h.cache.entries) + " series · " + esc(h.cache.bars) + " bars</span></div></div>" +
        '<div class="card"><h3>Pine compatibility (honest scope)</h3><p class="mut" style="margin:0 0 6px;font-size:12px">' + esc(reg.notice) + " Pine v5 and v6 only.</p><div class=\"grid2\">" +
        ["supported", "partial", "not_tested", "unsupported"].map((k) => '<div class="stat"><small>' + esc(k.replace("_", " ")) + "</small><b>" + esc(t[k] || 0) + "</b></div>").join("") + '</div><div class="mut" style="font-size:11px;margin-top:6px"><b>supported</b> = verified by our own tests against an independent reference (' + esc(Object.keys(reg.verified).length) + " functions). <b>partial</b> = runs with a documented limit. <b>not tested</b> = listed by PineTS, not verified by us. <b>unsupported</b> = not listed.</div>" +
        '<details><summary>Verified functions</summary><ul class="plain">' + Object.entries(reg.verified).map(([k, v]) => "<li><b>" + esc(k) + "</b> — " + esc(v) + "</li>").join("") + '</ul></details><details><summary>Partial</summary><ul class="plain">' + Object.entries(reg.partial).map(([k, v]) => "<li><b>" + esc(k) + "</b> — " + esc(v) + "</li>").join("") + "</ul></details></div>" +
        '<div class="card"><h3>Not supported</h3><ul class="plain">' + m.not_supported.map((x) => "<li>" + esc(x) + "</li>").join("") + "</ul></div>" +
        '<div class="card"><h3>Licences</h3><ul class="plain">' + m.license.components.map((c) => "<li><b>" + esc(c.name) + "</b> " + esc(c.version) + " — " + esc(c.license) + (c.note ? " (" + esc(c.note) + ")" : "") + "</li>").join("") + "</ul><div class=\"mut\" style=\"font-size:11px\">" + esc(m.license.agpl_obligation) + "<br>" + esc(m.license.disclaimer) + "</div></div>";
    } catch (e) { $("#abOut").innerHTML = '<div class="err">' + esc(e.message) + "</div>"; }
  }

  // ---------------------------------------------------------------- wiring
  function wire() {
    $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) selectTab(b.dataset.tab); });
    $("#scList").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); const card = e.target.closest("[data-id]"); if (b && card) scriptAction(card.dataset.id, b.dataset.act, b); });
    $("#scKind").addEventListener("change", loadScripts);
    let qt; $("#scQ").addEventListener("input", () => { clearTimeout(qt); qt = setTimeout(loadScripts, 300); });
    $("#scNew").addEventListener("click", () => {
      const t = st.templates.find((x) => x.id === $("#scTpl").value);
      loadEditor(t ? { ...newBlank(), name: t.name, description: t.description, kind: t.kind, source: t.source } : newBlank());
      st.dirty = true; renderEdMeta(); selectTab("editor");
    });
    const ta = $("#edTa");
    ta.addEventListener("input", () => { st.dirty = true; renderEdMeta(); renderEditorMirror(); });
    ta.addEventListener("scroll", syncScroll);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Tab") { e.preventDefault(); const s = ta.selectionStart; ta.setRangeText("  ", s, ta.selectionEnd, "end"); ta.dispatchEvent(new Event("input")); }
      if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveScript(false); }
    });
    ["#edName", "#edDesc", "#edKind"].forEach((s) => $(s).addEventListener("input", () => { st.dirty = true; renderEdMeta(); }));
    $("#edSave").addEventListener("click", () => saveScript(false));
    $("#edSaveAs").addEventListener("click", () => saveScript(true));
    $("#edCompile").addEventListener("click", () => compile(false));
    $("#edAdd").addEventListener("click", addCurrentToChart);
    $("#edApply").addEventListener("click", applyInputs);
    $("#edFav").addEventListener("click", async () => { if (!st.cur || !st.cur.id) return; const s = await api("/scripts/" + st.cur.id, { method: "PUT", body: { favorite: !st.cur.favorite } }); st.cur.favorite = s.favorite; $("#edFav").textContent = s.favorite ? "★" : "☆"; loadScripts(); });
    $("#edRestore").addEventListener("click", async () => {
      if (!st.cur || !st.cur.id || !$("#edVer").value) return;
      const v = await api("/scripts/" + st.cur.id + "/versions/" + $("#edVer").value).catch((e) => { showErr("#scErr", e); return null; });
      if (v) { $("#edTa").value = v.source; st.dirty = true; renderEdMeta(); renderEditorMirror(); }
    });
    $("#edDiag").addEventListener("click", (e) => { const d = e.target.closest(".diag"); const x = d && st.diags[+d.dataset.i]; if (x && x.line) gotoLine(x.line, x.column); });
    $("#btRun").addEventListener("click", runBacktest);
    $("#btHist").addEventListener("click", async (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) { try { renderBacktest(await api("/backtests/" + tr.dataset.id)); } catch (er) { showErr("#btErr", er); } } });
    $("#ppCreate").addEventListener("click", createPaper);
    $("#ppList").addEventListener("click", (e) => { const b = e.target.closest("button[data-act]"); const card = e.target.closest("[data-id]"); if (b && card) paperAction(card.dataset.id, b.dataset.act, b); });
    $("#aiRun").addEventListener("click", runAnalysis);
    $("#aiLevels").addEventListener("click", plotLevels);
    window.addEventListener("beforeunload", (e) => { if (st.dirty) { e.preventDefault(); e.returnValue = ""; } });
    setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) + " IST"; renderPill(); }, 1000);
  }

  async function boot() {
    wire(); bootChart(); renderPill(); loadHealth();
    try { st.templates = (await api("/templates")).templates; $("#scTpl").innerHTML = '<option value="">Blank script</option>' + st.templates.map((t) => '<option value="' + esc(t.id) + '">' + esc(t.name) + " (" + esc(t.kind) + ")</option>").join(""); } catch (e) { showErr("#scErr", e); }
    await loadScripts();
    loadEditor(newBlank());
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  window.SmartChartApp = st;      // for debugging / browser tests
})();
