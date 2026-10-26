/* Institution arena page. Pure helpers are exported for node tests; DOM wiring runs only in a browser. All API calls carry the arena token (header X-Arena-Token); no dashboard login. */
"use strict";
const ALLOWED = ["NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM"];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---- live watch (broker style): WAITING -> ACTIVE with ltp and open P&L, refreshed every 3 s without reloading the page ---- */
const lastLtp = {}; let liveTimer = null, liveBusy = false;
function liveRows(d) {
  const f = (x, n = 2) => (x === null || x === undefined ? "–" : Number(x).toLocaleString("en-IN", { maximumFractionDigits: n }));
  const pn = (x) => (x === null || x === undefined ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + "₹" + Math.abs(Math.round(x)).toLocaleString("en-IN"));
  const cls = (x) => (x > 0 ? "pnlpos" : x < 0 ? "pnlneg" : "");
  if (!d.rows.length) return '<span class="mut">No signals today yet. When you submit one it shows here: WAITING until it fills, then ACTIVE with the live price and P&amp;L.</span>';
  let o = `<div class="tot"><span>Open P&amp;L <b class="${cls(d.total_open_pnl_rs)}">${pn(d.total_open_pnl_rs)}</b></span><span>Closed today <b class="${cls(d.realised_today_rs)}">${pn(d.realised_today_rs)}</b></span><span>${esc(d.now_ist)} IST</span></div>`;
  for (const r of d.rows) {
    const live = r.state === "ACTIVE" || r.state === "WAITING", prev = lastLtp[r.id], dir = live && r.ltp != null && prev != null && r.ltp !== prev ? (r.ltp > prev ? "tickup" : "tickdn") : ""; if (r.ltp != null) lastLtp[r.id] = r.ltp;
    const age = r.quote_age_s, stale = live && age != null && age > 45;
    const big = r.state === "ACTIVE" ? `<div class="big ${cls(r.pnl_rs)}">${pn(r.pnl_rs)}</div>` : r.state === "CLOSED" ? `<div class="big ${cls(r.pnl_rs)}">${pn(r.pnl_rs)}</div>` : '<div class="big mut">–</div>';
    const nums = [];
    if (live) nums.push(`<span class="${dir}">LTP <b>${f(r.ltp)}</b></span>`, `<span>bid <b>${f(r.bid)}</b> / ask <b>${f(r.ask)}</b></span>`);
    if (r.state === "ACTIVE") nums.push(`<span>fill <b>${f(r.fill)}</b></span>`, (r.sl == null ? `<span class="mut">no stop-loss set</span>` : `<span>SL <b>${f(r.sl)}</b> (${f(r.to_sl)} away)</span>`), ((r.targets || []).length ? `<span>T1 <b>${f((r.targets || [])[0])}</b> (${f(r.to_t1)} away)</span>` : `<span class="mut">no target</span>`), `<span>${r.pnl_pct == null ? "" : (r.pnl_pct > 0 ? "+" : "") + r.pnl_pct + "%"}</span>`);
    if (r.state === "WAITING") nums.push(`<span>${esc(r.entry_mode)} <b>${f(r.entry)}</b></span>`, (r.sl == null ? `<span class="mut">no stop-loss</span>` : `<span>SL <b>${f(r.sl)}</b></span>`), ((r.targets || []).length ? `<span>T1 <b>${f((r.targets || [])[0])}</b></span>` : `<span class="mut">no target</span>`), r.valid_until_ist ? `<span>valid till <b>${esc(r.valid_until_ist)}</b></span>` : "");
    if (r.state === "CLOSED") nums.push(`<span>fill <b>${f(r.fill)}</b></span>`, `<span>exit <b>${f(r.exit)}</b> (${esc(r.why)})</span>`);
    const note = r.waiting_note ? `<div class="nums mut">${esc(r.waiting_note)}</div>` : r.why && r.state !== "CLOSED" ? `<div class="nums mut">${esc(r.why)}</div>` : "";
    o += `<div class="row"><span class="chip ${esc(r.state)}">${esc(r.state)}</span><div><b>${esc(r.label)}</b> <span class="mut">${esc(r.id)} · ${r.lots || 1} lot${(r.lots || 1) > 1 ? "s" : ""} · posted ${esc(r.posted_ist)}</span>${stale ? ' <span class="pnlneg">price not refreshed for ' + Math.round(age) + ' s</span>' : age != null && live ? ' <span class="mut">· checked ' + Math.round(age) + ' s ago</span>' : ""}</div>${big}<div class="nums">${nums.join("")}</div>${note}</div>`;
  }
  return o + '<div class="mut" style="font-size:11px;margin-top:4px">Open P&amp;L = (bid − fill) × lot − estimated costs. The final result is booked by the contest rules at the exit. Prices are the latest captured quotes (a few seconds old).</div>';
}
const rs = (x, d = 0) => (x === null || x === undefined || Number.isNaN(x) ? "—" : (x < 0 ? "−" : x > 0 ? "+" : "") + "₹" + Math.abs(x).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (x, d = 1) => (x === null || x === undefined ? "—" : (x * 100).toFixed(d) + "%");

/* form -> {ok, errors[], payload, preview}; mirrors the server rules so mistakes are caught before the (irreversible) submit */
/* every time shown on this page is INDIA STANDARD TIME (IST, UTC+5:30): the server sends epoch / UTC ISO values, converted here */
const IST_MS = 19800000, fmtIST = (ms, secs) => new Date(ms + IST_MS).toISOString().slice(0, secs ? 19 : 16).replace("T", " "), fmtIsoIST = (iso, secs) => fmtIST(Date.parse(iso), secs);
let RISK_MODULE = false, MAX_LOTS = 10;                                                         // set from /state: entry-only profile, ZeroHero's risk module may set stop and targets
function validateForm(v) {
  const err = [], num = (k) => (v[k] === "" || v[k] === undefined ? NaN : Number(v[k]));
  if (!ALLOWED.includes(String(v.underlying || "").toUpperCase())) err.push("Underlying must be one of " + ALLOWED.join(", "));
  if (!/^(\d{4}-\d{2}-\d{2}|\d{1,2}[A-Za-z]{3}\d{4})$/.test(String(v.expiry || "").trim())) err.push("Expiry like 27OCT2026 or 2026-10-27");
  const strike = num("strike"), entry = num("entry"), sl = num("sl");
  if (!(strike > 0)) err.push("Strike must be a positive number");
  if (!(entry > 0)) err.push("Entry premium must be > 0");
  const slBlank = String(v.sl === undefined || v.sl === null ? "" : v.sl).trim() === "";                          // the stop-loss is OPTIONAL: blank = no stop (add one later with "Change stop-loss", or never)
  if (!slBlank && !(sl > 0 && sl < entry)) err.push("Stop-loss, if you give one, must be above 0 and below entry (long options only)");
  const targets = String(v.targets || "").split(",").map((x) => x.trim()).filter(Boolean).map(Number);
  if (targets.some((t) => !(t > entry))) err.push("Targets are optional; if you give any, every target must be above entry");
  if (!["CE", "PE"].includes(String(v.option_type || "").toUpperCase())) err.push("Choose CE or PE");
  const lotsRaw = String(v.lots === undefined || v.lots === null ? "" : v.lots).trim(), lots = lotsRaw === "" ? null : Number(lotsRaw);
  if (lots !== null && !(Number.isInteger(lots) && lots >= 1 && lots <= (typeof MAX_LOTS === "number" ? MAX_LOTS : 10))) err.push("Quantity must be a whole number of lots, 1 to " + (typeof MAX_LOTS === "number" ? MAX_LOTS : 10));
  if (!v.confirmNotEntered) err.push("Tick: I have not entered this trade yet");
  const risk = entry - sl, rr = targets.length && risk > 0 ? (Math.min(...targets) - entry) / risk : null;
  const payload = { lots: lots === null ? undefined : lots, underlying: String(v.underlying || "").toUpperCase(), expiry: String(v.expiry || "").trim(), strike, option_type: String(v.option_type || "").toUpperCase(), entry_mode: String(v.entry_mode || "NEAR").toUpperCase(), entry, sl, targets: [...new Set(targets)].sort((a, b) => a - b), note: String(v.note || "").slice(0, 300) };
  return { ok: err.length === 0, errors: err, payload, preview: { risk_per_unit: risk > 0 ? risk : null, rr_to_t1: rr } };
}

/* tiny, safe markdown: escape first, then headings / bold / code / lists / tables / paragraphs */
function renderMarkdown(md) {
  const lines = String(md).split(/\r?\n/), out = []; let list = false, tbl = false;
  const inline = (t) => esc(t).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\*([^*]+)\*/g, "<i>$1</i>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const close = () => { if (list) { out.push("</ul>"); list = false; } if (tbl) { out.push("</tbody></table>"); tbl = false; } };
  for (const ln of lines) {
    if (/^\s*\|.*\|\s*$/.test(ln)) {
      if (/^\s*\|[\s\-:|]+\|\s*$/.test(ln)) continue;
      const cells = ln.trim().slice(1, -1).split("|").map((c) => inline(c.trim()));
      if (!tbl) { close(); out.push("<table><tbody>"); tbl = true; out.push("<tr>" + cells.map((c) => "<th>" + c + "</th>").join("") + "</tr>"); } else out.push("<tr>" + cells.map((c) => "<td>" + c + "</td>").join("") + "</tr>");
      continue;
    }
    const h = ln.match(/^(#{1,3})\s+(.*)$/); const li = ln.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (h) { close(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); }
    else if (li) { if (tbl) close(); if (!list) { out.push("<ul>"); list = true; } out.push("<li>" + inline(li[1]) + "</li>"); }
    else if (!ln.trim()) close();
    else { close(); out.push("<p>" + inline(ln) + "</p>"); }
  }
  close(); return out.join("\n");
}

function sideCard(label, s) {
  if (!s || !s.signals) return { label, lines: ["no signals yet"] };
  const L = [`signals ${s.signals} · settled ${s.settled} (${Object.entries(s.by_status || {}).map(([k, v]) => k + ":" + v).join(", ")})`];
  if (s.settled) L.push(`net ${rs(s.net_rs)} (${s.return_pct >= 0 ? "+" : ""}${s.return_pct.toFixed(2)}% of ₹${(s.capital || 0).toLocaleString("en-IN")})`, `win rate ${pct(s.win_rate)} · PF ${s.profit_factor === null ? "—" : s.profit_factor.toFixed(2)} · max DD ${rs(-s.max_drawdown_rs)} (${s.max_drawdown_pct.toFixed(1)}%)`, `gross ${rs(s.gross_rs)} · costs ${rs(-s.cost_rs)} · fills on real bid/ask ${pct(s.real_quote_fill_share, 0)}`);
  return { label, lines: L };
}

function verdictText(c, humanName) {
  if (!c) return "—";
  if (c.verdict === "INSUFFICIENT_DAYS") return `Too early: only ${c.days} trading day(s) with settled trades (need at least 5 for a comparison).`;
  const gap = `${rs(c.diff_total_rs)} (95% interval ${rs(c.ci95_rs[0])} to ${rs(c.ci95_rs[1])}) over ${c.days} days`;
  if (c.verdict === "DRAW") return `DRAW — the difference is inside the noise: ${gap}.`;
  return (c.verdict === "A_AHEAD" ? "ZeroHero AI is ahead" : `${humanName} is ahead`) + " in this period: " + gap + ". Ahead ≠ proven.";
}

/* management event form -> {ok, errors, payload} (mirrors submit.validate_event) */
function validateEvent(v) {
  const err = [], a = String(v.action || "").toUpperCase(), n = (k) => (v[k] === "" || v[k] === undefined ? NaN : Number(v[k]));
  if (!v.signal_id) err.push("Choose a signal");
  if (!["AMEND_SL", "AMEND_TARGETS", "TRAIL_SL", "EXIT_NOW", "CANCEL", "AMEND_LOTS"].includes(a)) err.push("Choose an action");
  const payload = { signal_id: v.signal_id, action: a };
  if (a === "AMEND_SL") { if (!(n("sl") > 0)) err.push("New stop-loss must be > 0"); else payload.sl = n("sl"); }
  if (a === "AMEND_LOTS") { const q = Number(v.lots); if (!(Number.isInteger(q) && q >= 1 && q <= MAX_LOTS)) err.push("Quantity must be a whole number of lots, 1 to " + MAX_LOTS); else payload.lots = q; }
  if (a === "AMEND_TARGETS") { const t = String(v.targets || "").split(",").map((x) => x.trim()).filter(Boolean).map(Number); if (!t.length || t.some((x) => !(x > 0))) err.push("Give one or more targets (> 0)"); else payload.targets = [...new Set(t)].sort((x, y) => x - y); }
  if (a === "TRAIL_SL") {
    const pts = n("trail_points"), pct = n("trail_pct");
    if (!Number.isNaN(pts) === !Number.isNaN(pct)) err.push("Give exactly one of: trail points, trail %");
    else if (!Number.isNaN(pts)) { if (!(pts > 0)) err.push("Trail points must be > 0"); else payload.trail_points = pts; }
    else { if (!(pct > 0 && pct < 100)) err.push("Trail % must be between 0 and 100"); else payload.trail_pct = pct; }
  }
  if (!v.confirm) err.push("Tick the confirmation: this cannot be undone");
  return { ok: err.length === 0, errors: err, payload };
}

if (typeof module !== "undefined") module.exports = { validateForm, validateEvent, renderMarkdown, sideCard, verdictText, rs, pct, esc, ALLOWED, fmtIST, fmtIsoIST, liveRows, setRiskModule: (b) => { RISK_MODULE = !!b; } };

if (typeof window !== "undefined" && typeof document !== "undefined") (function () {
  const $ = (id) => document.getElementById(id);
  let token = ""; try { token = sessionStorage.getItem("arena_token") || ""; } catch (e) { /* storage blocked */ }
  async function api(path, opts = {}) {
    const r = await fetch("/api/arena/public" + path, { ...opts, headers: { "X-Arena-Token": token, "Content-Type": "application/json" } });
    let j = null; try { j = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok) throw new Error((j && j.detail) || ("HTTP " + r.status));
    return j;
  }
  const msg = (id, text, bad) => { const e = $(id); e.textContent = text; e.className = "msg " + (bad ? "bad" : "ok"); e.hidden = !text; };
  async function liveTick() {
    if (typeof document === "undefined" || document.hidden || liveBusy || !token) return; liveBusy = true;
    try { const d = await api("/live"); const html = liveRows(d); document.querySelectorAll(".livePanel .liveBody").forEach((e) => { e.classList.remove("mut"); e.innerHTML = html; }); }
    catch (e) { document.querySelectorAll(".livePanel .liveBody").forEach((x) => { x.textContent = "live data unavailable (" + String(e.message || e) + ")"; }); }
    finally { liveBusy = false; }
  }
  const startLive = () => { stopLive(); liveTick(); liveTimer = setInterval(liveTick, 3000); }, stopLive = () => { if (liveTimer) { clearInterval(liveTimer); liveTimer = null; } };

  const show = (tab) => { document.querySelectorAll("[data-pane]").forEach((p) => (p.hidden = p.dataset.pane !== tab)); document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab)); if (tab === "mine") loadMine(); if (tab === "standings") loadStandings(); if (tab === "mine" || tab === "standings") startLive(); else stopLive(); };
  async function loadState() {
    try { const s = await api("/state"); $("mode").textContent = s.mode === "PRACTICE" ? `PRACTICE until ${s.start} — entries are stored but NOT scored` : s.mode === "ARENA_LIVE" ? (s.phase === "REHEARSAL" ? `REHEARSAL — official contest starts ${s.official_start} (ends ${s.end})` : `OFFICIAL CONTEST (${s.start} → ${s.end})`) : "ARENA ENDED";
      RISK_MODULE = !!s.risk_module; MAX_LOTS = s.max_lots || 10; { const d = document.getElementById("lotsDefault"), m = document.getElementById("lotsMax"); if (d) d.textContent = s.default_lots || 1; if (m) m.textContent = MAX_LOTS; const f = document.querySelector("form[name=submitForm], #sf"); const li = document.querySelector("input[name=lots]"); if (li) { li.defaultValue = String(s.default_lots || 1); if (!li.dataset.touched) li.value = s.default_lots || 1; } } { const n = document.getElementById("riskNote"); if (n) n.hidden = !RISK_MODULE; }
      $("mode").className = "banner " + (s.mode === "ARENA_LIVE" ? "live" : "prac"); $("who").textContent = s.participant + " · " + s.now_ist.replace("T", " ") + " IST"; $("conn").hidden = true; $("app").hidden = false; return s; }
    catch (e) { $("app").hidden = true; $("conn").hidden = false; msg("connmsg", e.message, true); return null; }
  }
  async function loadGuide() { try { $("guide").innerHTML = renderMarkdown((await api("/guide")).markdown); } catch (e) { $("guide").textContent = e.message; } }
  async function loadMine() {
    const tb = $("mine"); tb.replaceChildren();
    try { const j = await api("/signals"); if (!j.signals.length) { tb.insertRow().insertCell().textContent = "nothing submitted yet"; return; }
      const sel = $("mgSignal"); sel.replaceChildren(...j.signals.slice().reverse().map((r) => { const o = document.createElement("option"); o.value = r.id; o.textContent = `${r.id} · ${r.underlying} ${r.strike}${r.option_type} ${r.entry_mode} ${r.entry}`; return o; }));
      for (const r of j.signals.slice().reverse()) { const tr = tb.insertRow(); [r.id, fmtIsoIST(r.server_iso, true), r.excluded ? "EXCLUDED (agreed test)" : (r.scored ? "SCORED" : "PRACTICE"), `${r.underlying} ${r.strike} ${r.option_type} ${r.expiry}`, r.entry_mode, r.entry, (r.lots || 1), (r.sl === null || r.sl === undefined ? "no stop" : r.sl), (r.targets || []).join(", "),
        (r.events || []).map((e) => e.action + (e.sl ? " " + e.sl : "") + (e.targets ? " " + e.targets.join("/") : "") + (e.trail_points ? " " + e.trail_points + "pt" : "") + (e.trail_pct ? " " + e.trail_pct + "%" : "")).join(" → ") || "—", r.hash.slice(0, 10)].forEach((v) => (tr.insertCell().textContent = v)); } }
    catch (e) { tb.insertRow().insertCell().textContent = e.message; }
  }
  function resultsTable(el, rows) {
    el.replaceChildren(); for (const r of rows.slice(-60).reverse()) { const tr = el.insertRow(); [fmtIST((r.t || 0) * 1000), `${r.underlying} ${r.strike}${r.type}`, r.status, r.fill ? r.fill.toFixed(2) : "—", r.exit ? r.exit.toFixed(2) : "—", r.exit_reason || "—", r.net_rs !== undefined ? rs(r.net_rs) : "—"].forEach((v) => (tr.insertCell().textContent = v)); }
  }
  async function loadStandings() {
    try { const s = await api("/standings"); const put = (id, c) => { const e = $(id); e.replaceChildren(); const h = document.createElement("h3"); h.textContent = c.label; e.append(h); for (const l of c.lines) { const d = document.createElement("div"); d.textContent = l; e.append(d); } };
      put("aiCard", sideCard("ZeroHero AI (frozen AUTOSCALP)", s.ai)); if (s.active) { const c = sideCard("ZeroHero AI — ACTIVE arm (filters removed; exploratory, shadow)", s.active.summary); c.lines.push(`${(s.active.trades_per_day || 0).toFixed(2)} trades/day · COMMON ${(s.active.groups.COMMON || {}).settled || 0} · ACTIVE-ONLY ${(s.active.groups.ACTIVE_ONLY || {}).settled || 0} · verdict ${s.active.verdict}`); put("acCard", c); } put("huCard", sideCard(s.human_name, s.human)); $("verdict").textContent = verdictText(s.comparison, s.human_name);
      $("caveats").replaceChildren(...s.caveats.map((c) => { const li = document.createElement("li"); li.textContent = c; return li; })); $("spread").textContent = "spread models: " + JSON.stringify(s.spread_models);
      resultsTable($("aiRows"), s.ai_results); resultsTable($("huRows"), s.human_results); }
    catch (e) { $("verdict").textContent = e.message; }
  }
  $("saveToken").onclick = async () => { token = $("tokenIn").value.trim(); try { sessionStorage.setItem("arena_token", token); } catch (e) { /* ok */ } if (await loadState()) { loadGuide(); show("guide"); refreshContracts(); } };
  document.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => show(b.dataset.tab)));
  const form = $("f");
  function readForm() { const v = {}; for (const el of form.elements) if (el.name) v[el.name] = el.type === "checkbox" ? el.checked : el.value; return v; }
  const preview = () => { const r = validateForm(readForm()); $("prev").textContent = r.preview.risk_per_unit ? `risk/unit ${r.preview.risk_per_unit.toFixed(2)} · reward:risk to T1 ${r.preview.rr_to_t1 ? r.preview.rr_to_t1.toFixed(2) : "—"}` : ""; };
  let autoEntry = true, lastChain = null;
  const roleLabel = (e) => `${e.raw} · ${e.role}`;
  const rowLabel = (r) => `${r.label} ${r.strike}` + (r.quote ? ` · ${r.quote.ltp}${r.quote.bid ? " (bid " + r.quote.bid + " / ask " + r.quote.ask + ")" : ""}` : " · no quote");
  function suggestEntry() {
    if (!autoEntry || !lastChain) return; const k = Number(form.strike.value), r = lastChain.rows.find((x) => x.strike === k); if (!r || !r.quote) return;
    const q = r.quote, v = form.entry_mode.value === "LIMIT" ? (q.bid || q.ltp) : (q.ask || q.ltp); if (v) { form.entry.value = v; preview(); }
  }
  async function loadChain(keepExpiry) {
    const u = form.underlying.value, ot = form.option_type.value, ex = keepExpiry ? form.expiry.value : "";
    try {
      const j = await api(`/chain?underlying=${encodeURIComponent(u)}&option_type=${ot}` + (ex ? `&expiry=${encodeURIComponent(ex)}` : "") + "&n=7"); lastChain = j;
      if (!j.expiries || !j.expiries.length) { form.expiry.replaceChildren(); form.strike.replaceChildren(); $("hint").textContent = (j.problems || []).join(" · ") || `No captured quotes for ${u} yet.`; return; }
      const curExp = ex || j.expiries[0].raw; form.expiry.replaceChildren(...j.expiries.map((e) => { const o = document.createElement("option"); o.value = e.raw; o.textContent = roleLabel(e); return o; })); form.expiry.value = curExp;
      const keep = form.strike.value; form.strike.replaceChildren(...j.rows.map((r) => { const o = document.createElement("option"); o.value = r.strike; o.textContent = rowLabel(r); return o; }));
      form.strike.value = keep && j.rows.some((r) => String(r.strike) === keep) ? keep : String(j.atm);
      $("hint").textContent = j.spot ? `${u} price ${j.spot} (${String(j.spot_source).replace(/(\d)T(\d)/, "$1 $2").replace(/\.\d+Z?$/, "").slice(0, 44)}) · ATM ${j.atm} · step ${j.step}` : (j.problems || []).join(" · ");
      suggestEntry();
    } catch (e) { $("hint").textContent = e.message; }
  }
  form.oninput = (ev) => { preview(); const n = ev && ev.target && ev.target.name; if (n === "entry") autoEntry = false; if (n === "strike" || n === "entry_mode") suggestEntry(); };
  form.underlying.addEventListener("change", () => { autoEntry = true; form.entry.value = ""; loadChain(false); });
  form.option_type.addEventListener("change", () => { autoEntry = true; form.entry.value = ""; loadChain(true); });
  form.expiry.addEventListener("change", () => { autoEntry = true; form.entry.value = ""; loadChain(true); });
  $("refreshChain").onclick = () => { autoEntry = true; loadChain(true); };
  const pv = $("previewRisk"); if (pv) pv.onclick = async () => { const v = readForm(); const q = new URLSearchParams({ underlying: String(v.underlying || "").toUpperCase(), option_type: String(v.option_type || "").toUpperCase(), strike: v.strike, entry: v.entry, expiry: String(v.expiry || "") });
    try { const j = await api("/risk-preview?" + q.toString()); msg("submsg", `ZeroHero risk module: stop ${j.sl} · targets ${j.targets.join(" / ")} (1:1 / 1:2 / 1:3) · risk ${j.risk_per_unit} per unit` + (j.last_price ? ` · last price ${j.last_price}` : "") + (j.contract_captured ? "" : " · contract not captured: stop uses the no-quote rule") + " — nothing is recorded", false); } catch (e) { msg("submsg", String(e.message || e), true); } };
  const refreshContracts = () => loadChain(false);
  form.onsubmit = async (ev) => { ev.preventDefault(); const r = validateForm(readForm()); if (!r.ok) return msg("submsg", r.errors.join(" · "), true);
    $("go").disabled = true; try { const j = await api("/submit", { method: "POST", body: JSON.stringify(r.payload) }); msg("submsg", `Submitted ${j.id} at ${fmtIsoIST(j.server_iso, true)} IST${j.practice ? " (PRACTICE: not scored)" : ""} · hash ${j.hash.slice(0, 12)}… — it cannot be edited.` + ((j.warnings || []).length ? "  ⚠ " + j.warnings.join(" · ") : ""), !!((j.warnings || []).length)); form.reset(); preview(); }
    catch (e) { msg("submsg", e.message, true); } finally { $("go").disabled = false; } };
  const mg = $("mg"); const showMg = () => { const a = mg.action.value; mg.querySelectorAll("[data-for]").forEach((d) => (d.hidden = !d.dataset.for.split(" ").includes(a))); };
  mg.oninput = showMg; showMg();
  mg.onsubmit = async (ev) => { ev.preventDefault(); const v = { signal_id: mg.signal_id.value, action: mg.action.value, sl: mg.sl.value, lots: mg.lots.value, targets: mg.targets.value, trail_points: mg.trail_points.value, trail_pct: mg.trail_pct.value, confirm: mg.confirm.checked };
    const r = validateEvent(v); if (!r.ok) return msg("mgmsg", r.errors.join(" · "), true); $("mgGo").disabled = true;
    try { const j = await api("/amend", { method: "POST", body: JSON.stringify(r.payload) }); msg("mgmsg", `Recorded ${j.id} (${j.action}) at ${fmtIsoIST(j.server_iso, true)} IST${j.practice ? " (PRACTICE)" : ""} — takes effect after the contest execution delay, never retroactively.`, false); mg.reset(); showMg(); loadMine(); }
    catch (e) { msg("mgmsg", e.message, true); } finally { $("mgGo").disabled = false; } };
  $("tokenIn").value = token; if (token) loadState().then((s) => { if (s) { loadGuide(); show("guide"); refreshContracts(); } });
})();
