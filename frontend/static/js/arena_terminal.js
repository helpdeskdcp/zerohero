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
/* ---- Pine indicators on the underlying chart: two scripts, each switched on / off like an indicator or strategy (the choice is remembered in the browser) */
const TM_INDICATORS = [{ key: "winners", label: "Winners Scalper", title: "Winners Scalper (Pine): trailing-stop lines, BUY / SELL arrows, MA34" }, { key: "uturn", label: "Safe U-turn", title: "Safe U-turn (Pine): CALL / PUT idea after a confirmed turn at the session high or low, with its historical base rate" }];
function tmIndLoad(raw) {
  const on = Object.fromEntries(TM_INDICATORS.map((x) => [x.key, true]));
  try { const o = JSON.parse(raw || "{}"); for (const x of TM_INDICATORS) if (typeof o[x.key] === "boolean") on[x.key] = o[x.key]; } catch (e) { /* unreadable setting: all on */ }
  return on;
}
function tmPineMarkers(times, ms) {
  return (ms || []).map((m) => ({ t: tmSnap(times, m.time), m })).filter((x) => x.t != null).map(({ t, m }) => (m.side === "BUY"
    ? { time: t, position: "belowBar", color: m.strong ? "#22c55e" : "#86efac", shape: "arrowUp", text: m.strong ? "BUY" : "buy" }
    : { time: t, position: "aboveBar", color: m.strong ? "#ef4444" : "#fca5a5", shape: "arrowDown", text: m.strong ? "SELL" : "sell" }));
}
function tmUturnText(m) {
  const br = m.base_rate; return `U-turn ${m.side}` + (br && br.target_hit != null ? ` · ${Math.round(br.target_hit * 100)}% (n=${br.n})` : "");
}
function tmUturnMarkers(times, list) {
  return (list || []).map((m) => ({ t: tmSnap(times, m.time), m })).filter((x) => x.t != null).map(({ t, m }) => (m.side === "CE"
    ? { time: t, position: "belowBar", color: "#38bdf8", shape: "arrowUp", text: tmUturnText(m) }
    : { time: t, position: "aboveBar", color: "#c084fc", shape: "arrowDown", text: tmUturnText(m) }));
}
function tmBuildMarkers(times, u, ind, orderMarker) {
  const out = []; if (u && ind.winners) out.push(...tmPineMarkers(times, u.markers)); if (u && ind.uturn) out.push(...tmUturnMarkers(times, u.uturn)); if (orderMarker) out.push(orderMarker);
  return out.sort((a, b) => a.time - b.time);
}
function tmIndNote(ind, u) {
  const names = TM_INDICATORS.filter((x) => ind[x.key]).map((x) => x.label); const cal = u && u.uturn_calibration && u.uturn_calibration.overall;
  return names.length ? `Indicators: ${names.join(" + ")}` + (ind.uturn && cal && cal.n ? ` · U-turn history: first target before stop ${Math.round(cal.target_hit * 100)}% of ${cal.n} (break-even, no proven edge)` : "") : "Indicators: none (switch them on above the chart)";
}
/* chart request of a /live row: an option contract, or an NSE cash-equity symbol */
function tmChartPath(r, tf, since) {
  const sfx = `&tf=${tf}` + (since ? `&since=${since}` : "");
  return r.instrument === "EQUITY" ? `/chart/equity?symbol=${encodeURIComponent(r.underlying)}${sfx}`
    : `/chart?underlying=${encodeURIComponent(r.underlying)}&expiry=${encodeURIComponent(r.expiry)}&strike=${r.strike}&option_type=${r.option_type}${sfx}`;
}
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
/* one-tap exit button: label / css class per phase (idle -> counting [2 s, tap again = cancel] -> sending -> sent | error) */
function tmExitUi(ex) {
  const p = (ex && ex.phase) || "idle";
  return { phase: p, cls: p, text: p === "counting" ? `Exiting in ${ex.left}s · tap to CANCEL` : p === "sending" ? "Sending…" : p === "sent" ? "EXIT SENT ✓ settling…" : p === "error" ? "Failed · tap to retry" : "EXIT" };
}
/* gross / charges / net of an open position, the mark it is valued at, and the exit-now figure; "stale quote" instead of a number when the price is too old (server: positions.open_valuation) */
function tmBreakdown(r) {
  if (!r || r.state !== "ACTIVE") return "";
  if (r.stale) return "quote is stale: no P&L shown until a fresh price arrives";
  if (r.gross_pnl_rs == null) return "";
  const money = (x) => (x < 0 ? "−" : "+") + "₹" + Math.abs(Math.round(x)).toLocaleString("en-IN");
  return `gross ${money(r.gross_pnl_rs)} · est. charges −₹${Math.round(r.est_charges_rs).toLocaleString("en-IN")} · net ${money(r.net_pnl_rs)} · mark ${tmNum(r.mark_price)} (${r.mark_basis})` + (r.exit_now_net_rs != null ? ` · exit now ${money(r.exit_now_net_rs)}` : "");
}
function tmRowHtml(r, prev, ex) {
  const live = r.state === "ACTIVE" || r.state === "WAITING", dir = live && r.ltp != null && prev != null && r.ltp !== prev ? (r.ltp > prev ? "tickup" : "tickdn") : "";
  const pn = r.pnl_rs, cls = pn > 0 ? "pnlpos" : pn < 0 ? "pnlneg" : "";
  const side = `<span class="chip ${tmEsc(r.state)}">${tmEsc(r.state)}</span>`;
  const l1 = `<div class="tm-l1"><b>${tmEsc(r.label)}</b>${side}<span class="big ${cls}">${r.state === "ACTIVE" || r.state === "CLOSED" ? tmRs(pn) : ""}</span></div>`;
  const nums = [];
  if (live) nums.push(`<span class="${dir}">LTP <b>${tmNum(r.ltp)}</b></span>`, `<span>${tmNum(r.bid)} / ${tmNum(r.ask)}</span>`);
  if (r.state === "ACTIVE") nums.push(`<span>fill <b>${tmNum(r.fill)}</b></span>`, `<span>SL <b>${tmNum(r.sl)}</b></span>`, `<span>T1 <b>${tmNum((r.targets || [])[0])}</b></span>`);
  if (r.state === "WAITING") nums.push(`<span>${tmEsc(r.entry_mode || "")} <b>${tmNum(r.entry)}</b></span>`, `<span>SL <b>${tmNum(r.sl)}</b></span>`, `<span>T1 <b>${tmNum((r.targets || [])[0])}</b></span>`);
  if (r.state === "CLOSED") nums.push(`<span>fill <b>${tmNum(r.fill)}</b></span>`, `<span>exit <b>${tmNum(r.exit)}</b></span>`, `<span class="mut">${tmEsc(r.why || "")}</span>`);
  if (!["ACTIVE", "WAITING", "CLOSED"].includes(r.state)) nums.push(`<span class="mut">${tmEsc(r.why || r.waiting_note || "")}</span>`);
  const btn = r.state === "ACTIVE" ? (() => { const u = tmExitUi(ex); return `<div class="tm-l4"><button class="tm-exit ${u.cls}" data-exit="${tmEsc(r.id)}">${tmEsc(u.text)}</button></div>`; })() : "";
  return `${l1}<div class="tm-l2 mut">${tmEsc(r.id)} · ${r.lots || 1} ${r.instrument === "EQUITY" ? "sh" : "lot"} · ${tmEsc(r.posted_ist || "")}${r.quote_age_s != null && live ? " · " + Math.round(r.quote_age_s) + "s" : ""}</div><div class="tm-l3">${nums.join("")}</div>${tmBreakdown(r) ? `<div class="tm-l2 mut" data-bd="1">${tmEsc(tmBreakdown(r))}</div>` : ""}${btn}`;
}

