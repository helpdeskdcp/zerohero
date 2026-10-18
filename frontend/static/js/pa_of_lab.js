/* PA / Order-Flow research lab page. RESEARCH ONLY: read-only GETs to /api/pa_of_lab/*, no order controls.
   Pure helpers are exported (node tests); browser wiring runs only when `window` exists. Time axis = payload epoch seconds + 19800 (IST display). */
"use strict";
const IST = 19800;
const SIDE_NOTE = "Aggressor side is INFERRED (quote/tick rule) — not broker-confirmed buy/sell volume.";

const istHM = (t) => new Date((t + IST) * 1000).toISOString().slice(11, 16);
const istFull = (t) => new Date((t + IST) * 1000).toISOString().replace("T", " ").slice(0, 19) + " IST";
const fmt = (x, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(d));

/* UI state from a payload / error. kinds: loading | error | nodata | stale | ok */
function describeState(p, err) {
  if (err) return { kind: "error", text: "Error: " + (err.message || err) };
  if (!p) return { kind: "loading", text: "Loading…" };
  const s = p.status || {};
  if (s.state === "NO_DATA") return { kind: "nodata", text: s.message || "No data for this selection" };
  if (s.state === "STALE") return { kind: "stale", text: s.message || "Feed is stale" };
  return { kind: "ok", text: p.meta.price_source === "CANDLE_DERIVED" ? "Candle source — order flow unavailable" : "OK" };
}

function candleData(p) {
  return p.bars.map((b) => ({ time: b.t + IST, open: b.o, high: b.h, low: b.l, close: b.c,
    ...(b.closed ? {} : { color: "#64748b", wickColor: "#64748b", borderColor: "#64748b" }) }));
}

const TYPE_GROUPS = {
  swing: ["SH", "SL"], structure: ["BOS_UP", "BOS_DN", "CHOCH_UP", "CHOCH_DN", "RETEST_UP", "RETEST_DN"],
  liquidity: ["SWEEP_HIGH", "SWEEP_LOW", "EQH", "EQL"], rejection: ["REJECTION_BULL", "REJECTION_BEAR"], signal: ["PA", "OF"],
};

/* markers for the price-action series. confirmed = solid colour, provisional = grey with "?" — never merged. */
function buildMarkers(p, show) {
  const m = [], pa = p.pa; if (!pa) return m;
  const add = (t, pos, color, shape, text) => m.push({ time: t + IST, position: pos, color, shape, text });
  if (show.swing) {
    for (const e of pa.pivots) add(e.t, e.type === "SH" ? "aboveBar" : "belowBar", e.type === "SH" ? "#f59e0b" : "#38bdf8", "circle", e.label || e.type);
    for (const e of pa.provisional) add(e.t, e.type === "SH" ? "aboveBar" : "belowBar", "#94a3b8", "circle", e.type + "?");
  }
  for (const e of pa.events) {
    const g = Object.keys(TYPE_GROUPS).find((k) => TYPE_GROUPS[k].includes(e.type));
    if (!g || !show[g]) continue;
    const up = /UP|HIGH|BULL|EQL/.test(e.type) && !/SWEEP_HIGH|EQH/.test(e.type);
    const dn = !up;
    if (g === "structure") add(e.t, up ? "belowBar" : "aboveBar", up ? "#22c55e" : "#ef4444", up ? "arrowUp" : "arrowDown", e.type.replace("_", " "));
    else if (g === "liquidity") add(e.t, dn ? "aboveBar" : "belowBar", "#a78bfa", "square", e.type.replace("_", " "));
    else add(e.t, up ? "belowBar" : "aboveBar", up ? "#4ade80" : "#f87171", "circle", e.type === "REJECTION_BULL" ? "rej+" : "rej-");
  }
  if (show.signal) {
    for (const s of pa.signals) add(s.t, s.side > 0 ? "belowBar" : "aboveBar", s.side > 0 ? "#16a34a" : "#dc2626", s.side > 0 ? "arrowUp" : "arrowDown", "PA " + (s.side > 0 ? "L" : "S"));
    for (const s of (p.of && p.of.signals) || []) add(s.t, s.side > 0 ? "belowBar" : "aboveBar", "#06b6d4", "square", "OF " + s.kind.slice(0, 3));
  }
  m.sort((a, b) => a.time - b.time);
  return m;
}

