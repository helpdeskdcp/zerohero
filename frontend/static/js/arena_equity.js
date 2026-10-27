/* Arena equity ticket (NSE cash, intraday paper): BUY or SELL (short) N shares. Live quote line, auto-filled entry, one-click SL / targets, then POST /api/arena/public/submit with instrument=EQUITY.
   Pure helpers (eqValidate, eqAutoLevels, eqQuoteLine) are exported for node tests; the server re-validates everything. */
"use strict";
const EQ_MODES = ["NEAR", "LIMIT", "ABOVE", "MARKET"], EQ_SYMBOL = /^[A-Z0-9&-]{1,20}$/;
const eqNum = (x) => (x === "" || x === null || x === undefined ? null : Number(x));
const eqFmt = (x, n = 2) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "–" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: n }));

/* form -> {ok, errors[], payload}; mirrors submit.validate_equity */
function eqValidate(v, maxNotional = 2500000) {
  const err = [], sym = String(v.symbol || "").trim().toUpperCase(), side = String(v.side || "BUY").toUpperCase(), mode = String(v.entry_mode || "NEAR").toUpperCase();
  if (!EQ_SYMBOL.test(sym)) err.push("Enter the NSE symbol, e.g. RELIANCE");
  if (side !== "BUY" && side !== "SELL") err.push("Side must be BUY or SELL");
  if (!EQ_MODES.includes(mode)) err.push("Choose an entry mode");
  const entry = eqNum(v.entry), sl = eqNum(v.sl), qty = eqNum(v.qty);
  if (!(entry > 0)) err.push("Entry price must be > 0");
  if (!(Number.isInteger(qty) && qty >= 1 && qty <= 100000)) err.push("Quantity: whole number of shares, 1 to 100000");
  else if (entry > 0 && qty * entry > maxNotional) err.push("Order value is above the paper limit of ₹" + eqFmt(maxNotional, 0));
  let tg = String(v.targets || "").split(",").map((x) => x.trim()).filter(Boolean).map(Number);
  if (tg.some((x) => !(x > 0))) err.push("Targets: comma separated numbers > 0"); tg = [...new Set(tg)].sort((a, b) => (side === "SELL" ? b - a : a - b));
  if (entry > 0) {
    if (side === "BUY") { if (sl !== null && !(sl > 0 && sl < entry)) err.push("BUY: stop-loss must be below the entry"); if (tg.some((x) => x <= entry)) err.push("BUY: every target must be above the entry"); }
    else { if (sl !== null && !(sl > entry)) err.push("SELL: stop-loss must be above the entry"); if (tg.some((x) => !(x > 0 && x < entry))) err.push("SELL: every target must be below the entry"); }
  }
  if (!v.confirmNotEntered) err.push("Tick “I have not entered this trade yet”");
  const payload = { instrument: "EQUITY", underlying: sym, side, entry_mode: mode, entry, qty, targets: tg, note: String(v.note || "").slice(0, 300) };
  if (sl !== null) payload.sl = sl;
  return { ok: err.length === 0, errors: err, payload };
}
/* one-click levels: stop-loss risk% and targets as multiples of the risk, on the right side of the entry */
function eqAutoLevels(side, entry, riskPct = 0.5, rr = [1, 2]) {
  const e = Number(entry); if (!(e > 0)) return null; const r = e * riskPct / 100, sgn = side === "SELL" ? -1 : 1, rd = (x) => Math.round(x / 0.05) * 0.05;
  const f = (x) => Number(rd(x).toFixed(2));
  return { sl: f(e - sgn * r), targets: rr.map((k) => f(e + sgn * k * r)) };
}
function eqQuoteLine(q) {
  if (!q || !q.last) return "No live quote yet: the symbol is now under the 3-second capture, quotes start within seconds.";
  const l = q.last, age = q.age_s != null ? ` · ${Math.round(q.age_s)}s old` : "";
  return `${q.symbol} (${q.name}) · LTP ${eqFmt(l.ltp)} · bid ${eqFmt(l.bid)} / ask ${eqFmt(l.ask)}${age}`;
}
/* server-sent events: split a text buffer into complete events (JSON payloads of "data:" lines) and the unfinished rest */
function eqParseSse(buf) {
  const events = []; let rest = buf, i;
  while ((i = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, i); rest = rest.slice(i + 2);
    const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
    if (data) { try { events.push(JSON.parse(data)); } catch (e) { /* ignore a malformed block */ } }
  }
  return { events, rest };
}
/* price of a symbol's tick for the entry field: the live last-traded price */
function eqTickPrice(t) { return t && t.ltp != null && t.ltp > 0 ? Number(t.ltp) : null; }
function eqSrcLabel(t) { return !t || t.src == null ? "no quote" : t.src === "ws" ? "live push" : "3 s tape" + (t.age_s != null ? ` · ${t.age_s}s old` : ""); }
if (typeof module !== "undefined") module.exports = { eqValidate, eqAutoLevels, eqQuoteLine, eqParseSse, eqTickPrice, eqSrcLabel, EQ_MODES };


