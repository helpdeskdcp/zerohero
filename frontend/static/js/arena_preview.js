/* Arena "Submit" tab: live chart of the contract you have selected (underlying + expiry + strike + CE/PE) with the ZeroHero ZHX Pine script on it.
   Read-only: it only reads /chart and /chart/zhsmc (same endpoints as the Terminal); nothing is submitted from here. Reuses tmZhMarkers / tmZhDashText / TM_ZH_COLORS / tmSnap / IST_SHIFT from arena_terminal.js. */
(function () {
  if (typeof document === "undefined") return;
  const $ = (id) => document.getElementById(id), form = $("f"), box = $("pvBox");
  if (!form || !box || !window.LightweightCharts) return;
  const pane = document.querySelector('[data-pane="submit"]'), LW = window.LightweightCharts, el = $("pvChart");
  let cand = [], srOn = true, meanOn = true, srLines = [], vw = null, em = null, chart = null, cs = null, mk = null, key = "", gen = 0, tf = 1, zhOn = true, last = null, lastZh = null, zhTs = 0, zhLines = [], myLines = [], timer = null, debounce = null;
  const read = () => { try { return sessionStorage.getItem("arena_token") || ""; } catch (e) { return ""; } };
  async function api(path) { const r = await fetch("/api/arena/public" + path, { headers: { "X-Arena-Token": read() }, cache: "no-store" }); if (!r.ok) throw new Error(path.split("?")[0] + " " + r.status); return r.json(); }
  const num = (v) => { const x = parseFloat(String(v || "").replace(",", ".")); return isFinite(x) && x > 0 ? x : null; };
  const sel = () => { const u = form.underlying.value, ex = form.expiry.value, st = form.strike.value, ot = form.option_type.value; return u && ex && st && ot ? { u, ex, st, ot } : null; };
  const q = (s) => `underlying=${encodeURIComponent(s.u)}&expiry=${encodeURIComponent(s.ex)}&strike=${s.st}&option_type=${s.ot}`;
  function ensure() {
    if (chart) return;
    chart = LW.createChart(el, { width: el.clientWidth, height: el.clientHeight, layout: { background: { color: "#0b0f14" }, textColor: "#dbe4f0", fontSize: 12 }, grid: { vertLines: { color: "#18212f" }, horzLines: { color: "#18212f" } },
      rightPriceScale: { borderColor: "#243047", scaleMargins: { top: 0.14, bottom: 0.08 } }, timeScale: { borderColor: "#243047", timeVisible: true, secondsVisible: false, rightOffset: 5 }, localization: { priceFormatter: (p) => p.toFixed(2) } });
    new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth, height: el.clientHeight })).observe(el);
  }
  function fresh() {
    if (cs) { try { chart.removeSeries(cs); } catch (e) { /* ok */ } }
    cs = chart.addSeries(LW.CandlestickSeries, { upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444" });
    mk = LW.createSeriesMarkers(cs, []); zhLines = []; myLines = []; srLines = []; last = null; lastZh = null; cand = [];
    if (vw) { try { chart.removeSeries(vw); } catch (e) { /* ok */ } } if (em) { try { chart.removeSeries(em); } catch (e) { /* ok */ } }
    vw = chart.addSeries(LW.LineSeries, { color: "#facc15", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, title: "VWAP" });
    em = chart.addSeries(LW.LineSeries, { color: "#60a5fa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA20" });
  }
  /* support / resistance (from the candles on the chart, no look-ahead: a swing is a fractal confirmed by 2 later bars): day high / low + the nearest swing levels around the price,
     nearby levels merged. Mean line: VWAP (volume-weighted, from the cumulative volume) and EMA20. Both are guides, not signals. */
  function swings() {
    const hi = [], lo = [], w = 2, n = cand.length;
    for (let i = w; i < n - w; i++) {
      let h = true, l = true; for (let k = 1; k <= w; k++) { if (!(cand[i].high > cand[i - k].high && cand[i].high >= cand[i + k].high)) h = false; if (!(cand[i].low < cand[i - k].low && cand[i].low <= cand[i + k].low)) l = false; }
      if (h) hi.push(cand[i].high); if (l) lo.push(cand[i].low);
    }
    return { hi, lo };
  }
  function drawSr() {
    srLines.forEach((l) => { try { cs.removePriceLine(l); } catch (e) { /* ok */ } }); srLines = []; if (!srOn || cand.length < 6) return;
    const px = cand[cand.length - 1].close, dh = Math.max(...cand.map((c) => c.high)), dl = Math.min(...cand.map((c) => c.low)), sw = swings(), tol = Math.max(px * 0.012, 0.05);
    const near = (arr, above) => { const c = arr.filter((v) => (above ? v > px + tol * 0.2 : v < px - tol * 0.2)).sort((a, b) => (above ? a - b : b - a)); const out = []; for (const v of c) if (!out.some((o) => Math.abs(o - v) < tol)) out.push(v); return out.slice(0, 2); };
    const add = (p, c, t, st) => srLines.push(cs.createPriceLine({ price: p, color: c, lineWidth: st === 0 ? 2 : 1, lineStyle: st, axisLabelVisible: true, title: t }));
    near(sw.hi.concat(sw.lo), true).forEach((v, i) => add(v, "#f97316", "R" + (i + 1), 2)); near(sw.hi.concat(sw.lo), false).forEach((v, i) => add(v, "#06b6d4", "S" + (i + 1), 2));
    if (dh > px) add(dh, "#c084fc", "Day high", 1); if (dl < px) add(dl, "#38bdf8", "Day low", 1);
  }
  function drawMean() {
    if (!vw || !em) return; const on = meanOn && cand.length > 1; const vd = [], ed = []; let pv = 0, vv = 0, pc = 0, e = null; const k = 2 / 21;
    for (const c of cand) {
      const dv = c.cum != null ? Math.max(0, c.cum - pc) : 0; if (c.cum != null) pc = c.cum; const tp = (c.high + c.low + c.close) / 3; pv += tp * dv; vv += dv; if (vv > 0) vd.push({ time: c.time, value: pv / vv });
      e = e == null ? c.close : c.close * k + e * (1 - k); ed.push({ time: c.time, value: e });
    }
    vw.setData(on ? vd : []); em.setData(on ? ed : []);
  }
  function drawGuides() { drawSr(); drawMean(); }
  function drawMine() {                                                                              // the levels typed in the form: ENTRY / SL / targets, so the plan is seen on the chart
    myLines.forEach((l) => { try { cs.removePriceLine(l); } catch (e) { /* ok */ } }); myLines = [];
    const add = (p, c, t) => { if (p) myLines.push(cs.createPriceLine({ price: p, color: c, lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: t })); };
    add(num(form.entry && form.entry.value), "#60a5fa", "YOUR ENTRY"); add(num(form.sl && form.sl.value), "#ef4444", "YOUR SL");
    String((form.targets && form.targets.value) || "").split(",").forEach((x, i) => add(num(x), "#22c55e", "YOUR T" + (i + 1)));
  }
  function drawZh(times) {
    zhLines.forEach((l) => { try { cs.removePriceLine(l); } catch (e) { /* ok */ } }); zhLines = [];
    if (zhOn && lastZh) (lastZh.levels || []).forEach((L) => zhLines.push(cs.createPriceLine({ price: L.price, color: TM_ZH_COLORS[L.head] || "#9ca3af", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "ZHX " + L.title })));
    mk.setMarkers(zhOn && lastZh ? tmZhMarkers(times, lastZh.markers).sort((a, b) => a.time - b.time) : []);
    const d = $("pvDash"); const t = zhOn ? tmZhDashText(lastZh) : ""; d.textContent = t; d.style.display = t ? "block" : "none";
  }
  let times = [];
  async function loadZh(s, my) {
    zhTs = Date.now(); let z; try { z = await api(`/chart/zhsmc?${q(s)}&tf=${tf}`); } catch (e) { z = { markers: [], levels: [], dash: [], error: String(e.message || e) }; }
    if (my !== gen) return; lastZh = z; drawZh(times);
  }
  async function full(s) {
    const my = ++gen; ensure(); fresh(); $("pvTitle").textContent = `${s.u} ${s.st} ${s.ot} · ${s.ex}`; $("pvNote").textContent = "loading…";
    let c; try { c = await api(`/chart?${q(s)}&tf=${tf}`); } catch (e) { if (my === gen) $("pvNote").textContent = "chart not available: " + e.message; return; }
    if (my !== gen) return; cs.setData(c.candles); cand = c.candles.slice(); drawGuides(); last = c.candles.length ? c.candles[c.candles.length - 1] : null; times = c.candles.map((x) => x.time);
    $("pvNote").textContent = c.candles.length ? `${c.candles.length} candles · ${tf}m · live` : "No quotes captured for this contract yet today (new contract or market closed).";
    $("pvLtp").textContent = last ? `LTP ${last.close.toFixed(2)}` : ""; chart.timeScale().fitContent(); drawMine(); if (zhOn) loadZh(s, my);
  }
  async function tick() {
    if (!pane || pane.hidden || document.hidden || !cs) return; const s = sel(); if (!s) return; const my = gen;
    try {
      const c = await api(`/chart?${q(s)}&tf=${tf}` + (last ? `&since=${last.time - IST_SHIFT}` : "")); if (my !== gen) return;
      for (const b of c.candles) { if (!last || b.time >= last.time) { cs.update(b); last = b; } if (!times.length || b.time > times[times.length - 1]) times.push(b.time); const i = cand.findIndex((x) => x.time === b.time); if (i >= 0) cand[i] = b; else if (!cand.length || b.time > cand[cand.length - 1].time) cand.push(b); }
      drawGuides();
      if (last) $("pvLtp").textContent = `LTP ${last.close.toFixed(2)}`;
    } catch (e) { /* next tick */ }
    drawMine(); if (zhOn && Date.now() - zhTs > 20000) loadZh(s, my);                                  // the Pine run is cached 15 s on the server
  }
  function changed() {
    clearTimeout(debounce); debounce = setTimeout(() => { if (!pane || pane.hidden) return; const s = sel(); if (!s) { $("pvNote").textContent = "Choose underlying, expiry, strike and CE / PE to see the live chart."; return; }
      const k = [s.u, s.ex, s.st, s.ot, tf].join("|"); if (k !== key) { key = k; full(s); } else if (cs) drawMine(); }, 350);
  }
  for (const n of ["underlying", "expiry", "strike", "option_type"]) form[n].addEventListener("change", changed);
  for (const n of ["entry", "sl", "targets"]) if (form[n]) form[n].addEventListener("input", () => { if (cs) drawMine(); });
  new MutationObserver(changed).observe(form.expiry, { childList: true }); new MutationObserver(changed).observe(form.strike, { childList: true });
  document.querySelectorAll("[data-pvtf]").forEach((b) => b.addEventListener("click", () => { tf = +b.dataset.pvtf; document.querySelectorAll("[data-pvtf]").forEach((x) => x.classList.toggle("on", x === b)); key = ""; changed(); }));
  $("pvSr").addEventListener("change", (e) => { srOn = e.target.checked; drawSr(); }); $("pvMean").addEventListener("change", (e) => { meanOn = e.target.checked; drawMean(); });
  $("pvZh").addEventListener("change", (e) => { zhOn = e.target.checked; if (zhOn && !lastZh) { const s = sel(); if (s) loadZh(s, gen); } else drawZh(times); });
  document.querySelectorAll(".tabs button, nav button, [data-tab]").forEach((b) => b.addEventListener("click", () => setTimeout(changed, 100)));
  timer = setInterval(tick, 3000); setTimeout(changed, 800);
})();