/* one flat list of everything plotted, for the inspector table: timestamp, price, rule, status */
function allEvents(p) {
  const pa = p.pa; if (!pa) return [];
  const rows = [];
  for (const e of pa.pivots) rows.push({ t: e.t, kind: e.label || e.type, price: e.price, rule: e.rule, status: e.status, confirmed_t: e.confirmed_t, id: e.id });
  for (const e of pa.provisional) rows.push({ t: e.t, kind: e.type + "?", price: e.price, rule: e.rule, status: e.status, confirmed_t: null, id: e.id });
  for (const e of pa.events) rows.push({ t: e.t, kind: e.type, price: e.price, rule: e.rule, status: e.status, confirmed_t: e.confirmed_t, id: e.id });
  for (const s of pa.signals) rows.push({ t: s.t, kind: "PA SIGNAL " + (s.side > 0 ? "LONG" : "SHORT"), price: s.entry, rule: s.rule, status: s.status, confirmed_t: s.confirmed_t, id: s.id });
  for (const s of (p.of && p.of.signals) || []) rows.push({ t: s.t, kind: "OF " + s.kind, price: s.entry, rule: s.rule, status: s.status, confirmed_t: s.confirmed_t, id: s.id });
  return rows.sort((a, b) => a.t - b.t || a.kind.localeCompare(b.kind));
}

function filterSignals(p, type, side) {
  const all = [];
  for (const s of (p.pa && p.pa.signals) || []) all.push({ ...s, type: "PA" });
  for (const s of (p.of && p.of.signals) || []) all.push({ ...s, type: "OF" });
  const combined = Object.fromEntries((p.combined || []).map((c) => [c.idx, c]));
  return all.filter((s) => (type === "ALL" || s.type === type) && (side === 0 || s.side === side)).map((s) => ({ ...s, combined: combined[s.idx] || null })).sort((a, b) => a.t - b.t);
}

/* footprint cells for one bar. Tick-level rows are SUMMED into display bins of `binTicks` ticks (lossless: totals are preserved); `y(price)` comes from the chart.
   Returns [] when levels are absent (never fabricates). Imbalance (>=3:1, >=10 contracts) is evaluated on the display bin. */
function footprintCells(fbar, y, tick, binTicks = 1) {
  if (!fbar || !fbar.levels || !fbar.levels.length) return [];
  const bs = tick * binTicks, agg = new Map();
  for (const [price, buy, sell] of fbar.levels) {
    const k = Math.floor(price / bs + 1e-6), a = agg.get(k) || { buy: 0, sell: 0 };
    a.buy += buy; a.sell += sell; agg.set(k, a);
  }
  return [...agg.entries()].sort((a, b) => a[0] - b[0]).map(([k, { buy, sell }]) => {
    const lo = k * bs, top = y(lo + bs - tick / 2), bot = y(lo - tick / 2);
    const tot = buy + sell, ratio = buy >= sell ? (sell ? buy / sell : Infinity) : (buy ? -(sell / buy) : -Infinity);
    return { price: lo, buy, sell, y: Math.min(top, bot), h: Math.abs(bot - top), imbalance: Math.abs(ratio) >= 3 && tot >= 10 ? Math.sign(ratio) : 0, tot };
  });
}

function dqBadge(dq) { return dq ? dq.status + (dq.reasons && dq.reasons.length ? " (" + dq.reasons.join(", ") + ")" : "") : "—"; }

/* cvd line data with whitespace points at segment breaks so the line never bridges a feed gap */
function cvdSeries(p) {
  const out = [], f = p.footprint.bars; let prev = null;
  for (const b of f) {
    if (prev !== null && b.t - prev !== p.meta.tf_sec) out.push({ time: prev + p.meta.tf_sec + IST });
    out.push({ time: b.t + IST, value: b.cvd_segment }); prev = b.t;
  }
  return out;
}
function deltaSeries(p) {
  return p.footprint.bars.map((b) => ({ time: b.t + IST, value: b.delta, color: b.dq.status !== "OK" ? "#64748b" : b.delta >= 0 ? "#22c55e" : "#ef4444" }));
}

if (typeof module !== "undefined") module.exports = { istHM, istFull, fmt, describeState, candleData, buildMarkers, allEvents, filterSignals, footprintCells, cvdSeries, deltaSeries, dqBadge, TYPE_GROUPS, SIDE_NOTE };