/* ---- order editor (broker-style modify): which fields an order allows, and the /amend events a form produces ---- */
function tmEditable(r) {
  if (!r) return null;
  if (r.state === "WAITING") return { entry: r.entry_mode === "ABOVE" || r.entry_mode === "LIMIT", sl: true, targets: true, lots: true, trail: false, cancel: true };
  if (r.state === "ACTIVE") return { entry: false, sl: true, targets: true, lots: false, trail: true, trailPct: !(r.instrument === "EQUITY" && r.side === "SELL"), cancel: false };
  return null;
}
const tmParseList = (v) => String(v == null ? "" : v).split(",").map((x) => x.trim()).filter(Boolean).map(Number);
/* form values -> [{action, ...}] (only what changed against the row); also returns human warnings (never blocking) and errors (blocking) */
function tmEditEvents(r, f) {
  const ed = tmEditable(r), ev = [], warn = [], err = [];
  if (!ed) return { ev, warn, err: ["This order can no longer be modified."] };
  const num = (v) => (v === "" || v == null ? null : Number(v));
  const same = (a, b) => a != null && b != null && Math.abs(a - b) < 1e-9;
  if (ed.entry) { const e = num(f.entry); if (e !== null) { if (!(e > 0)) err.push("Entry must be > 0"); else if (!same(e, r.entry)) ev.push({ action: "AMEND_ENTRY", entry: e }); } }
  const sl = num(f.sl); if (sl !== null) { if (!(sl > 0)) err.push("Stop-loss must be > 0"); else if (!same(sl, r.sl)) ev.push({ action: "AMEND_SL", sl }); }
  const raw = String(f.targets == null ? "" : f.targets).trim();
  if (raw) { const t = tmParseList(raw); if (!t.length || t.some((x) => !(x > 0))) err.push("Targets: comma separated numbers > 0"); else { const cur = (r.targets || []).slice().sort((a, b) => a - b), nw = [...new Set(t)].sort((a, b) => a - b); if (JSON.stringify(cur) !== JSON.stringify(nw)) ev.push({ action: "AMEND_TARGETS", targets: nw }); } }
  if (ed.lots) { const q = num(f.lots); if (q !== null) { if (!(Number.isInteger(q) && q >= 1)) err.push("Lots: whole number >= 1"); else if (q !== (r.lots || 1)) ev.push({ action: "AMEND_LOTS", lots: q }); } }
  const short = r.instrument === "EQUITY" && r.side === "SELL";
  if (ed.trail) { const tp = num(f.trail_points), tc = ed.trailPct === false ? null : num(f.trail_pct); if (tp !== null && tc !== null) err.push("Trail: give points OR percent, not both"); else if (tp !== null) { if (!(tp > 0)) err.push("Trail points must be > 0"); else ev.push({ action: "TRAIL_SL", trail_points: tp }); } else if (tc !== null) { if (!(tc > 0 && tc < 100)) err.push("Trail % must be between 0 and 100"); else ev.push({ action: "TRAIL_SL", trail_pct: tc }); } }
  const eSl = ev.find((e) => e.action === "AMEND_SL"), eTg = ev.find((e) => e.action === "AMEND_TARGETS"), eEn = ev.find((e) => e.action === "AMEND_ENTRY");
  const ref = r.state === "ACTIVE" ? r.fill : (eEn ? eEn.entry : r.entry), slN = eSl ? eSl.sl : r.sl, tgN = eTg ? eTg.targets : (r.targets || []);
  if (!short) {
    if (ref != null && slN != null && slN >= ref) warn.push("Stop-loss is at/above the entry price: it will trigger at once.");
    if (ref != null && tgN.length && tgN[0] <= ref) warn.push("First target is at/below the entry price.");
    if (r.state === "ACTIVE" && r.bid != null && eSl && eSl.sl >= r.bid) warn.push("Stop-loss is at/above the current bid: the position exits immediately.");
  } else {                                                                                        // a short: the stop sits ABOVE the entry, targets BELOW it; it exits at the ask
    if (ref != null && slN != null && slN <= ref) warn.push("Stop-loss is at/below the entry price (a short's stop must be above it): it will trigger at once.");
    if (ref != null && tgN.length && tgN[0] >= ref) warn.push("First target is at/above the entry price (a short's targets must be below it).");
    if (r.state === "ACTIVE" && r.ask != null && eSl && eSl.sl <= r.ask) warn.push("Stop-loss is at/below the current ask: the position is covered immediately.");
  }
  return { ev, warn, err };
}
function tmEditHtml(r) {
  const ed = tmEditable(r); if (!ed) return "";
  const v = (x) => (x == null ? "" : tmEsc(x)), inp = (n, label, val, extra = "") => `<label class="tm-f"><span>${label}</span><input data-f="${n}" inputmode="decimal" value="${v(val)}" ${extra}></label>`;
  const cur = r.state === "ACTIVE" ? `fill <b>${tmNum(r.fill)}</b> · SL <b>${tmNum(r.sl)}</b> · T <b>${tmEsc((r.targets || []).map((x) => tmNum(x)).join(", ") || "–")}</b>` : `${tmEsc(r.entry_mode || "")} <b>${tmNum(r.entry)}</b> · SL <b>${tmNum(r.sl)}</b> · T <b>${tmEsc((r.targets || []).map((x) => tmNum(x)).join(", ") || "–")}</b>`;
  return `<div class="tm-edit-h"><b>${r.state === "ACTIVE" ? "Modify position" : "Modify order"}</b> <span class="mut">${tmEsc(r.label)} · ${tmEsc(r.id)}</span></div>`
    + `<div class="tm-edit-cur mut" id="tmEditCur">now: ${cur}</div>`
    + `<div class="tm-edit-g">${ed.entry ? inp("entry", "Entry", r.entry) : ""}${inp("sl", "Stop-loss", r.sl)}${inp("targets", "Targets (a, b)", (r.targets || []).join(", "))}${ed.lots ? inp("lots", r.instrument === "EQUITY" ? "Qty (shares)" : "Lots", r.lots || 1) : ""}${ed.trail ? inp("trail_points", "Trail pts", "") + (ed.trailPct === false ? "" : inp("trail_pct", "Trail %", "")) : ""}</div>`
    + `<div class="tm-edit-b"><button class="tm-save" data-act="save">Save changes</button>${r.state === "ACTIVE" && r.fill != null ? '<button data-act="sl2cost">SL → cost</button>' : ""}${ed.cancel ? '<button class="tm-cancelo" data-act="cancel">Cancel order</button>' : ""}<span id="tmEditMsg" class="tm-edit-m"></span></div>`
    + `<div class="mut tm-edit-n">A change takes effect after the contest execution delay (same for everyone), never retroactively.</div>`;
}
if (typeof module !== "undefined") module.exports = { TM_INDICATORS, tmIndLoad, tmPineMarkers, tmUturnMarkers, tmUturnText, tmBuildMarkers, tmIndNote, tmBreakdown, tmChartPath, tmBucket, tmSnap, tmLevels, tmRowHtml, tmRs, tmExitUi, tmEditable, tmEditEvents, tmEditHtml, tmParseList };

