/* Arena Terminal tab: live chart on the left, Orders / Positions / Closed on the right. Click a row -> its chart (option premium, or the underlying with the Winners Scalper (Pine) indicator).
   Read-only: it only GETs /api/arena/public/{live,chart,chart/underlying} with the same arena token as the rest of the page. Pure helpers are exported for node tests. */
"use strict";
const tmEsc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tmNum = (x, n = 2) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "–" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: n }));
const tmRs = (x) => (x === null || x === undefined ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + "₹" + Math.abs(Math.round(x)).toLocaleString("en-IN"));
const IST_SHIFT = 19800;

/* which right-hand tab a /live row belongs to */
function tmBucket(r) { return r.state === "WAITING" || r.state === "NO DATA" ? "orders" : r.state === "ACTIVE" ? "positions" : "closed"; }
/* nearest candle time at or before t (t and candle times are both already IST-shifted seconds) */
function tmSnap(times, t) { let lo = 0, hi = times.length - 1, ans = null; while (lo <= hi) { const m = (lo + hi) >> 1; if (times[m] <= t) { ans = times[m]; lo = m + 1; } else hi = m - 1; } return ans; }
/* levels drawn as lines on the option chart */
function tmLevels(r) {
  const L = [];
  if (r.entry != null) L.push({ price: r.entry, color: "#60a5fa", style: "dashed", title: r.state === "CLOSED" || r.state === "ACTIVE" ? "ENTRY" : r.entry_mode + " " });
  if (r.fill != null) L.push({ price: r.fill, color: "#e5e7eb", style: "solid", title: "FILL" });
  if (r.sl != null) L.push({ price: r.sl, color: "#ef4444", style: "solid", title: "SL" });
  (r.targets || []).forEach((t, i) => L.push({ price: t, color: "#22c55e", style: "dotted", title: "T" + (i + 1) }));
  if (r.exit != null) L.push({ price: r.exit, color: "#94a3b8", style: "dashed", title: "EXIT" });
  return L;
}
function tmRowHtml(r, prev) {
  const live = r.state === "ACTIVE" || r.state === "WAITING", dir = live && r.ltp != null && prev != null && r.ltp !== prev ? (r.ltp > prev ? "tickup" : "tickdn") : "";
  const pn = r.pnl_rs, cls = pn > 0 ? "pnlpos" : pn < 0 ? "pnlneg" : "";
  const side = `<span class="chip ${tmEsc(r.state)}">${tmEsc(r.state)}</span>`;
  const l1 = `<div class="tm-l1"><b>${tmEsc(r.label)}</b>${side}<span class="big ${cls}">${r.state === "ACTIVE" || r.state === "CLOSED" ? tmRs(pn) : ""}</span></div>`;
  const nums = [];
  if (live) nums.push(`<span class="${dir}">LTP <b>${tmNum(r.ltp)}</b></span>`, `<span>${tmNum(r.bid)} / ${tmNum(r.ask)}</span>`);
  if (r.state === "ACTIVE") nums.push(`<span>fill <b>${tmNum(r.fill)}</b></span>`, `<span>SL <b>${tmNum(r.sl)}</b></span>`, `<span>T1 <b>${tmNum((r.targets || [])[0])}</b></span>`);
  if (r.state === "WAITING") nums.push(`<span>${tmEsc(r.entry_mode || "")} <b>${tmNum(r.entry)}</b></span>`, `<span>SL <b>${tmNum(r.sl)}</b></span>`);
  if (r.state === "CLOSED") nums.push(`<span>fill <b>${tmNum(r.fill)}</b></span>`, `<span>exit <b>${tmNum(r.exit)}</b></span>`, `<span class="mut">${tmEsc(r.why || "")}</span>`);
  if (!["ACTIVE", "WAITING", "CLOSED"].includes(r.state)) nums.push(`<span class="mut">${tmEsc(r.why || r.waiting_note || "")}</span>`);
  return `${l1}<div class="tm-l2 mut">${tmEsc(r.id)} · ${r.lots || 1} lot · ${tmEsc(r.posted_ist || "")}${r.quote_age_s != null && live ? " · " + Math.round(r.quote_age_s) + "s" : ""}</div><div class="tm-l3">${nums.join("")}</div>`;
}
if (typeof module !== "undefined") module.exports = { tmBucket, tmSnap, tmLevels, tmRowHtml, tmRs };