/* ------------------------------ browser wiring ------------------------------ */
if (typeof window !== "undefined" && typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id);
  const LW = window.LightweightCharts;
  let payload = null, selected = null, timer = null, syncing = false;
  const show = { swing: true, structure: true, liquidity: true, rejection: false, signal: true };
  const lsGet = (k, d) => { try { return localStorage.getItem(k) || d; } catch (e) { return d; } };
  const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked: fine */ } };

  const opts = (h) => ({ autoSize: true, height: h, layout: { background: { color: "#0b0f14" }, textColor: "#cbd5e1" }, grid: { vertLines: { color: "#1b2433" }, horzLines: { color: "#1b2433" } },
    rightPriceScale: { borderColor: "#243047" }, timeScale: { borderColor: "#243047", timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 } });
  const A = LW.createChart($("chartA"), opts()), B = LW.createChart($("chartB"), opts());
  const candA = A.addSeries(LW.CandlestickSeries, { upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444" });
  const candB = B.addSeries(LW.CandlestickSeries, { upColor: "rgba(34,197,94,.15)", downColor: "rgba(239,68,68,.15)", borderVisible: false, wickUpColor: "#475569", wickDownColor: "#475569" });
  let vwapS = null, deltaS = null, cvdS = null, mk = null, lines = [], paneOK = true;
  try {
    deltaS = B.addSeries(LW.HistogramSeries, { priceScaleId: "delta", priceFormat: { type: "volume" } }, 1);
    cvdS = B.addSeries(LW.LineSeries, { priceScaleId: "cvd", color: "#38bdf8", lineWidth: 2, lastValueVisible: false }, 1);
    B.priceScale("cvd").applyOptions({ visible: true, borderColor: "#243047" });
  } catch (e) { paneOK = false; }
  mk = LW.createSeriesMarkers(candA, []);

  function overlay(host) {
    const c = document.createElement("canvas"); c.style.cssText = "position:absolute;inset:0;z-index:10;pointer-events:none;width:100%;height:100%"; host.style.position = "relative"; host.appendChild(c); return c;
  }
  const ovA = overlay($("chartA")), ovB = overlay($("chartB"));
  const ctxFit = (cv) => { const r = cv.getBoundingClientRect(), d = window.devicePixelRatio || 1; cv.width = r.width * d; cv.height = r.height * d; const x = cv.getContext("2d"); x.setTransform(d, 0, 0, d, 0, 0); x.clearRect(0, 0, r.width, r.height); return [x, r]; };

  function drawHighlight(x, chart, r) {
    if (!selected) return; const px = chart.timeScale().timeToCoordinate(selected.t + IST); if (px === null) return;
    const w = Math.max(6, chart.timeScale().options().barSpacing); x.fillStyle = "rgba(250,204,21,.12)"; x.fillRect(px - w / 2, 0, w, r.height);
  }
  function drawA() { const [x, r] = ctxFit(ovA); if (payload) drawHighlight(x, A, r); }
  function drawB() {
    const [x, r] = ctxFit(ovB); if (!payload || !payload.footprint.available) return;
    const ts = B.timeScale(), sp = ts.options().barSpacing, tick = payload.meta.tick_size || 0.1;
    const vr = ts.getVisibleLogicalRange(); if (!vr) return;
    const bars = payload.footprint.bars;
    for (let i = Math.max(0, Math.floor(vr.from) - 1); i <= Math.min(bars.length - 1, Math.ceil(vr.to) + 1); i++) {
      const b = bars[i], px = ts.timeToCoordinate(b.t + IST); if (px === null) continue;
      const w = sp * 0.9, pxTick = Math.abs((candB.priceToCoordinate(b.c + tick) || 0) - (candB.priceToCoordinate(b.c) || 0)) || 0.01;
      const binTicks = Math.max(1, Math.ceil(9 / pxTick)), detailed = w >= 40;
      if (!detailed) {                                   /* zoomed out: one delta-coloured box per bar (bar-level aggregate, not price-level cells) */
        const hi = candB.priceToCoordinate(b.h), lo = candB.priceToCoordinate(b.l); if (hi === null || lo === null) continue;
        const vol = b.buy + b.sell, a = 0.2 + 0.6 * Math.min(1, vol ? Math.abs(b.delta) / vol : 0);
        x.fillStyle = b.delta >= 0 ? `rgba(34,197,94,${a})` : `rgba(239,68,68,${a})`; x.fillRect(px - w / 2, hi, w, Math.max(2, lo - hi));
      } else {
        for (const c of footprintCells(b, (p) => candB.priceToCoordinate(p), tick, binTicks)) {
          if (c.y === null || Number.isNaN(c.y)) continue;
          const a = Math.min(0.8, 0.2 + c.tot / 300); x.fillStyle = c.imbalance > 0 ? `rgba(34,197,94,${a})` : c.imbalance < 0 ? `rgba(239,68,68,${a})` : `rgba(100,116,139,${Math.min(a, .45)})`;
          x.fillRect(px - w / 2, c.y, w, Math.max(1, c.h - 1));
          if (c.h >= 9) { x.fillStyle = "#e2e8f0"; x.font = "9px system-ui"; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(`${c.sell} × ${c.buy}`, px, c.y + c.h / 2); }
        }
      }
      if (b.dq.status !== "OK") { x.strokeStyle = b.gap_in_bar ? "#f87171" : "#f59e0b"; x.setLineDash([3, 3]); const hi = candB.priceToCoordinate(b.h), lo = candB.priceToCoordinate(b.l); if (hi !== null && lo !== null) x.strokeRect(px - w / 2, hi, w, lo - hi); x.setLineDash([]); }
      if (selected && selected.t === b.t) { x.fillStyle = "rgba(250,204,21,.10)"; x.fillRect(px - w / 2, 0, w, r.height); }
    }
  }
  /* Overlays redraw only when something that changes the mapping changed (visible range, either price scale, size, selection, new payload).
     Polled once per frame because vertical price-scale zoom/drag and late layout emit no event. */
  let dirtyKey = "", version = 0, fitFrames = 0;
  const redraw = () => { version++; };
  function fit() { if (!payload || !payload.bars.length) return; const n = payload.bars.length; if (n <= 150) A.timeScale().fitContent(); else A.timeScale().setVisibleLogicalRange({ from: n - 120, to: n + 3 }); }
  const stat = (window.__palab = { frames: 0, draws: 0, fits: 0, err: null });
  function frame() {
    stat.frames++;
    try {
    if (fitFrames > 0 && --fitFrames === 0) { fit(); stat.fits++; }
    if (!document.hidden && payload && payload.bars.length) {
      const ref = payload.bars[payload.bars.length - 1].c, vr = B.timeScale().getVisibleLogicalRange(), ra = A.timeScale().getVisibleLogicalRange();
      const k = [version, vr && vr.from, vr && vr.to, ra && ra.from, ra && ra.to, candB.priceToCoordinate(ref), candB.priceToCoordinate(ref + 10), candA.priceToCoordinate(ref), $("chartB").clientWidth, $("chartB").clientHeight, $("chartA").clientWidth, selected && selected.t].join("|");
      if (k !== dirtyKey) { const t1 = performance.now(); dirtyKey = k; drawA(); drawB(); stat.draws++; stat.lastDrawMs = +(performance.now() - t1).toFixed(1); stat.maxDrawMs = Math.max(stat.maxDrawMs || 0, stat.lastDrawMs); }
    }
    } catch (e) { stat.err = String(e && e.stack || e); console.error(e); }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  /* synchronized zoom/pan + crosshair (both charts share the same bar list, so logical ranges are identical) */
  function syncRange(src, dst) { src.timeScale().subscribeVisibleLogicalRangeChange((r) => { if (syncing || !r) return; syncing = true; dst.timeScale().setVisibleLogicalRange(r); syncing = false; redraw(); }); }
  syncRange(A, B); syncRange(B, A);
  function xhair(src, dst, sSeries, dSeries) {
    src.subscribeCrosshairMove((p) => {
      if (!payload) return;
      if (!p.time) { dst.clearCrosshairPosition(); return inspect(null); }
      const bar = payload.bars.find((b) => b.t + IST === p.time); if (bar) { try { dst.setCrosshairPosition(bar.c, p.time, dSeries); } catch (e) { /* api absent */ } inspect(bar.t); }
    });
  }
  xhair(A, B, candA, candB); xhair(B, A, candB, candA);

  function inspect(t) {
    const el = $("inspect"); if (!payload) { el.textContent = ""; return; }
    if (t === null) { el.textContent = "hover a bar"; return; }
    const b = payload.bars.find((x) => x.t === t), f = payload.footprint.bars.find((x) => x.t === t);
    const ev = allEvents(payload).filter((e) => e.t === t);
    let s = `${istFull(t)} · O ${fmt(b.o)} H ${fmt(b.h)} L ${fmt(b.l)} C ${fmt(b.c)}${b.closed ? "" : " (bar still open)"}`;
    if (f) s += `\nbuy* ${f.buy} sell* ${f.sell} unknown ${f.unknown} Δ* ${f.delta} CVDseg* ${f.cvd_segment} | ticks ${f.n_ticks} quote-rule ${fmt((f.quote_share || 0) * 100, 0)}% spread ${fmt(f.spread_mean)} | ${f.side_source} | DQ ${dqBadge(f.dq)}\n(* inferred, not broker-confirmed)`;
    for (const e of ev) s += `\n• ${e.kind} @ ${fmt(e.price)} — ${e.rule} — ${e.status}${e.confirmed_t ? " (known from " + istHM(e.confirmed_t) + ")" : ""}`;
    el.textContent = s;
  }

  function setState(st) { const b = $("banner"); b.className = "banner " + st.kind; b.textContent = st.text; b.hidden = st.kind === "ok"; }

  function render(p) {
    const t0 = performance.now(); payload = p; const st = describeState(p); setState(st);
    $("src").textContent = `${p.meta.symbol} ${p.meta.kind} · ${p.meta.tf_sec / 60}m · price: ${p.meta.price_source}`;
    const f = p.status.feed; $("feed").textContent = f ? `${f.mode}${f.age_sec != null ? " · last tick " + Math.round(f.age_sec) + "s ago" : ""}` : "no feed";
    $("dq").textContent = "DQ " + Object.entries(p.status.data_quality.bars_by_dq || {}).map(([k, v]) => k + ":" + v).join(" ");
    $("regime").textContent = p.pa ? p.pa.regime.label + (p.pa.regime.no_trade ? " · NO TRADE" : "") : "—";
    candA.setData(candleData(p)); candB.setData(candleData(p));
    mk.setMarkers(buildMarkers(p, show));
    lines.forEach((l) => candA.removePriceLine(l)); lines = [];
    if (p.pa) {
      const lastDay = p.bars.length ? p.bars[p.bars.length - 1].day : null;
      for (const l of p.pa.levels) if (l.day === lastDay) lines.push(candA.createPriceLine({ price: l.price, color: "#64748b", lineStyle: 2, lineWidth: 1, title: l.type, axisLabelVisible: false }));
      if (p.pa.opening_range) for (const [k, v] of [["OR-H", p.pa.opening_range.high], ["OR-L", p.pa.opening_range.low]]) lines.push(candA.createPriceLine({ price: v, color: "#eab308", lineStyle: 3, lineWidth: 1, title: k, axisLabelVisible: false }));
      if (vwapS) { A.removeSeries(vwapS); vwapS = null; }
      if (p.pa.vwap) { vwapS = A.addSeries(LW.LineSeries, { color: "#e879f9", lineWidth: 1, lastValueVisible: false, priceLineVisible: false }); vwapS.setData(p.pa.vwap.map((v) => ({ time: v.t + IST, value: v.v }))); }
      $("vwapnote").textContent = p.pa.vwap_note || "";
    }
    const fa = p.footprint.available; $("fpmsg").hidden = fa; $("fpmsg").textContent = fa ? "" : "Footprint unavailable: " + p.footprint.reason;
    if (fa && deltaS) { deltaS.setData(deltaSeries(p)); cvdS.setData(cvdSeries(p)); } else if (deltaS) { deltaS.setData([]); cvdS.setData([]); }
    renderSignals(); renderEvents();
    fitFrames = 4;
    redraw();
    if (window.__palab) window.__palab.renderMs = Math.round(performance.now() - t0);
  }

  function renderSignals() {
    const tb = $("sigs"); tb.replaceChildren(); if (!payload) return;
    const list = filterSignals(payload, $("ftype").value, Number($("fside").value));
    if (!list.length) { const tr = tb.insertRow(); const td = tr.insertCell(); td.colSpan = 7; td.textContent = "no signals for this selection (NO TRADE)"; return; }
    for (const s of list) {
      const tr = tb.insertRow(); tr.className = selected && selected.id === s.id ? "sel" : "";
      const cells = [istHM(s.t), s.type, s.side > 0 ? "LONG" : "SHORT", fmt(s.entry), fmt(s.stop), s.t2 !== undefined ? fmt(s.t2) : "—", s.combined ? s.combined.state + " · " + s.combined.of_status : "—"];
      cells.forEach((v) => { tr.insertCell().textContent = v; });
      tr.onclick = () => select(s);
    }
  }
  function renderEvents() {
    const tb = $("evs"); tb.replaceChildren(); if (!payload) return;
    for (const e of allEvents(payload).slice(-200).reverse()) {
      const tr = tb.insertRow(); tr.className = e.status === "PROVISIONAL" ? "prov" : "";
      [istHM(e.t), e.kind, fmt(e.price), e.rule, e.status].forEach((v) => { tr.insertCell().textContent = v; });
      tr.onclick = () => select({ t: e.t, id: e.id });
    }
  }
  function select(s) {
    selected = s;
    lines.filter((l) => l._sel).forEach((l) => { candA.removePriceLine(l); }); lines = lines.filter((l) => !l._sel);
    if (s.entry !== undefined) for (const [k, v, c] of [["ENTRY", s.entry, "#e2e8f0"], ["STOP", s.stop, "#ef4444"], ["T1", s.t1, "#22c55e"], ["T2", s.t2, "#16a34a"]]) if (v !== undefined) { const l = candA.createPriceLine({ price: v, color: c, lineStyle: 2, lineWidth: 1, title: k, axisLabelVisible: true }); l._sel = true; lines.push(l); }
    const i = payload.bars.findIndex((b) => b.t === s.t); if (i >= 0) A.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - 30), to: i + 30 });
    inspect(s.t); renderSignals(); redraw();
  }

  async function load() {
    clearTimeout(timer); setState(describeState(null)); $("banner").hidden = false;
    const q = new URLSearchParams({ symbol: $("symbol").value, date: $("date").value, tf: $("tf").value, source: $("source").value, days: $("days").value });
    try {
      const r = await fetch("/api/pa_of_lab/chart?" + q);
      if (!r.ok) throw new Error(r.status + " " + (await r.text()).slice(0, 160));
      render(await r.json());
      const p = payload; if (p.status.feed && p.status.feed.mode === "LIVE_TODAY") timer = setTimeout(() => { if (!document.hidden) load(); else timer = setTimeout(load, 15000); }, 10000);
    } catch (e) { payload = null; setState(describeState(null, e)); candA.setData([]); candB.setData([]); redraw(); }
  }
  async function init() {
    for (const k of Object.keys(show)) { const cb = $("sh_" + k); cb.checked = lsGet("palab_" + k, show[k] ? "1" : "0") === "1"; show[k] = cb.checked; cb.onchange = () => { show[k] = cb.checked; lsSet("palab_" + k, cb.checked ? "1" : "0"); if (payload) mk.setMarkers(buildMarkers(payload, show)); }; }
    try {
      const m = await (await fetch("/api/pa_of_lab/meta")).json(); const syms = [...new Set(m.tick_sources.map((x) => x.symbol))];
      $("symbol").replaceChildren(...["NIFTY"].concat(syms).filter((v, i, a) => a.indexOf(v) === i).map((s) => new Option(s, s)));
      const dates = [...new Set(m.tick_sources.map((x) => x.date))]; const nd = m.tick_sources.filter((x) => x.symbol === "NIFTY").map((x) => x.date); $("date").value = nd[0] || dates[0] || new Date().toISOString().slice(0, 10);
      $("avail").textContent = "tick dates: " + dates.slice(0, 8).join(", ");
    } catch (e) { $("avail").textContent = "meta unavailable: " + e.message; }
    ["symbol", "date", "tf", "source", "days"].forEach((id) => { $(id).onchange = load; });
    ["ftype", "fside"].forEach((id) => { $(id).onchange = renderSignals; });
    document.querySelectorAll("[data-view]").forEach((b) => { b.onclick = () => { document.body.dataset.view = b.dataset.view; lsSet("palab_view", b.dataset.view); setTimeout(() => { A.resize(); B.resize(); redraw(); }, 30); }; });
    document.body.dataset.view = lsGet("palab_view", "stacked");
    new ResizeObserver(redraw).observe($("chartA")); new ResizeObserver(redraw).observe($("chartB"));
    load();
  }
  init();
})();