if (typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id), pane = document.querySelector('[data-pane="terminal"]');
  if (!pane) return;
  const POLL_MS = 1000; let token = "", rows = [], sel = null, bucketTab = "orders", chart = null, series = {}, view = "option", tf = 1, utf = 15, timers = {}, lastLtp = {}, htmlCache = {}, lastBar = null, forming = null, userZoomed = false, lines = [], mk = null, gen = 0, lastUnderlying = null;
  const readToken = () => { try { token = sessionStorage.getItem("arena_token") || ""; } catch (e) { token = ""; } return token; };
  async function api(path) {
    const r = await fetch("/api/arena/public" + path, { headers: { "X-Arena-Token": readToken() }, cache: "no-store" });
    if (!r.ok) throw new Error(path.split("?")[0] + " " + r.status); return r.json();
  }
  async function apiPost(path, body) {
    const r = await fetch("/api/arena/public" + path, { method: "POST", headers: { "X-Arena-Token": readToken(), "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
    let j = {}; try { j = await r.json(); } catch (e) { /* non-json error body */ }
    if (!r.ok) throw new Error(j.detail || (path + " " + r.status)); return j;
  }
  // EXIT_DELAY_MS: 0 = the tap sends immediately (owner 2026-10-26: no extra wait on an exit); >0 = countdown with tap-again-to-cancel
  const EXIT_DELAY_MS = (typeof window !== "undefined" && typeof window.TM_EXIT_DELAY_MS === "number") ? window.TM_EXIT_DELAY_MS : 0;
  const exitState = {}; let exitTimer = null;
  const exitUiOf = (id) => exitState[id] || null;
  function exitTick() {
    const now = Date.now(); let any = false;
    for (const id of Object.keys(exitState)) { const e = exitState[id]; if (e.phase !== "counting") continue; any = true; e.left = Math.max(1, Math.ceil((e.deadline - now) / 1000)); if (now >= e.deadline) { e.phase = "sending"; postExit(id); } }
    renderList(); renderExitBtn(); if (!any && exitTimer) { clearInterval(exitTimer); exitTimer = null; }
  }
  async function postExit(id) {
    try { await apiPost("/amend", { signal_id: id, action: "EXIT_NOW", note: "terminal one-tap exit" }); exitState[id].phase = "sent"; setNote(`Exit recorded for ${id} — it settles after the contest execution delay, at the bid.`); }
    catch (e) { exitState[id].phase = "error"; setNote("Exit failed: " + e.message, true); }
    renderList(); renderExitBtn(); liveTick();
  }
  function exitClick(id) {
    const e = exitState[id];
    if (e && (e.phase === "sending" || e.phase === "sent")) return;
    if (e && e.phase === "counting") { delete exitState[id]; setNote("Exit cancelled."); renderList(); renderExitBtn(); return; }      // second tap during the countdown = cancel
    if (EXIT_DELAY_MS <= 0) { exitState[id] = { phase: "sending" }; setNote(`Exit ${id} sent…`); renderList(); renderExitBtn(); postExit(id); return; }
    exitState[id] = { phase: "counting", deadline: Date.now() + EXIT_DELAY_MS, left: Math.ceil(EXIT_DELAY_MS / 1000) }; setNote(`Exit ${id} in ${Math.ceil(EXIT_DELAY_MS / 1000)} s — tap again to cancel.`);
    if (!exitTimer) exitTimer = setInterval(exitTick, 200); renderList(); renderExitBtn();
  }
  function renderExitBtn() {
    const b = $("tmExit"); if (!b) return; const r = sel; b.hidden = !(r && r.state === "ACTIVE"); if (b.hidden) return;
    const u = tmExitUi(exitUiOf(r.id)); b.textContent = u.text; b.className = "tm-exit " + u.cls;
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
  let ind = (() => { try { return tmIndLoad(localStorage.getItem("arena_ind")); } catch (e) { return tmIndLoad(""); } })(), lastTimes = [], lastOrder = null;
  const orderMarker = (r, times) => { if (!r.posted_ts) return null; const t = tmSnap(times, Math.floor(r.posted_ts) + IST_SHIFT); return t != null ? { time: t, position: "belowBar", color: "#60a5fa", shape: "circle", text: "ORDER" } : null; };
  function applyInd() {                                                                          // a switch was flipped: lines, markers and the note follow at once, no reload
    for (const k of ["up", "dn", "ma"]) if (series[k]) series[k].applyOptions({ visible: !!ind.winners });
    setMarkers(tmBuildMarkers(lastTimes, lastUnderlying, ind, lastOrder));
    if (lastUnderlying) setNote(tmIndNote(ind, lastUnderlying));
  }

  async function loadOption(r, myGen) {
    const q = tmChartPath(r, tf);
    const [c, u] = await Promise.all([api(q), r.instrument === "EQUITY" ? Promise.resolve(null) : api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=15`).catch(() => null)]);
    if (myGen !== gen) return; clearChart(); addCandles(); series.c.setData(c.candles); lastBar = c.candles.length ? c.candles[c.candles.length - 1] : null; drawLevels(r);
    const times = c.candles.map((x) => x.time); lastTimes = times; lastOrder = orderMarker(r, times); lastUnderlying = u;
    setMarkers(tmBuildMarkers(times, u, ind, lastOrder)); if (!userZoomed) chart.timeScale().fitContent();
    setNote(c.candles.length ? `${c.candles.length} candles · ${tf}m · exchange-stamped quotes` + (r.instrument === "EQUITY" ? ` · ${r.underlying} cash equity` : ` · Pine arrows on ${r.underlying}: ${tmIndNote(ind, u)}`) : "No quotes captured for this contract yet today.");
  }
  async function refreshOption(r) {
    const since = lastBar ? lastBar.time - IST_SHIFT : undefined; const c = await api(tmChartPath(r, tf, since));
    for (const b of c.candles) { if (!lastBar || b.time >= lastBar.time) { series.c.update(b); lastBar = b; } }
  }
  async function loadUnderlying(r, myGen) {
    const u = await api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=${utf}`); if (myGen !== gen) return; clearChart(); addCandles(); const LW = window.LightweightCharts;
    series.c.setData(u.candles); lastBar = u.candles.length ? u.candles[u.candles.length - 1] : null; lastUnderlying = u;
    const up = [], dn = []; u.stop.forEach((p, i) => { up.push(p.up ? { time: p.time, value: p.value } : { time: p.time }); dn.push(p.up ? { time: p.time } : { time: p.time, value: p.value }); });
    series.up = chart.addSeries(LW.LineSeries, { color: "#22c55e", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, visible: !!ind.winners }); series.up.setData(up);
    series.dn = chart.addSeries(LW.LineSeries, { color: "#ef4444", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, visible: !!ind.winners }); series.dn.setData(dn);
    series.ma = chart.addSeries(LW.LineSeries, { color: "rgba(245,158,11,.55)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, visible: !!ind.winners }); series.ma.setData(u.ma);
    const times = u.candles.map((x) => x.time); lastTimes = times; lastOrder = orderMarker(r, times);
    setMarkers(tmBuildMarkers(times, u, ind, lastOrder)); if (!userZoomed) chart.timeScale().fitContent();
    setNote(`${r.underlying} ${utf}m · ${u.source} · ${tmIndNote(ind, u)}` + (ind.winners ? " · green/red = Winners trailing stop, orange = MA34" : "") + (u.spot ? ` · spot ${tmNum(u.spot)} (live feed)` : ""));
  }
  async function refreshUnderlying(r) {
    const u = await api(`/chart/underlying?underlying=${encodeURIComponent(r.underlying)}&tf=${utf}`); if (!u.candles.length || !series.c) return;
    let last = u.candles[u.candles.length - 1], span = utf * 60, nowIst = Math.floor(Date.now() / 1000) + IST_SHIFT, bucket = Math.floor(nowIst / span) * span;
    if (u.spot) {                                                                                  // broker-style: the live feed price drives the forming candle even when the stored candles are minutes behind
      if (bucket === last.time) { last.close = u.spot; last.high = Math.max(last.high, u.spot); last.low = Math.min(last.low, u.spot); }
      else if (bucket > last.time && bucket - last.time <= span * 3) { if (!forming || forming.time !== bucket) forming = { time: bucket, open: u.spot, high: u.spot, low: u.spot, close: u.spot }; forming.high = Math.max(forming.high, u.spot); forming.low = Math.min(forming.low, u.spot); forming.close = u.spot; last = { ...forming }; }
    }
    if (!lastBar || last.time >= lastBar.time) { series.c.update(last); lastBar = last; }
    if (u.markers.length !== (lastUnderlying ? lastUnderlying.markers.length : -1) || (u.uturn || []).length !== (lastUnderlying ? (lastUnderlying.uturn || []).length : -1)) loadUnderlying(r, gen);      // a new Pine signal (either script): redraw everything once
    lastUnderlying = u;
  }
  async function openChart(r) {
    if (!ensureChart()) { setNote("chart library not loaded", true); return; }
    if (r.instrument === "EQUITY" && view !== "option") { view = "option"; document.querySelectorAll("[data-tmview]").forEach((x) => x.classList.toggle("on", x.dataset.tmview === "option")); }          // cash equity has no Pine / underlying view
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
    const r = sel; if (!r) { renderExitBtn(); renderEditor(); $("tmTitle").textContent = "Select an order or a position →"; $("tmState").textContent = ""; $("tmLtp").textContent = ""; $("tmPnl").textContent = ""; return; }
    renderExitBtn(); renderEditor();
    $("tmTitle").textContent = r.label; $("tmState").className = "chip " + r.state; $("tmState").textContent = r.state;
    $("tmLtp").textContent = r.ltp != null ? `LTP ${tmNum(r.ltp)}  (${tmNum(r.bid)} / ${tmNum(r.ask)})` : ""; const p = r.pnl_rs; $("tmPnl").textContent = p != null ? tmRs(p) : (r.stale ? "stale" : ""); $("tmPnl").title = tmBreakdown(r); $("tmPnl").className = p > 0 ? "pnlpos" : p < 0 ? "pnlneg" : "";
  }

  /* ---------------------------------------------------------------- order editor (click an order / position to modify it) */
  let editKey = "";
  function renderEditor() {
    const box = $("tmEdit"); if (!box) return;
    const r = sel, ed = tmEditable(r);
    if (!ed) { box.hidden = true; box.innerHTML = ""; editKey = ""; return; }
    const key = r.id + "|" + r.state;
    if (key !== editKey) { box.innerHTML = tmEditHtml(r); editKey = key; box.hidden = false; }
    else { const c = $("tmEditCur"); if (c) { const t = document.createElement("div"); t.innerHTML = tmEditHtml(r); const nc = t.querySelector("#tmEditCur"); if (nc && c.innerHTML !== nc.innerHTML) c.innerHTML = nc.innerHTML; } }   // levels refresh, the typed values stay
  }
  const editVals = () => { const f = {}; document.querySelectorAll("#tmEdit [data-f]").forEach((i) => (f[i.dataset.f] = i.value)); return f; };
  const editMsg = (t, bad) => { const m = $("tmEditMsg"); if (m) { m.textContent = t; m.style.color = bad ? "#fca5a5" : "#86efac"; } };
  async function sendEvents(r, evs) {
    const done = [];
    for (const e of evs) { await apiPost("/amend", { signal_id: r.id, ...e }); done.push(e.action.replace("AMEND_", "").toLowerCase()); }
    return done;
  }
  async function editAct(act) {
    const r = sel; if (!r || !tmEditable(r)) return;
    try {
      if (act === "cancel") { if (!confirm("Cancel this waiting order?")) return; editMsg("Sending…"); await sendEvents(r, [{ action: "CANCEL" }]); editMsg("Cancel sent ✓"); }
      else if (act === "sl2cost") { if (r.fill == null) return; editMsg("Sending…"); await sendEvents(r, [{ action: "AMEND_SL", sl: r.fill }]); editMsg("SL → cost sent ✓"); }
      else {
        const x = tmEditEvents(r, editVals()); if (x.err.length) { editMsg(x.err.join(" · "), true); return; }
        if (!x.ev.length) { editMsg("Nothing changed."); return; }
        if (x.warn.length && !confirm(x.warn.join("\n") + "\n\nSend anyway?")) return;
        editMsg("Sending…"); const done = await sendEvents(r, x.ev); editMsg("Sent: " + done.join(", ") + " ✓ (effective after the execution delay)");
      }
      editKey = ""; liveTick();
    } catch (e) { editMsg(e.message || String(e), true); }
  }
  if ($("tmEdit")) $("tmEdit").addEventListener("click", (ev) => { const b = ev.target && ev.target.closest ? ev.target.closest("[data-act]") : null; if (b) editAct(b.dataset.act); });
  if ($("tmEdit")) $("tmEdit").addEventListener("keydown", (ev) => { if (ev.key === "Enter" && ev.target && ev.target.matches && ev.target.matches("[data-f]")) editAct("save"); });
  function highlight() { document.querySelectorAll("#tmList .tm-row").forEach((e) => e.classList.toggle("sel", sel && e.dataset.id === sel.id)); }
  function renderList() {
    const by = { orders: [], positions: [], closed: [] }; rows.forEach((r) => by[tmBucket(r)].push(r));
    $("tmCntOrders").textContent = by.orders.length; $("tmCntPos").textContent = by.positions.length; $("tmCntClosed").textContent = by.closed.length;
    document.querySelectorAll("[data-tm]").forEach((b) => b.classList.toggle("on", b.dataset.tm === bucketTab));
    const list = $("tmList"), want = by[bucketTab], ids = new Set(want.map((r) => r.id));
    list.querySelectorAll(".tm-row").forEach((e) => { if (!ids.has(e.dataset.id)) e.remove(); });
    want.forEach((r, i) => {
      let el = list.querySelector(`.tm-row[data-id="${CSS.escape(r.id)}"]`);
      if (!el) { el = document.createElement("div"); el.className = "tm-row"; el.dataset.id = r.id; el.onclick = (ev) => { const b = ev && ev.target && ev.target.closest ? ev.target.closest("[data-exit]") : null; if (b) { ev.stopPropagation(); exitClick(el.dataset.id); return; } const cur = rows.find((x) => x.id === el.dataset.id); if (cur) openChart(cur); }; list.append(el); }
      const h = tmRowHtml(r, lastLtp[r.id], exitUiOf(r.id)); if (htmlCache[r.id] !== h) { el.innerHTML = h; htmlCache[r.id] = h; }
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null); lastLtp[r.id] = r.ltp;
    });
    if (!want.length) { if (!list.querySelector(".tm-empty")) { const d = document.createElement("div"); d.className = "tm-empty mut"; d.textContent = { orders: "No pending orders.", positions: "No open positions.", closed: "No closed trades today." }[bucketTab]; list.append(d); } }
    else list.querySelectorAll(".tm-empty").forEach((e) => e.remove());
    highlight();
  }
  async function liveTick() {
    if (document.hidden) return;
    try {
      const d = await api("/live"); rows = d.rows || []; for (const id of Object.keys(exitState)) { const r0 = rows.find((x) => x.id === id); if (!r0 || r0.state !== "ACTIVE") delete exitState[id]; } renderList();
      $("tmTot").innerHTML = `Open P&amp;L <b class="${d.total_open_pnl_rs > 0 ? "pnlpos" : d.total_open_pnl_rs < 0 ? "pnlneg" : ""}">${tmRs(d.total_open_pnl_rs)}</b> · Closed today <b class="${d.realised_today_rs > 0 ? "pnlpos" : d.realised_today_rs < 0 ? "pnlneg" : ""}">${tmRs(d.realised_today_rs)}</b> · <span class="mut">${tmEsc(d.now_ist)} IST</span>`;
      if (sel) { const cur = rows.find((x) => x.id === sel.id); if (cur) { const re = cur.sl !== sel.sl || cur.state !== sel.state || cur.fill !== sel.fill || JSON.stringify(cur.targets) !== JSON.stringify(sel.targets); sel = cur; renderHeader(); if (re && series.c && view === "option") drawLevels(cur); } }
      else if (rows.length) { const first = rows.find((x) => x.state === "ACTIVE") || rows.find((x) => x.state === "WAITING") || rows[0]; bucketTab = tmBucket(first); renderList(); openChart(first); }
    } catch (e) { setNote("live: " + e.message, true); }
  }
  if (window.ArenaPush) { ArenaPush.subscribe(() => { if (!pane.hidden) liveTick(); }); ArenaPush.start(readToken); }                    // an order changed: refresh the terminal at once instead of waiting for the next 1 s poll
  function start() { stop(); readToken(); if (!token) { setNote("Connect with your arena token first (Guide tab).", true); return; } ensureChart(); liveTick(); timers.live = setInterval(liveTick, POLL_MS); timers.chart = setInterval(chartTick, POLL_MS); }
  function stop() { Object.values(timers).forEach(clearInterval); timers = {}; }

  /* ---------------------------------------------------------------- toolbar + tabs */
  document.querySelectorAll("[data-tm]").forEach((b) => (b.onclick = () => { bucketTab = b.dataset.tm; htmlCache = {}; $("tmList").replaceChildren(); renderList(); }));
  document.querySelectorAll("[data-tmview]").forEach((b) => (b.onclick = () => { view = b.dataset.tmview; document.querySelectorAll("[data-tmview]").forEach((x) => x.classList.toggle("on", x === b)); $("tmTfOpt").hidden = view !== "option"; $("tmTfUnd").hidden = view === "option"; if (sel) openChart(sel); }));
  document.querySelectorAll("[data-tmtf]").forEach((b) => (b.onclick = () => { tf = Number(b.dataset.tmtf); document.querySelectorAll("[data-tmtf]").forEach((x) => x.classList.toggle("on", x === b)); if (sel && view === "option") openChart(sel); }));
  document.querySelectorAll("[data-tmutf]").forEach((b) => (b.onclick = () => { utf = Number(b.dataset.tmutf); document.querySelectorAll("[data-tmutf]").forEach((x) => x.classList.toggle("on", x === b)); if (sel && view !== "option") openChart(sel); }));
  document.querySelectorAll("[data-ind]").forEach((b) => {
    b.checked = !!ind[b.dataset.ind];
    b.onchange = () => { ind[b.dataset.ind] = b.checked; try { localStorage.setItem("arena_ind", JSON.stringify(ind)); } catch (e) { /* storage blocked: the switch still works for this visit */ } applyInd(); };
  });
  if ($("tmExit")) $("tmExit").onclick = () => { if (sel && sel.state === "ACTIVE") exitClick(sel.id); };
  const zoom = (f) => { if (!chart) return; userZoomed = true; const ts = chart.timeScale(), o = ts.options(); ts.applyOptions({ barSpacing: Math.max(1, Math.min(60, (o.barSpacing || 6) * f)) }); };
  $("tmZoomIn").onclick = () => zoom(1.35); $("tmZoomOut").onclick = () => zoom(1 / 1.35); $("tmFit").onclick = () => { userZoomed = false; if (chart) { chart.timeScale().fitContent(); chart.priceScale("right").applyOptions({ autoScale: true }); } };
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { const on = b.dataset.tab === "terminal"; document.querySelector("main").classList.toggle("wide", on); if (on) setTimeout(start, 50); else stop(); }));
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !pane.hidden) { liveTick(); } });
})();
