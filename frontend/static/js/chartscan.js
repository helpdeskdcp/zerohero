// ZeroHero chart-scan page: Chartink-style results table + candlestick chart with the selected scan's signals marked.
// Pure helpers (ChartScanLogic) are separated from the browser bootstrap so they can be unit-tested with node.
(function (root) {
  "use strict";

  const Logic = {
    // lightweight-charts markers from the API's marker list: LONG = green arrow under the bar, SHORT = red arrow above it. Markers at times that are not on the chart are dropped.
    buildMarkers(markers, candles) {
      const times = new Set((candles || []).map((c) => c.time));
      return (markers || []).filter((m) => times.has(m.time)).sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)).map((m) => (m.side === "SHORT"
        ? { time: m.time, position: "aboveBar", color: "#f87171", shape: "arrowDown", text: "S" }
        : { time: m.time, position: "belowBar", color: "#34d399", shape: "arrowUp", text: "L" }));
    },
    volumeData(candles) {
      return (candles || []).map((c) => ({ time: c.time, value: c.volume || 0, color: c.close >= c.open ? "rgba(52,211,153,0.35)" : "rgba(248,113,113,0.35)" }));
    },
    verdictClass(v) { return v === "GO-SHADOW" ? "ok" : v === "NO-GO" ? "bad" : "warn"; },
    fmtTime(t, tf) {
      if (tf === "1d") return String(t);
      const d = new Date(t * 1000), p = (x) => String(x).padStart(2, "0");
      return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate()) + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes());
    },
    backtestRows(bt) {
      if (!bt || !bt.horizons) return [];
      return Object.entries(bt.horizons).map(([h, v]) => ({ h, n: v.n, mean: v.mean_net_bps == null ? null : Math.round(v.mean_net_bps * 10) / 10, hit: v.hit == null ? null : Math.round(v.hit * 100) }));
    },
  };

  function bootstrap() {
    const $ = (id) => document.getElementById(id);
    const q = new URLSearchParams(location.search);
    const st = { tf: q.get("tf") || "1d", inst: (q.get("i") || "NIFTY").toUpperCase(), scan: q.get("scan") || "BRK20_UP", bars: parseInt(q.get("bars") || "250", 10) };
    let CORE = ["NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM"], STOCKS = [];
    const chart = LightweightCharts.createChart($("chart"), { autoSize: true, layout: { background: { color: "transparent" }, textColor: "#cbd5e1" }, grid: { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
      rightPriceScale: { borderColor: "#334155" }, timeScale: { borderColor: "#334155", timeVisible: true } });
    const candle = chart.addSeries(LightweightCharts.CandlestickSeries, { upColor: "#34d399", downColor: "#f87171", borderVisible: false, wickUpColor: "#34d399", wickDownColor: "#f87171" });
    const vol = chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    let markerApi = null;
    const j = (u) => fetch(u, { credentials: "same-origin" }).then((r) => r.json());
    const fill = (sel, opts, cur) => { sel.innerHTML = opts.map((o) => `<option value="${o.v}"${o.v === cur ? " selected" : ""}>${o.t}</option>`).join(""); };

    async function loadScans() {
      const r = await j("/api/scanner/results?tf=" + st.tf);
      fill($("scan"), r.scans.map((s) => ({ v: s.name, t: s.name + " (" + s.side + ")" })), st.scan);
      if (!r.scans.some((s) => s.name === st.scan)) st.scan = r.scans[0].name, $("scan").value = st.scan;
      const rows = r.results.filter((x) => !x.error);
      $("res").innerHTML = rows.length ? rows.map((x) => `<tr data-i="${x.inst}" data-s="${x.scan}"><td>${x.inst}</td><td>${x.scan}</td><td class="${x.side === "LONG" ? "ok" : "bad"}">${x.side}</td><td>${Logic.fmtTime(x.bar, st.tf).slice(0, 16)}</td><td>${x.close.toFixed(2)}</td></tr>`).join("")
        : '<tr><td colspan="5" class="mut">no scan fires on the latest bar</td></tr>';
      document.querySelectorAll("#res tr[data-i]").forEach((tr) => tr.addEventListener("click", () => { st.inst = tr.dataset.i; st.scan = tr.dataset.s; $("inst").value = st.inst; $("scan").value = st.scan; loadChart(); }));
    }

    async function loadChart() {
      $("status").textContent = "loading…";
      const d = await j(`/api/scanner/chart?inst=${st.inst}&tf=${st.tf}&scan=${encodeURIComponent(st.scan)}&bars=${st.bars}`);
      if (d.error) { $("status").textContent = d.error; return; }
      candle.setData(d.candles.map((c) => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
      vol.setData(Logic.volumeData(d.candles));
      const m = Logic.buildMarkers(d.markers, d.candles);
      if (markerApi) markerApi.setMarkers(m); else markerApi = LightweightCharts.createSeriesMarkers(candle, m);
      chart.timeScale().fitContent();
      $("status").textContent = `${d.inst} ${st.tf === "1d" ? "daily" : "5-minute (front-month future, IST)"} · ${d.bars} bars · last ${Logic.fmtTime(d.last_bar, st.tf)} · ${m.length} signal${m.length === 1 ? "" : "s"} of ${st.scan}`;
      $("expr").textContent = d.expr || (d.side ? d.side + " variant of the owner's 5-minute scan" : "");
      const bt = d.backtest, rows = Logic.backtestRows(bt);
      $("bt").innerHTML = bt ? `<span class="chip ${Logic.verdictClass(bt.verdict)}">${bt.verdict || "–"}</span> back-test net bp per trade by horizon: ` + rows.map((r) => `<span class="mut">${r.h}${st.tf === "1d" ? "d" : " bars"}</span> ${r.mean == null ? "–" : r.mean} (n ${r.n}, hit ${r.hit == null ? "–" : r.hit}%)`).join(" · ") : '<span class="mut">no back-test result for this scan yet</span>';
      $("hits").innerHTML = d.markers.slice().reverse().slice(0, 40).map((x) => `<button data-t="${x.time}">${Logic.fmtTime(x.time, st.tf)}</button>`).join(" ") || '<span class="mut">no signals in this window</span>';
      document.querySelectorAll("#hits button").forEach((b) => b.addEventListener("click", () => { const t = isNaN(b.dataset.t) ? b.dataset.t : parseInt(b.dataset.t, 10); chart.timeScale().setVisibleRange(st.tf === "1d" ? { from: shift(t, -40), to: shift(t, 10) } : { from: t - 3600 * 5, to: t + 3600 * 2 }); }));
    }
    const shift = (isoDay, days) => { const d = new Date(isoDay + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };

    const fillInst = () => fill($("inst"), (st.tf === "1d" ? CORE.concat(STOCKS) : CORE).map((x) => ({ v: x, t: CORE.includes(x) ? x : x + " (stock)" })), st.inst);
    fill($("tf"), [{ v: "1d", t: "Daily" }, { v: "5m", t: "5-minute (futures)" }], st.tf);
    $("tf").addEventListener("change", async () => { st.tf = $("tf").value; st.scan = st.tf === "1d" ? "BRK20_UP" : "FULL_BULL"; if (st.tf === "5m" && !CORE.includes(st.inst)) st.inst = "NIFTY"; fillInst(); await loadScans(); loadChart(); });
    $("inst").addEventListener("change", () => { st.inst = $("inst").value; loadChart(); });
    $("scan").addEventListener("change", () => { st.scan = $("scan").value; loadChart(); });
    $("refresh").addEventListener("click", async () => { await loadScans(); loadChart(); });
    j("/api/scanner/instruments").then((r) => { CORE = r.core; STOCKS = r.stocks; fillInst(); }).catch(() => fillInst()).then(loadScans).then(loadChart).catch((e) => { $("status").textContent = "error: " + e; });
  }

  const api = { Logic, bootstrap };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.ChartScan = api;
})(typeof window !== "undefined" ? window : globalThis);
