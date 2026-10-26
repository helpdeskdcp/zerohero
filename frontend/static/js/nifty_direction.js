// NIFTY 50 constituents direction panel inside the Chart Scanner. Pure helpers (NiftyDirLogic) are unit-testable with node; bootstrap talks to /api/nifty-direction/*.
(function (root) {
  "use strict";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const Logic = {
    esc,
    kindClass(k) { return k === "LIVE" ? "ok" : k === "HISTORICAL" ? "warn" : "bad"; },            // MOCK / NONE look alarming on purpose
    heatColor(chg) {
      if (chg == null) return "rgba(148,163,184,.25)";
      const a = Math.min(Math.abs(chg) / 2, 1) * 0.75 + 0.15;
      return chg >= 0 ? "rgba(52,211,153," + a.toFixed(2) + ")" : "rgba(248,113,113," + a.toFixed(2) + ")";
    },
    fmt(x, d) { return x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(d == null ? 2 : d); },
    blockers(o) {
      const out = [];
      if (!o || o.status === "NO_ADAPTER_CONFIGURED") out.push("No live market-data adapter is configured/tested (use Replay).");
      const h = o && o.health;
      if (h && h.weights_status !== "COMPLETE") out.push("Official per-stock weights " + h.weights_status + ": contribution, breadth and sector scoring are disabled (nothing is estimated).");
      const sc = o && o.score; if (sc && sc.missing) sc.missing.forEach((m) => out.push(m.component + " ← " + m.dependency));
      return out;
    },
    metaLine(meta) {
      if (!meta) return "";
      const lab = { live: "LIVE MARKET", last_close: "LAST MARKET CLOSE", replay: "HISTORICAL REPLAY" }[meta.mode] || String(meta.mode || "").toUpperCase();
      const parts = [lab, "data: " + (meta.data_kind || "NONE"), "tz " + (meta.timezone || "–"), "source " + (meta.source || "–"), "freshness " + (meta.freshness || "–")];
      if (meta.session) parts.push("session " + meta.session);
      if (meta.last_update_utc) parts.push("as of " + meta.last_update_utc.replace("T", " ").slice(0, 19) + "Z");
      if (meta.market_open_now === false) parts.push("NSE closed now");
      return parts.join(" · ") + (meta.note ? " — " + meta.note : "") + (meta.signals ? " · signals: closed-candle only (intrabar provisional " + String(meta.signals.provisional_intrabar).toLowerCase().replace("_", " ") + ")" : "");
    },
    unweightedBreadth(u) {
      if (!u || u.status !== "OK") return "";
      return "<span class='mut'>UNWEIGHTED (counts only)</span> · Adv " + u.advancers + " / Dec " + u.decliners + " / Unch " + u.unchanged + " · " + u.valid + "/50 quotes";
    },
    unweightedSectorRows(u) {
      if (!u || u.status !== "OK") return "";
      return u.sectors.map((s) => "<tr><td>" + esc(s.sector) + "</td><td>" + s.advancers + "/" + s.decliners + "/" + s.unchanged + "</td><td class='" + (s.strength >= 0 ? "ok" : "bad") + "'>" + Logic.fmt(s.median_chg_pct, 2) + "%</td></tr>").join("");
    },
    derivText(d) {
      const f = (d && d.futures) || {}, c = (d && d.chain) || {};
      const age = (x) => (x == null ? "" : " · " + Math.round(x) + "s old");
      const ft = f.available ? "Futures " + esc(f.expiry) + " (" + esc(f.identity) + "): " + esc(f.activity) + " · price " + Logic.fmt(f.price_chg_pct) + "% · OI " + Logic.fmt(f.oi_chg_pct) + "% · basis " + Logic.fmt(f.basis) + age(f.age_sec)
        : "Futures: <span class='warn'>unavailable (" + esc(f.reason) + (f.age_sec != null ? ", " + Math.round(f.age_sec) + "s old > " + f.limit_sec + "s" : "") + ")</span>";
      const ct = c.available ? "Chain " + esc(c.expiry) + " (" + esc(c.identity) + "): PCR " + Logic.fmt(c.pcr) + " (" + esc(String(c.oi_scope || "").toLowerCase().replace("_", "-")) + " OI, " + c.strikes + " strikes) · OI walls " + esc(c.support_strike) + " / " + esc(c.resistance_strike) + " · IV " + Logic.fmt(c.iv_mean == null ? null : c.iv_mean * 100, 1) + "%" + age(c.age_sec)
        : "Chain: <span class='warn'>unavailable (" + esc(c.reason) + (c.age_sec != null ? ", " + Math.round(c.age_sec) + "s old > " + c.limit_sec + "s" : "") + ")</span>";
      return ft + "<br>" + ct + (d && d.available ? "<br><span class='mut'>OI is read together with price, never alone; bias " + Logic.fmt(d.bias) + "</span>" : "");
    },
    trendExplain(t) {
      if (!t || t.ema_fast == null) return "";
      return " (EMA gap " + Logic.fmt(Math.abs(t.ema_fast - t.ema_slow), 2) + " vs neutral band " + Logic.fmt(t.neutral_band, 2) + ")";
    },
    heatmapHtml(rows) {
      return (rows || []).map((r) => '<div class="nd-cell" title="' + esc(r.symbol + " · " + r.sector + " · w=" + (r.weight_pct == null ? "n/a" : r.weight_pct) + " · " + r.status) + '" style="background:' + Logic.heatColor(r.chg_pct) + '"><b>' + esc(r.symbol) + "</b><br>" + (r.chg_pct == null ? esc(r.status) : Logic.fmt(r.chg_pct) + "%") + "</div>").join("");
    },
    scoreRows(sc) {
      if (!sc) return [];
      return Object.entries(sc.components || {}).map(([k, v]) => ({ k, v })).concat((sc.missing || []).map((m) => ({ k: m.component, v: null, why: m.dependency })));
    },
  };
  function bootstrap() {
    const $ = (id) => document.getElementById(id), j = (u) => fetch(u, { credentials: "same-origin" }).then((r) => r.json());
    async function load() {
      const mode = $("ndMode").value, asOf = $("ndAsOf").value.trim();
      $("ndStatus").textContent = "loading…";
      const url = "/api/nifty-direction/overview?mode=" + mode + (mode === "replay" ? "&as_of=" + encodeURIComponent(asOf) : "");
      let o; try { const r = await fetch(url, { credentials: "same-origin" }); o = await r.json(); if (r.status === 401) { $("ndStatus").textContent = "not signed in (401): open this page through your authenticated session"; return; } } catch (e) { $("ndStatus").textContent = "error: " + e; return; }
      if (o.detail && !o.status) { $("ndStatus").textContent = String(o.detail); $("ndMeta").textContent = ""; return; }
      $("ndMeta").textContent = Logic.metaLine(o.meta);
      const kind = o.data_kind || "NONE";
      $("ndKind").className = "chip " + Logic.kindClass(kind); $("ndKind").textContent = "data: " + kind;
      $("ndBlock").innerHTML = Logic.blockers(o).map((b) => "<div class='warn'>⚠ " + esc(b) + "</div>").join("");
      if (o.status === "NO_ADAPTER_CONFIGURED") { $("ndStatus").textContent = ""; return; }
      $("ndStatus").textContent = new Date(o.ts * 1000).toLocaleTimeString();
      const sc = o.score || {}, t = o.trend15 || {}, e = o.entry5 || {};
      $("ndOverview").innerHTML = "<b>" + esc(sc.side || "NO SIDE") + "</b> · score " + Logic.fmt(sc.total, 1) + " (" + esc(sc.grade) + ") · <span class='" + (sc.actionable ? "ok" : "warn") + "'>" + (sc.actionable ? "ACTIONABLE (paper)" : "NOT ACTIONABLE — " + esc(sc.reason)) + "</span><br>15m: " + esc(t.state) + (t.reason ? " (" + esc(t.reason) + ")" : "") + Logic.trendExplain(t) + " · 5m: " + esc(e.signal || "none") + " (" + esc(e.reason) + ") · spot " + Logic.fmt(o.spot);
      $("ndHeat").innerHTML = Logic.heatmapHtml(o.heatmap);
      const snap = o.snapshot || {};
      const uw = snap.unweighted, uwb = Logic.unweightedBreadth(uw);
      $("ndBreadth").innerHTML = snap.status === "OK" ? "Adv " + snap.breadth.advancers + " / Dec " + snap.breadth.decliners + " · weighted adv " + Logic.fmt(snap.breadth.weighted_adv * 100, 0) + "%" : (uwb ? uwb + "<br><span class='warn'>weighted breadth disabled: " + esc(snap.reason || "no snapshot") + "</span>" : "<span class='warn'>disabled: " + esc(snap.reason || "no snapshot") + (uw && uw.reason ? " / " + esc(uw.reason) : "") + "</span>");
      $("ndSectors").innerHTML = snap.status !== "OK" && uw && uw.status === "OK" ? Logic.unweightedSectorRows(uw) + "<tr><td colspan=3 class='mut'>unweighted: adv/dec/unch and median % change; weighted contribution needs verified weights</td></tr>" : snap.status === "OK" ? snap.sectors.map((s) => "<tr><td>" + esc(s.sector) + "</td><td>" + Logic.fmt(s.weight_pct, 1) + "</td><td class='" + (s.contrib >= 0 ? "ok" : "bad") + "'>" + Logic.fmt(s.contrib, 3) + "</td></tr>").join("") : "<tr><td colspan=3 class='warn'>disabled (weights)</td></tr>";
      $("ndTop").innerHTML = snap.status === "OK" ? snap.top.map((r) => "<tr><td>" + esc(r.symbol) + "</td><td>" + Logic.fmt(r.weight_pct, 2) + "</td><td>" + Logic.fmt(r.chg_pct) + "%</td><td class='" + (r.contrib >= 0 ? "ok" : "bad") + "'>" + Logic.fmt(r.contrib, 3) + "</td></tr>").join("") : "<tr><td colspan=4 class='warn'>disabled: verified official weights required</td></tr>";
      $("ndDeriv").innerHTML = Logic.derivText(o.derivatives);
      $("ndReasons").innerHTML = Logic.scoreRows(sc).map((r) => "<tr><td>" + esc(r.k) + "</td><td>" + (r.v == null ? "<span class='warn'>missing: " + esc(r.why) + "</span>" : Logic.fmt(r.v, 1)) + "</td></tr>").join("");
      const h = o.health || {}; $("ndHealth").innerHTML = "kind " + esc(h.data_kind) + " · caps " + esc((h.capabilities || []).join(",")) + " · valid quotes " + h.quotes_valid + "/" + h.constituents + " · weight coverage " + Logic.fmt((h.weight_coverage || 0) * 100, 0) + "% · weights " + esc(h.weights_status) + " · csv " + esc((h.csv_sha256 || "").slice(0, 10)) + (Object.keys(h.quotes_invalid || {}).length ? "<br>invalid: " + esc(JSON.stringify(h.quotes_invalid).slice(0, 300)) : "");
    }
    $("ndLoad").onclick = load; $("ndMode").onchange = () => { $("ndAsOf").style.display = $("ndMode").value === "replay" ? "" : "none"; if ($("ndMode").value !== "replay") load(); }; $("ndMode").onchange();
  }
  const api = { Logic, bootstrap }; if (typeof module !== "undefined" && module.exports) module.exports = api; else root.NiftyDir = api;
})(typeof window !== "undefined" ? window : globalThis);
