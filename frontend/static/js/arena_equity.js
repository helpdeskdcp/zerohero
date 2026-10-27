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
if (typeof module !== "undefined") module.exports = { eqValidate, eqAutoLevels, eqQuoteLine, EQ_MODES };

if (typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id); if (!$("eqCard")) return;
  const tokenOf = () => { try { return sessionStorage.getItem("arena_token") || ""; } catch (e) { return ""; } };
  async function api(path, opts = {}) {
    const r = await fetch("/api/arena/public" + path, { ...opts, headers: { "X-Arena-Token": tokenOf(), "Content-Type": "application/json" }, cache: "no-store" });
    let j = null; try { j = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok) throw new Error((j && j.detail) || ("HTTP " + r.status)); return j;
  }
  const msg = (text, bad) => { const e = $("eqMsg"); e.textContent = text; e.className = "msg " + (bad ? "bad" : "ok"); e.hidden = !text; };
  let lastSym = "", entryTouched = false, timer = null, maxNotional = 2500000, quote = null;
  const val = (id) => $(id).value;
  const read = () => ({ symbol: val("eqSym"), side: val("eqSide"), qty: val("eqQty"), entry_mode: val("eqMode"), entry: val("eqEntry"), sl: val("eqSl"), targets: val("eqTg"), note: val("eqNote"), confirmNotEntered: $("eqConfirm").checked });
  function px() { const l = quote && quote.last; if (!l) return null; const s = val("eqSide"); return s === "SELL" ? (l.bid || l.ltp) : (l.ask || l.ltp); }
  async function refreshQuote(force) {
    const sym = val("eqSym").trim().toUpperCase(); if (!EQ_SYMBOL.test(sym)) { $("eqQuote").textContent = ""; lastSym = ""; return; }
    if (sym !== lastSym) { entryTouched = false; lastSym = sym; if (force !== false) $("eqEntry").value = ""; }
    try { quote = await api("/equity/quote?symbol=" + encodeURIComponent(sym)); maxNotional = quote.max_notional_rs || maxNotional; $("eqQuote").textContent = eqQuoteLine(quote); $("eqQuote").className = "mut";
      const p = px(); if (p && !entryTouched && val("eqMode") !== "LIMIT" && val("eqMode") !== "ABOVE") $("eqEntry").value = p; }
    catch (e) { quote = null; $("eqQuote").textContent = e.message; $("eqQuote").className = "msg bad"; }
  }
  function schedule() { clearTimeout(timer); timer = setTimeout(() => refreshQuote(), 450); }
  $("eqSym").addEventListener("input", schedule); $("eqSide").addEventListener("change", () => { entryTouched = false; refreshQuote(false); }); $("eqMode").addEventListener("change", () => { entryTouched = false; refreshQuote(false); });
  $("eqEntry").addEventListener("input", () => { entryTouched = true; });
  $("eqAuto").onclick = () => { const e = Number(val("eqEntry")) || px(); if (!e) return msg("Enter a symbol (and entry) first", true); const l = eqAutoLevels(val("eqSide"), e); $("eqEntry").value = e; $("eqSl").value = l.sl; $("eqTg").value = l.targets.join(", "); msg("", false); };
  $("eqGo").onclick = async () => {
    const r = eqValidate(read(), maxNotional); if (!r.ok) return msg(r.errors.join(" · "), true);
    $("eqGo").disabled = true; msg("Sending…", false);
    try { const j = await api("/submit", { method: "POST", body: JSON.stringify(r.payload) });
      msg(`Submitted ${j.id}: ${j.side} ${j.qty} ${j.underlying} — server time ${String(j.server_iso || "").slice(11, 19)} UTC${j.warnings && j.warnings.length ? " · " + j.warnings.join(" · ") : ""}`, false); $("eqConfirm").checked = false; }
    catch (e) { msg(e.message, true); } finally { $("eqGo").disabled = false; }
  };
  setInterval(() => { if (!document.hidden && $("eqCard").offsetParent !== null && EQ_SYMBOL.test(val("eqSym").trim().toUpperCase())) refreshQuote(false); }, 3000);
})();
