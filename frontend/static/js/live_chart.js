// Live chart client for the ZeroHero platform delta protocol (see backend/app/platform/ws/delta_protocol.py).
// Pure logic (ChartFeed) is separated from the browser bootstrap so it can be unit-tested with node.
(function (root) {
  "use strict";

  // Applies snapshot/candle messages to a lightweight-charts series incrementally: series.update() per
  // changed candle, setData() only on a (re)snapshot. A seq gap never corrupts the chart: it is ignored and
  // a resync (fresh snapshot) is requested.
  class ChartFeed {
    constructor({ series, instrument, tf, onResync, onStatus }) {
      this.series = series; this.instrument = instrument; this.tf = tf;
      this.onResync = onResync || function () {}; this.onStatus = onStatus || function () {};
      this.lastSeq = null; this.waiting = true; this.applied = 0; this.resyncs = 0;
    }
    static bar(c) { return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }; }
    apply(msg) {
      if (!msg || msg.i !== this.instrument || msg.tf !== this.tf) return "ignored";
      if (msg.t === "snapshot") {
        this.series.setData(msg.candles.map(ChartFeed.bar));
        this.lastSeq = msg.seq; this.waiting = false; this.onStatus("live");
        return "snapshot";
      }
      if (msg.t === "candle") {
        if (this.lastSeq === null || msg.seq !== this.lastSeq + 1) {   // gap, duplicate or out-of-order
          if (this.lastSeq !== null && msg.seq <= this.lastSeq) return "ignored";   // stale/duplicate delivery
          this.resyncs += 1; this.waiting = true; this.onStatus("resync"); this.onResync();
          return "resync";
        }
        this.series.update(ChartFeed.bar(msg.c));
        this.lastSeq = msg.seq; this.applied += 1;
        return "applied";
      }
      return "ignored";
    }
  }

  function bootstrap() {
    const q = new URLSearchParams(location.search);
    const instrument = q.get("i") || "NSE:NIFTY-IDX", tf = parseInt(q.get("tf") || "60", 10);
    const el = document.getElementById("chart"), status = document.getElementById("status");
    const chart = LightweightCharts.createChart(el, { autoSize: true, layout: { background: { color: "#0b0f14" }, textColor: "#cbd5e1" },
      timeScale: { timeVisible: true, secondsVisible: tf < 60 } });
    const series = chart.addSeries(LightweightCharts.CandlestickSeries);
    let ws, retry = 1000;
    const feed = new ChartFeed({ series, instrument, tf, onStatus: (s) => { status.textContent = instrument + " " + tf + "s: " + s; },
      onResync: () => ws && ws.readyState === 1 && ws.send(JSON.stringify({ t: "resync", i: instrument, tf })) });
    function connect() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(proto + "://" + location.host + "/ws" + (q.get("token") ? "?token=" + encodeURIComponent(q.get("token")) : ""));
      ws.onopen = () => { retry = 1000; status.textContent = "subscribing…"; ws.send(JSON.stringify({ t: "subscribe", i: instrument, tf })); };
      ws.onmessage = (ev) => { try { feed.apply(JSON.parse(ev.data)); } catch (e) { /* ignore non-JSON keepalives */ } };
      ws.onclose = () => { status.textContent = "disconnected, retrying"; feed.lastSeq = null; setTimeout(connect, retry); retry = Math.min(retry * 2, 15000); };
    }
    connect();
    setTimeout(() => { if (feed.waiting) status.textContent = "waiting for platform stream (not enabled on this server yet)"; }, 6000);
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { ChartFeed };
  else root.ZeroHeroLiveChart = { ChartFeed, bootstrap };
})(typeof window !== "undefined" ? window : globalThis);