if (typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id), pane = document.querySelector('[data-pane="terminal"]');
  if (!pane) return;
  const POLL_MS = 1500; let token = "", rows = [], sel = null, bucketTab = "orders", chart = null, series = {}, view = "option", tf = 1, utf = 15, timers = {}, lastLtp = {}, htmlCache = {}, lastBar = null, forming = null, userZoomed = false, lines = [], mk = null, gen = 0, lastUnderlying = null;
  const readToken = () => { try { token = sessionStorage.getItem("arena_token") || ""; } catch (e) { token = ""; } return token; };
  async function api(path) {
    const r = await fetch("/api/arena/public" + path, { headers: { "X-Arena-Token": readToken() }, cache: "no-store" });
    if (!r.ok) throw new Error(path.split("?")[0] + " " + r.status); return r.json();
  }
  const setNote = (t, bad) => { const n = $("tmNote"); n.textContent = t || ""; n.style.color = bad ? "#f87171" : "var(--mut)"; };

  /* ---------------------------------------------------------------- chart */
  function ensureChart() {
    if (chart || !window.LightweightCharts) return !!chart;
    const el = $("tmChart"), LW = window.LightweightCharts;
    chart = LW.createChart(el, { width: el.clientWidth, height: el.clientHeight, layout: { background: { color: "#0b0f14" }, textColor: "#dbe4f0", fontSize: 12 }, grid: { vertLines: { color: "#18212f" }, horzLines: { color: "#18212f" } },
      rightPriceScale: { borderColor: "#243047", scaleMargins: { top: 0.12, bottom: 0.1 } }, timeScale: { borderColor: "#243047", timeVisible: true, secondsVisible: false, rightOffset: 6 }, crosshair: { mode: 0 }, localization: { priceFormatter: (p) => p.toFixed(2) } });
    new ResizeObserver(() => { chart.applyOptions({ width: el.clientWidth, height: el.clientHeight }); if (!userZoomed) chart.timeScale().fitContent(); }).observe(el);
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => {});
    el.addEventListener("wheel", () => { userZoomed = true; }, { passive: true }); el.addEventListener("mousedown", () => { userZoomed = true; }); el.addEventListener("touchstart", () => { userZoomed = true; }, { passive: true });
    return true;
  }
  function clearChart() {
    for (const k of Object.keys(series)) { try { chart.removeSeries(series[k]); } catch (e) { /* ok */ } }
    series = {}; lines = []; mk = null; lastBar = null; forming = null;
  }
  const lineStyle = (s) => ({ solid: 0, dotted: 1, dashed: 2 }[s] ?? 0);
  function addCandles() {
    const LW = window.LightweightCharts; series.c = chart.addSeries(LW.CandlestickSeries, { upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444", priceLineVisible: true, lastValueVisible: true });
    mk = LW.createSeriesMarkers(series.c, []);
  }
  function drawLevels(r) {
    lines.forEach((l) => { try { series.c.removePriceLine(l); } catch (e) { /* ok */ } }); lines = [];
    tmLevels(r).forEach((L) => lines.push(series.c.createPriceLine({ price: L.price, color: L.color, lineWidth: 1, lineStyle: lineStyle(L.style), axisLabelVisible: true, title: L.title })));
  }
  function setMarkers(list) { if (mk) mk.setMarkers(list.sort((a, b) => a.time - b.time)); }
  function pineMarkers(times, ms) {
    return (ms || []).map((m) => ({ t: tmSnap(times, m.time), m })).filter((x) => x.t != null).map(({ t, m }) => (m.side === "BUY"
      ? { time: t, position: "belowBar", color: m.strong ? "#22c55e" : "#86efac", shape: "arrowUp", text: m.strong ? "BUY" : "buy" }
      : { time: t, position: "aboveBar", color: m.strong ? "#ef4444" : "#fca5a5", shape: "arrowDown", text: m.strong ? "SELL" : "sell" }));
  }

  async function loadOption(r, myGen) {
    const q = `/chart?underlying=${encodeURIComponent(r.underlying)}&expiry=${encodeURIComponent(r.expiry)}&strike=${r.strike}&option_type=${r.option_type}&tf=${tf}`;
    const [c, u] = await Promise.all([api(q), api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=15`).catch(() => null)]);
    if (myGen !== gen) return; clearChart(); addCandles(); series.c.setData(c.candles); lastBar = c.candles.length ? c.candles[c.candles.length - 1] : null; drawLevels(r);
    const times = c.candles.map((x) => x.time), mks = u ? pineMarkers(times, u.markers) : [];
    if (r.posted_ts) { const t = tmSnap(times, Math.floor(r.posted_ts) + IST_SHIFT); if (t != null) mks.push({ time: t, position: "belowBar", color: "#60a5fa", shape: "circle", text: "ORDER" }); }
    setMarkers(mks); lastUnderlying = u; if (!userZoomed) chart.timeScale().fitContent();
    setNote(c.candles.length ? `${c.candles.length} candles · ${tf}m · exchange-stamped quotes · Pine arrows = Winners Scalper signals on ${r.underlying}` : "No quotes captured for this contract yet today.");
  }
  async function refreshOption(r) {
    const since = lastBar ? lastBar.time - IST_SHIFT : undefined; const c = await api(`/chart?underlying=${encodeURIComponent(r.underlying)}&expiry=${encodeURIComponent(r.expiry)}&strike=${r.strike}&option_type=${r.option_type}&tf=${tf}` + (since ? `&since=${since}` : ""));
    for (const b of c.candles) { if (!lastBar || b.time >= lastBar.time) { series.c.update(b); lastBar = b; } }
  }
  async function loadUnderlying(r, myGen) {
    const u = await api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=${utf}`); if (myGen !== gen) return; clearChart(); addCandles(); const LW = window.LightweightCharts;
    series.c.setData(u.candles); lastBar = u.candles.length ? u.candles[u.candles.length - 1] : null; lastUnderlying = u;
    const up = [], dn = []; u.stop.forEach((p, i) => { up.push(p.up ? { time: p.time, value: p.value } : { time: p.time }); dn.push(p.up ? { time: p.time } : { time: p.time, value: p.value }); });
    series.up = chart.addSeries(LW.LineSeries, { color: "#22c55e", lineWidth: 2, priceLineVisible: false, lastValueVisible: false }); series.up.setData(up);
    series.dn = chart.addSeries(LW.LineSeries, { color: "#ef4444", lineWidth: 2, priceLineVisible: false, lastValueVisible: false }); series.dn.setData(dn);
    series.ma = chart.addSeries(LW.LineSeries, { color: "rgba(245,158,11,.55)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }); series.ma.setData(u.ma);
    const times = u.candles.map((x) => x.time), mks = pineMarkers(times, u.markers);
    if (r.posted_ts) { const t = tmSnap(times, Math.floor(r.posted_ts) + IST_SHIFT); if (t != null) mks.push({ time: t, position: "belowBar", color: "#60a5fa", shape: "circle", text: "ORDER" }); }
    setMarkers(mks); if (!userZoomed) chart.timeScale().fitContent();
    setNote(`${r.underlying} ${utf}m · ${u.source} · Winners Scalper (Pine): green/red = trailing stop, arrows = signals, orange = MA34` + (u.spot ? ` · spot ${tmNum(u.spot)} (live feed)` : ""));
  }
  async function refreshUnderlying(r) {
    const u = await api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=${utf}`); if (!u.candles.length || !series.c) return;
    let last = u.candles[u.candles.length - 1], span = utf * 60, nowIst = Math.floor(Date.now() / 1000) + IST_SHIFT, bucket = Math.floor(nowIst / span) * span;
    if (u.spot) {                                                                                  // broker-style: the live feed price drives the forming candle even when the stored candles are minutes behind
      if (bucket === last.time) { last.close = u.spot; last.high = Math.max(last.high, u.spot); last.low = Math.min(last.low, u.spot); }
      else if (bucket > last.time && bucket - last.time <= span * 3) { if (!forming || forming.time !== bucket) forming = { time: bucket, open: u.spot, high: u.spot, low: u.spot, close: u.spot }; forming.high = Math.max(forming.high, u.spot); forming.low = Math.min(forming.low, u.spot); forming.close = u.spot; last = { ...forming }; }
    }
    if (!lastBar || last.time >= lastBar.time) { series.c.update(last); lastBar = last; }
    if (u.markers.length !== (lastUnderlying ? lastUnderlying.markers.length : -1)) loadUnderlying(r, gen);                                  // a new Pine signal: redraw everything once
    lastUnderlying = u;
  }
  async function openChart(r) {
    if (!ensureChart()) { setNote("chart library not loaded", true); return; }
    sel = r; gen += 1; userZoomed = false; const my = gen; renderHeader(); highlight();
    try { if (view === "option") await loadOption(r, my); else await loadUnderlying(r, my); } catch (e) { setNote("chart: " + e.message, true); }
  }
  async function chartTick() {
    if (!sel || !series.c || document.hidden) return; const my = gen;
    try { if (view === "option") await refreshOption(sel); else await refreshUnderlying(sel); } catch (e) { /* next tick */ }
    if (my !== gen) return;
  }

  /* ---------------------------------------------------------------- right panel */
  function renderHeader() {
    const r = sel; if (!r) { $("tmTitle").textContent = "Select an order or a position →"; $("tmState").textContent = ""; $("tmLtp").textContent = ""; $("tmPnl").textContent = ""; return; }
    $("tmTitle").textContent = r.label; $("tmState").className = "chip " + r.state; $("tmState").textContent = r.state;
    $("tmLtp").textContent = r.ltp != null ? `LTP ${tmNum(r.ltp)}  (${tmNum(r.bid)} / ${tmNum(r.ask)})` : ""; const p = r.pnl_rs; $("tmPnl").textContent = p != null ? tmRs(p) : ""; $("tmPnl").className = p > 0 ? "pnlpos" : p < 0 ? "pnlneg" : "";
  }
  function highlight() { document.querySelectorAll("#tmList .tm-row").forEach((e) => e.classList.toggle("sel", sel && e.dataset.id === sel.id)); }
  function renderList() {
    const by = { orders: [], positions: [], closed: [] }; rows.forEach((r) => by[tmBucket(r)].push(r));
    $("tmCntOrders").textContent = by.orders.length; $("tmCntPos").textContent = by.positions.length; $("tmCntClosed").textContent = by.closed.length;
    document.querySelectorAll("[data-tm]").forEach((b) => b.classList.toggle("on", b.dataset.tm === bucketTab));
    const list = $("tmList"), want = by[bucketTab], ids = new Set(want.map((r) => r.id));
    list.querySelectorAll(".tm-row").forEach((e) => { if (!ids.has(e.dataset.id)) e.remove(); });
    want.forEach((r, i) => {
      let el = list.querySelector(`.tm-row[data-id="${CSS.escape(r.id)}"]`);
      if (!el) { el = document.createElement("div"); el.className = "tm-row"; el.dataset.id = r.id; el.onclick = () => { const cur = rows.find((x) => x.id === el.dataset.id); if (cur) openChart(cur); }; list.append(el); }
      const h = tmRowHtml(r, lastLtp[r.id]); if (htmlCache[r.id] !== h) { el.innerHTML = h; htmlCache[r.id] = h; }
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null); lastLtp[r.id] = r.ltp;
    });
    if (!want.length) { if (!list.querySelector(".tm-empty")) { const d = document.createElement("div"); d.className = "tm-empty mut"; d.textContent = { orders: "No pending orders.", positions: "No open positions.", closed: "No closed trades today." }[bucketTab]; list.append(d); } }
    else list.querySelectorAll(".tm-empty").forEach((e) => e.remove());
    highlight();
  }
  async function liveTick() {
    if (document.hidden) return;
    try {
      const d = await api("/live"); rows = d.rows || []; renderList();
      $("tmTot").innerHTML = `Open P&amp;L <b class="${d.total_open_pnl_rs > 0 ? "pnlpos" : d.total_open_pnl_rs < 0 ? "pnlneg" : ""}">${tmRs(d.total_open_pnl_rs)}</b> · Closed today <b class="${d.realised_today_rs > 0 ? "pnlpos" : d.realised_today_rs < 0 ? "pnlneg" : ""}">${tmRs(d.realised_today_rs)}</b> · <span class="mut">${tmEsc(d.now_ist)} IST</span>`;
      if (sel) { const cur = rows.find((x) => x.id === sel.id); if (cur) { const re = cur.sl !== sel.sl || cur.state !== sel.state || cur.fill !== sel.fill || JSON.stringify(cur.targets) !== JSON.stringify(sel.targets); sel = cur; renderHeader(); if (re && series.c && view === "option") drawLevels(cur); } }
      else if (rows.length) { const first = rows.find((x) => x.state === "ACTIVE") || rows.find((x) => x.state === "WAITING") || rows[0]; bucketTab = tmBucket(first); renderList(); openChart(first); }
    } catch (e) { setNote("live: " + e.message, true); }
  }
  function start() { stop(); readToken(); if (!token) { setNote("Connect with your arena token first (Guide tab).", true); return; } ensureChart(); liveTick(); timers.live = setInterval(liveTick, POLL_MS); timers.chart = setInterval(chartTick, POLL_MS); }
  function stop() { Object.values(timers).forEach(clearInterval); timers = {}; }

  /* ---------------------------------------------------------------- toolbar + tabs */
  document.querySelectorAll("[data-tm]").forEach((b) => (b.onclick = () => { bucketTab = b.dataset.tm; htmlCache = {}; $("tmList").replaceChildren(); renderList(); }));
  document.querySelectorAll("[data-tmview]").forEach((b) => (b.onclick = () => { view = b.dataset.tmview; document.querySelectorAll("[data-tmview]").forEach((x) => x.classList.toggle("on", x === b)); $("tmTfOpt").hidden = view !== "option"; $("tmTfUnd").hidden = view === "option"; if (sel) openChart(sel); }));
  document.querySelectorAll("[data-tmtf]").forEach((b) => (b.onclick = () => { tf = Number(b.dataset.tmtf); document.querySelectorAll("[data-tmtf]").forEach((x) => x.classList.toggle("on", x === b)); if (sel && view === "option") openChart(sel); }));
  document.querySelectorAll("[data-tmutf]").forEach((b) => (b.onclick = () => { utf = Number(b.dataset.tmutf); document.querySelectorAll("[data-tmutf]").forEach((x) => x.classList.toggle("on", x === b)); if (sel && view !== "option") openChart(sel); }));
  const zoom = (f) => { if (!chart) return; userZoomed = true; const ts = chart.timeScale(), o = ts.options(); ts.applyOptions({ barSpacing: Math.max(1, Math.min(60, (o.barSpacing || 6) * f)) }); };
  $("tmZoomIn").onclick = () => zoom(1.35); $("tmZoomOut").onclick = () => zoom(1 / 1.35); $("tmFit").onclick = () => { userZoomed = false; if (chart) { chart.timeScale().fitContent(); chart.priceScale("right").applyOptions({ autoScale: true }); } };
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { const on = b.dataset.tab === "terminal"; document.querySelector("main").classList.toggle("wide", on); if (on) setTimeout(start, 50); else stop(); }));
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !pane.hidden) { liveTick(); } });
})();
