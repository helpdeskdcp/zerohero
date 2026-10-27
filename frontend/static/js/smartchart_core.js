// Smart Chart pure logic (no DOM): timeframe/symbol mapping, bar de-duplication, data-status derivation,
// Pine syntax highlighting, HTML escaping. Unit-tested with node (frontend/tests/smartchart.test.js).
(function (root) {
  "use strict";

  const INDICES = new Set(["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]);
  const TF_SEC = { "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Vela timeframe string ('5', '60', 'D', '1h', '5m', '1D') -> API timeframe, or null when the backend cannot serve it.
  function tfToApi(tf) {
    const s = String(tf == null ? "" : tf).trim();
    if (/^\d+$/.test(s)) { const n = Number(s); return { 1: "1m", 3: "3m", 5: "5m", 15: "15m", 30: "30m", 60: "1h" }[n] || null; }
    const m = s.match(/^(\d+)(m|[hHdD])$/);          // lowercase m = minutes; uppercase M (month) is NOT a minute
    if (m) { const k = Number(m[1]) + m[2].toLowerCase(); return TF_SEC[k] ? k : null; }
    if (/^1?[dD]$/.test(s)) return "1d";
    return null;
  }

  // Vela ticker (provider prefix already stripped) -> backend symbol id.
  const symbolToApi = (ticker) => { const t = String(ticker || "").toUpperCase().replace(/^(NSE|BSE):/, ""); return INDICES.has(t) ? t : "NSE:" + t; };

  // Accepts a live candle stream that may repeat or reorder deliveries. 'new' = a bar opened, 'update' = the forming bar
  // changed, 'dup' = identical to what we already have, 'old' = older than the newest bar (never rewinds the chart).
  class BarGate {
    constructor() { this.last = null; }
    accept(b) {
      if (!b || !Number.isFinite(b.time)) return "old";
      if (!this.last || b.time > this.last.time) { this.last = { ...b }; return "new"; }
      if (b.time < this.last.time) return "old";
      if (b.open === this.last.open && b.high === this.last.high && b.low === this.last.low && b.close === this.last.close && b.volume === this.last.volume) return "dup";
      this.last = { ...b };
      return "update";
    }
  }

  // Honest data-status badge from the server's candle metadata. REST polling is never described as a socket.
  function statusFromMeta(meta, nowMs, tfSec, errored) {
    if (errored) return { state: "ERROR", label: "OFFLINE", detail: errored };
    if (!meta) return { state: "NONE", label: "NO DATA", detail: "waiting for first response" };
    const src = "Upstox · REST poll ~" + (meta.live_ttl_sec || 3) + "s";
    if (meta.stale) return { state: "STALE", label: "STALE", detail: src + " · " + (meta.error || "data source error") + " (showing cached bars)" };
    if (!meta.market_open) return { state: "CLOSED", label: "MARKET CLOSED", detail: src + " · last session data" };
    const sinceRefresh = meta.tail_refreshed_ms ? nowMs - meta.tail_refreshed_ms : Infinity;
    const sinceBar = meta.last_bar_open_ms ? nowMs - meta.last_bar_open_ms : Infinity;
    if (sinceBar > 2 * tfSec * 1000 + 60000) return { state: "STALE", label: "STALE", detail: src + " · no new bar for " + Math.round(sinceBar / 1000) + "s while the market is open" };
    if (sinceRefresh > 20000) return { state: "DELAYED", label: "DELAYED", detail: src + " · last refresh " + Math.round(sinceRefresh / 1000) + "s ago" };
    return { state: "LIVE", label: "LIVE (polled)", detail: src };
  }

  // ---- Pine syntax highlighting (display only; the server/PineTS is the real parser) ----------------------------------
  const KW = new Set("if else for to by while switch break continue return var varip const import export method type enum true false and or not in".split(" "));
  const NS = new Set("ta math str array matrix map request strategy input color label line box table linefill polyline chart syminfo barstate timeframe ticker session runtime log display shape location size".split(" "));
  const FN = new Set("indicator strategy library plot plotshape plotchar plotarrow plotbar plotcandle hline fill bgcolor barcolor alert alertcondition nz na fixnan int float bool string".split(" "));
  function highlight(src) {
    const re = /(\/\/[^\n]*)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;
    let out = "", last = 0, m;
    while ((m = re.exec(src))) {
      out += esc(src.slice(last, m.index)); last = m.index + m[0].length;
      const t = esc(m[0]);
      if (m[1]) out += /^\/\/\s*@version/.test(m[1]) ? '<span class="tk-ver">' + t + "</span>" : '<span class="tk-com">' + t + "</span>";
      else if (m[2]) out += '<span class="tk-str">' + t + "</span>";
      else if (m[3]) out += '<span class="tk-num">' + t + "</span>";
      else if (KW.has(m[4])) out += '<span class="tk-kw">' + t + "</span>";
      else if (NS.has(m[4])) out += '<span class="tk-ns">' + t + "</span>";
      else if (FN.has(m[4])) out += '<span class="tk-fn">' + t + "</span>";
      else out += t;
    }
    return out + esc(src.slice(last));
  }

  const fmtNum = (v, d = 2) => (v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const fmtTime = (ms) => (ms ? new Date(ms).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "—");
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

  // Equity curve -> SVG path (no external chart lib needed for a sparkline).
  function sparkPath(points, w, h, pad = 4) {
    if (!points || points.length < 2) return "";
    let lo = Infinity, hi = -Infinity;
    for (const p of points) { if (p < lo) lo = p; if (p > hi) hi = p; }
    const span = hi - lo || 1;
    const step = (w - 2 * pad) / (points.length - 1);
    return points.map((p, i) => (i ? "L" : "M") + (pad + i * step).toFixed(1) + " " + (h - pad - ((p - lo) / span) * (h - 2 * pad)).toFixed(1)).join(" ");
  }

  const api = { INDICES, TF_SEC, esc, tfToApi, symbolToApi, BarGate, statusFromMeta, highlight, fmtNum, fmtTime, cls, sparkPath };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SmartChartCore = api;
})(typeof window !== "undefined" ? window : globalThis);