if (typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id); if (!$("eqCard")) return;
  const tokenOf = () => { try { return sessionStorage.getItem("arena_token") || ""; } catch (e) { return ""; } };
  const headers = () => ({ "X-Arena-Token": tokenOf(), "Content-Type": "application/json" });
  async function api(path, opts = {}) {
    const r = await fetch("/api/arena/public" + path, { ...opts, headers: headers(), cache: "no-store" });
    let j = null; try { j = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok) throw new Error((j && j.detail) || ("HTTP " + r.status)); return j;
  }
  const msg = (text, bad) => { const e = $("eqMsg"); e.textContent = text; e.className = "msg " + (bad ? "bad" : "ok"); e.hidden = !text; };
  const val = (id) => $(id).value, esc = (x) => String(x).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let sym = "", follow = true, maxNotional = 2500000, lastTick = null, lastLtp = null, stream = null, pollTimer = null, searchGen = 0, hits = [], hi = -1, debounce = null;
  const read = () => ({ symbol: sym || val("eqSym"), side: val("eqSide"), qty: val("eqQty"), entry_mode: val("eqMode"), entry: val("eqEntry"), sl: val("eqSl"), targets: val("eqTg"), note: val("eqNote"), confirmNotEntered: $("eqConfirm").checked });

  /* ------------------------------------------------------------ live price (SSE stream, polling fallback) */
  function paint(t) {
    lastTick = t; const p = eqTickPrice(t); $("eqLive").hidden = !sym;
    const dot = $("eqDot"); dot.className = "eq-dot " + (t && t.src === "ws" ? "ws" : t && t.src === "db" ? "db" : "off");
    if (p != null) { const el = $("eqLtp"); el.textContent = p.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); if (lastLtp != null && p !== lastLtp) { el.className = "eq-ltp " + (p > lastLtp ? "up" : "dn"); setTimeout(() => (el.className = "eq-ltp"), 350); } lastLtp = p; }
    $("eqBid").textContent = t && t.bid != null ? Number(t.bid).toFixed(2) : "–"; $("eqAsk").textContent = t && t.ask != null ? Number(t.ask).toFixed(2) : "–"; $("eqSrc").textContent = eqSrcLabel(t);
    if (follow && p != null) $("eqEntry").value = p.toFixed(2);                                                 // the entry follows the live price until you type your own
  }
  function setFollow(on) { follow = on; $("eqFollow").textContent = on ? "· following live price" : "· your price (manual)"; $("eqLiveBtn").hidden = on; if (on && lastTick) paint(lastTick); }
  function stopStream() { if (stream) { stream.abort(); stream = null; } if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
  function startPolling(mySym) {
    if (pollTimer) return;
    pollTimer = setInterval(async () => { if (document.hidden || mySym !== sym) return; try { const j = await api("/equity/ticks?symbols=" + encodeURIComponent(mySym)); paint(j.ticks[mySym]); } catch (e) { /* next tick */ } }, 1000);
  }
  async function startStream(mySym) {
    stopStream(); const ctl = new AbortController(); stream = ctl; let fails = 0;
    while (!ctl.signal.aborted && mySym === sym) {
      try {
        const r = await fetch("/api/arena/public/equity/stream?symbols=" + encodeURIComponent(mySym), { headers: headers(), signal: ctl.signal, cache: "no-store" });
        if (!r.ok || !r.body) throw new Error("stream " + r.status);
        fails = 0; if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
        for (;;) { const { done, value } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true }); const p = eqParseSse(buf); buf = p.rest; for (const ev of p.events) if (ev.ticks && ev.ticks[mySym] && mySym === sym) paint(ev.ticks[mySym]); }
      } catch (e) { if (ctl.signal.aborted) return; fails += 1; if (fails >= 2) startPolling(mySym); }
      if (ctl.signal.aborted || mySym !== sym) return; await new Promise((res) => setTimeout(res, Math.min(5000, 400 * (fails + 1))));
    }
  }

  /* ------------------------------------------------------------ symbol search (combobox) */
  function closeList() { $("eqList").hidden = true; $("eqSym").setAttribute("aria-expanded", "false"); hi = -1; }
  function renderList() {
    const el = $("eqList"); if (!hits.length) { closeList(); return; }
    el.innerHTML = hits.map((h, i) => `<div class="eq-item${i === hi ? " on" : ""}" role="option" data-i="${i}"><b>${esc(h.symbol)}</b><small>${esc(h.company || "")}</small></div>`).join("");
    el.hidden = false; $("eqSym").setAttribute("aria-expanded", "true");
  }
  async function search(q) {
    const my = ++searchGen; if (!q.trim()) { hits = []; closeList(); return; }
    try { const j = await api("/equity/search?q=" + encodeURIComponent(q.trim()) + "&limit=12"); if (my !== searchGen) return; hits = j.results || []; hi = hits.length ? 0 : -1; renderList(); }
    catch (e) { if (my === searchGen) { hits = []; closeList(); $("eqQuote").textContent = e.message; $("eqQuote").className = "msg bad"; } }
  }
  async function choose(h) {
    sym = h.symbol; $("eqSym").value = h.symbol; closeList(); lastLtp = null; lastTick = null; $("eqName").textContent = h.symbol + (h.company ? " · " + h.company : ""); setFollow(true); $("eqEntry").value = ""; $("eqQuote").textContent = ""; $("eqLive").hidden = false;
    startStream(h.symbol);                                                                                   // live price starts now
    try { const q = await api("/equity/quote?symbol=" + encodeURIComponent(h.symbol)); maxNotional = q.max_notional_rs || maxNotional; if (q.last && q.last.ltp && follow && !$("eqEntry").value) $("eqEntry").value = Number(q.last.ltp).toFixed(2); } catch (e) { $("eqQuote").textContent = e.message; $("eqQuote").className = "msg bad"; }
  }
  $("eqSym").addEventListener("input", () => { sym = ""; stopStream(); $("eqLive").hidden = true; clearTimeout(debounce); debounce = setTimeout(() => search(val("eqSym")), 80); });
  $("eqSym").addEventListener("focus", () => { if (val("eqSym") && !sym) search(val("eqSym")); });
  $("eqSym").addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); if (hits.length) { hi = (hi + 1) % hits.length; renderList(); } }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); if (hits.length) { hi = (hi - 1 + hits.length) % hits.length; renderList(); } }
    else if (ev.key === "Enter") { if (!$("eqList").hidden && hits[hi]) { ev.preventDefault(); choose(hits[hi]); } }
    else if (ev.key === "Escape") closeList();
  });
  $("eqList").addEventListener("mousedown", (ev) => { const it = ev.target.closest && ev.target.closest(".eq-item"); if (it) { ev.preventDefault(); choose(hits[Number(it.dataset.i)]); } });
  document.addEventListener("click", (ev) => { if (!ev.target.closest || !ev.target.closest(".eq-combo")) closeList(); });

  /* ------------------------------------------------------------ the rest of the ticket */
  $("eqEntry").addEventListener("input", () => setFollow(false));
  $("eqLiveBtn").onclick = () => setFollow(true);
  $("eqAuto").onclick = () => { const e = Number(val("eqEntry")) || eqTickPrice(lastTick); if (!e) return msg("Choose a symbol first", true); const l = eqAutoLevels(val("eqSide"), e); $("eqEntry").value = e; setFollow(false); $("eqSl").value = l.sl; $("eqTg").value = l.targets.join(", "); msg("", false); };
  $("eqGo").onclick = async () => {
    const r = eqValidate(read(), maxNotional); if (!r.ok) return msg(r.errors.join(" · "), true);
    $("eqGo").disabled = true; msg("Sending…", false);
    try { const j = await api("/submit", { method: "POST", body: JSON.stringify(r.payload) });
      msg(`Submitted ${j.id}: ${j.side} ${j.qty} ${j.underlying} — server time ${String(j.server_iso || "").slice(11, 19)} UTC${j.warnings && j.warnings.length ? " · " + j.warnings.join(" · ") : ""}`, false); $("eqConfirm").checked = false; }
    catch (e) { msg(e.message, true); } finally { $("eqGo").disabled = false; }
  };
  setFollow(true);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopStream(); else if (sym) startStream(sym); });                    // no stream while the tab is hidden
  document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { if (b.dataset.tab !== "equity") stopStream(); else if (sym) startStream(sym); }));
})();
