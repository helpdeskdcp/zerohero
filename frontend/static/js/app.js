// Chanakya AI — dashboard client. No build step; vanilla JS.
(() => {
  const state = { view: "signalshub", tradeFilter: "" };

  // ---------------- View routing (shared by sidebar nav + bottom tab bar) ----------------
  function setView(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === `view-${view}`));
    document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
    document.querySelectorAll(".tab-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
    if (view === "signalshub") loadSignalsHub();
    if (view === "overview") loadOverview();
    if (view === "signals") loadSignals();
    if (view === "trades") loadTrades();
    if (view === "monitor") loadMonitor();
    if (view === "scalp") loadScalp();
    if (view === "research") loadResearch();
    if (view === "system") loadSystem();
    if (view === "autoscalp") loadAutoscalp();
    if (view === "mathscalp") loadMathScalp();
    if (view === "orderflow") loadOrderflow();
    if (view === "optionchain") loadOptionchain();
    if (view === "runner") { try { refreshRunSelection(); } catch (e) {} }
  }
  document.querySelectorAll(".nav-item, .tab-item").forEach(btn => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  // ---------------- Helpers ----------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const fmt = (n, d = 2) =>
    (n === null || n === undefined || n === "" || isNaN(Number(n))) ? "—" : Number(n).toFixed(d);
  // signed number for P&L cells so gain/loss is not colour-only
  const fmtSigned = (n, d = 2) => {
    if (n === null || n === undefined || n === "" || isNaN(Number(n))) return "—";
    const v = Number(n);
    return (v > 0 ? "+" : "") + v.toFixed(d);
  };

  // Non-silent load-failure surface. A fetch failure used to only reach the
  // console; the operator now sees a dismissable toast and each view keeps its
  // last-known content instead of silently going stale.
  let _toastTimer = null;
  function showError(where, err) {
    console.error(where, err);
    const box = $("#toast");
    if (!box) return;
    box.textContent = `${where}: ${(err && err.message) || err || "request failed"}`;
    box.hidden = false;
    box.className = "toast toast--err";
    if (_toastTimer) clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { box.hidden = true; }, 8000);
  }
  function clearError() { const b = $("#toast"); if (b) b.hidden = true; }
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
  const text = (value, fallback = "—") => esc(value === null || value === undefined || value === "" ? fallback : value);
  const directionClass = value => value === "BUY" ? "BUY" : value === "SELL" ? "SELL" : "UNKNOWN";
  const timeStr = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch { return iso; }
  };

  // optional API token — only needed if the server sets CHANAKYA_API_TOKEN
  let apiToken = "";
  try { apiToken = localStorage.getItem("chanakya_token") || ""; } catch { /* private mode */ }
  function setToken(t) {
    apiToken = (t || "").trim();
    try { localStorage.setItem("chanakya_token", apiToken); } catch { /* ignore */ }
  }

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    if (apiToken) headers["Authorization"] = "Bearer " + apiToken;
    const res = await fetch(path, { ...opts, headers });
    if (res.status === 401) {
      const t = prompt("API token required (server has CHANAKYA_API_TOKEN set):", apiToken);
      if (t !== null && t.trim()) { setToken(t); location.reload(); }
      throw new Error("401 unauthorized");
    }
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json();
  }

  // ---------------- WebSocket live feed ----------------
  let ws;
  let wsPingTimer = null;
  let wsReconnectTimer = null;
  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = apiToken ? `?token=${encodeURIComponent(apiToken)}` : "";
    ws = new WebSocket(`${proto}://${location.host}/ws${q}`);
    ws.onopen = () => setWsStatus(true);
    ws.onclose = () => {
      setWsStatus(false);
      // one keepalive timer at a time — clear the old one before reconnecting
      if (wsPingTimer) { clearInterval(wsPingTimer); wsPingTimer = null; }
      if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(connectWs, 2500);
    };
    ws.onerror = () => { try { ws.close(); } catch (_) { /* already closing */ } };
    ws.onmessage = (evt) => {
      try { handleWsMessage(JSON.parse(evt.data)); } catch { /* ignore malformed frame */ }
    };
    // keepalive ping — replace any previous timer so reconnects don't stack them
    if (wsPingTimer) clearInterval(wsPingTimer);
    wsPingTimer = setInterval(() => {
      try { if (ws && ws.readyState === 1) ws.send("ping"); } catch (_) { /* socket gone */ }
    }, 20000);
  }
  function setWsStatus(online) {
    const el = $("#wsStatus");
    el.textContent = online ? "● live" : "● connecting";
    el.className = "pill " + (online ? "pill--online" : "pill--offline");
  }

  function handleWsMessage(msg) {
    if (msg.type === "signal") {
      prependFeed(msg.data);
      if (state.view === "overview") loadOverview();
      if (state.view === "signals") loadSignals();
    }
    if (msg.type === "trade_open" || msg.type === "trade_update" || msg.type === "trade_closed") {
      if (state.view === "trades") loadTrades();
      if (state.view === "overview") loadOverview();
    }
    if (msg.type === "scalp_signal") {
      prependFeed(msg.data);
      if (state.view === "scalp") loadScalp();
    }
    if (msg.type === "scalp_open" || msg.type === "scalp_update" || msg.type === "scalp_closed") {
      if (state.view === "scalp") loadScalp();
      if (state.view === "overview") loadOverview();
    }
    if (state.view === "monitor" &&
        /^(scalp_|position_|combo_|reversal_|turning_point_|execution_)/.test(msg.type || "")) loadMonitor();
    if (msg.type === "reversal_signal") {
      const d = msg.data || {};
      prependFeed({ direction: d.direction, decision: "REVERSAL " + (d.reversal || ""),
        market_regime: d.kind, probability: d.confidence, underlying: d.symbol, created_ts: new Date().toISOString() });
    }
    if (msg.type === "turning_point_signal") {
      const d = msg.data || {};
      prependFeed({ direction: (d.direction || "").includes("UP") ? "BUY" : "SELL",
        decision: "TURN " + (d.direction || ""), market_regime: (d.timeframe || "") + " conf " + d.confidence,
        probability: Math.round((d.p_up || 0.5) * 100), underlying: d.symbol, created_ts: new Date().toISOString() });
    }
    if (msg.type === "position_open" || msg.type === "position_update" || msg.type === "position_exit") {
      if (state.view === "scalp") loadScalp();
      if (msg.type === "position_exit") {
        const d = msg.data || {};
        prependFeed({ direction: d.direction, decision: (d.exit_reason || "EXIT") + " — " + (d.underlying || ""),
          market_regime: "position", probability: null, underlying: d.underlying, created_ts: d.closed_ts });
      }
    }
  }

  function prependFeed(sig) {
    const feed = $("#liveFeed");
    const empty = $(".feed-empty", feed);
    if (empty) empty.remove();
    const li = document.createElement("li");
    const dirClass = sig.direction === "BUY" ? "buy" : sig.direction === "SELL" ? "sell" : "none";
    li.className = "feed-item " + dirClass;
    li.innerHTML = `
      <span class="feed-dir ${directionClass(sig.direction)}">${text(sig.direction)}</span>
      <span class="feed-meta">${text(sig.underlying || sig.symbol)} · ${text(sig.decision)} · ${text(sig.market_regime)} · prob ${fmt(sig.probability, 1)}%</span>
      <span class="feed-time">${timeStr(sig.created_ts)}</span>
    `;
    feed.prepend(li);
    while (feed.children.length > 40) feed.removeChild(feed.lastChild);
  }

  // ---------------- Overview ----------------
  // ---------------- Unified Signal Dashboard ----------------
  const _dirCls = (d) => d === "BULLISH" ? "pos" : d === "BEARISH" ? "neg" : "hint";
  async function loadSignalsHub() {
    try {
      const d = await api("/api/signals/unified");
      $("#signalshubNote").textContent = (d.note || "") + (d.cached ? "  (cached)" : "") +
        (d.errors && Object.keys(d.errors).length ? "  ⚠ " + JSON.stringify(d.errors) : "");
      const grid = $("#signalshubGrid");
      grid.innerHTML = (d.rows || []).map(r => {
        const a = r.autoscalp || {}, h = r.hcs || {}, c = r.confluence || {}, o = r.orderflow || {};
        const badge = `<span class="${_dirCls(r.agreement === "BULLISH" ? "BULLISH" : r.agreement === "BEARISH" ? "BEARISH" : "")}">${esc(r.agreement)}</span>`;
        const av = r.agreement_votes || {};
        const tradeable = a.decision === "BUY_CE" || a.decision === "BUY_PE";
        return `<div class="rcard" data-exch="${esc(r.exchange || "")}" style="margin-bottom:10px">
          <h3><span class="exch">${esc(r.exchange || "—")}</span> ${esc(r.symbol)} ${badge}
            <span class="agree-note">agree ${av.bullish || 0}▲ / ${av.bearish || 0}▼ of ${av.n || 0}</span></h3>
          <div class="sig-grid">
            <div>
              <div class="kv"><span>AutoScalp</span><b class="${_dirCls(a.direction)}">${esc(a.decision || "—")}</b></div>
              <div class="kv"><span>regime / type</span><b>${esc(a.regime || "—")} · ${esc(a.signal_type || "—")}</b></div>
              <div class="kv"><span>confidence / p</span><b>${esc(a.confidence || "—")} · ${fmt(a.probability, 3)}</b></div>
              ${a.expected_premium_move != null ? `<div class="kv"><span>EPM${a.epm_method === "fallback" ? " ~" : ""}</span><b>${fmt(a.expected_premium_move, 2)} pt</b></div>` : ""}
              ${tradeable ? `<div class="kv"><span>entry / SL</span><b>${fmt(a.entry)} / ${fmt(a.stop_loss)}</b></div>
              <div class="kv"><span>T1 / T2</span><b>${fmt(a.target_1)} / ${fmt(a.target_2)}</b></div>
              <div class="kv"><span>RR / EV·R</span><b>${fmt(a.rr, 2)} / ${fmt(a.ev_r, 2)}</b></div>` : ""}
            </div>
            <div>
              <div class="kv"><span>HCS A+</span><b class="${h.a_plus ? "pos" : "hint"}">${h.a_plus ? "A+" : "no"}</b></div>
              <div class="kv"><span>HCS score</span><b>${fmt(h.hcs_score, 1)}</b></div>
              <div class="kv"><span>adaptive p</span><b>${fmt(h.adaptive_probability, 3)}</b></div>
              ${h.top_veto ? `<div class="kv"><span>top veto</span><b class="neg">${esc(h.top_veto)}</b></div>` : ""}
              <div class="kv"><span class="hint" style="font-size:.85em">${esc(h.reason || "")}</span></div>
            </div>
            <div>
              <div class="kv"><span>Confluence</span><b class="${_dirCls(c.direction)}">${esc(c.signal || "—")}</b></div>
              <div class="kv"><span>score / conf</span><b>${fmt(c.score, 1)} / ${esc(c.confidence ?? "—")}</b></div>
              <div class="kv"><span>spot</span><b>${fmt(c.spot)}</b></div>
              <div class="kv"><span>support / resist</span><b>${fmt(c.support)} / ${fmt(c.resistance)}</b></div>
              <div class="kv"><span>pivot</span><b>${fmt(c.pivot)}</b></div>
            </div>
            <div>
              <div class="kv"><span>Order-flow</span><b>${esc(o.state || o.status || "—")}</b></div>
              <div class="kv"><span>action</span><b class="${o.action === "AVOID" ? "neg" : "hint"}">${esc(o.action || "—")}</b></div>
              <div class="kv"><span>research</span><b>${esc(o.research_status || "—")}</b></div>
              <div class="kv"><span class="hint" style="font-size:.85em">as of ${esc(o.as_of || "—")}</span></div>
            </div>
          </div>
        </div>`;
      }).join("") || '<span class="hint">no symbols</span>';
    } catch (e) { const el = $("#signalshubErr"); if (el) { el.hidden = false; el.textContent = String(e.message || e); } }
  }
  document.addEventListener("click", (e) => { if (e.target && e.target.id === "signalshubRefresh") loadSignalsHub(); });

  async function loadOverview() {
    try {
      const [signals, trades, research] = await Promise.all([
        api("/api/signals?limit=500"),
        api("/api/trades?limit=500"),
        api("/api/research"),
      ]);
      $("#statSignals").textContent = signals.length;
      $("#statOpen").textContent = trades.filter(t => t.status === "OPEN").length;
      $("#statClosed").textContent = trades.filter(t => t.status === "CLOSED").length;
      $("#statWinRate").textContent = research.paper_trades.win_rate_pct !== null ? research.paper_trades.win_rate_pct + "%" : "—";
      const pnlEl = $("#statPnl");
      pnlEl.textContent = fmt(research.paper_trades.total_realized_pnl, 2);
      pnlEl.className = "stat-value " + (research.paper_trades.total_realized_pnl > 0 ? "pos" : research.paper_trades.total_realized_pnl < 0 ? "neg" : "");
      $("#statPF").textContent = research.paper_trades.profit_factor !== null ? fmt(research.paper_trades.profit_factor, 2) : "—";
    } catch (e) { showError("overview", e); }
  }

  // ---------------- Signal Ledger ----------------
  async function loadSignals() {
    try {
      const rows = await api("/api/signals?limit=300");
      const tbody = $("#signalsTable tbody");
      tbody.innerHTML = rows.map(r => `
        <tr>
          <td>${timeStr(r.created_ts)}</td>
          <td>${text(r.underlying || r.symbol)}</td>
          <td class="feed-dir ${directionClass(r.direction)}">${text(r.direction)}</td>
          <td><span class="badge ${esc(r.decision)}">${text(r.decision)}</span></td>
          <td>${text(r.market_regime)}</td>
          <td>${fmt(r.probability, 1)}%</td>
          <td>${fmt(r.confidence, 1)}%</td>
          <td>${fmt(r.risk_reward, 2)}</td>
          <td><span class="badge ${esc(r.risk_status)}">${text(r.risk_status)}</span></td>
          <td class="reason-cell">${esc((r.reason || "").slice(0, 160))}</td>
        </tr>
      `).join("") || `<tr><td colspan="10" class="hint">No signals logged yet.</td></tr>`;
    } catch (e) { showError("signals", e); }
  }
  $("#refreshSignals").addEventListener("click", loadSignals);

  // ---------------- Paper Trades ----------------
  async function loadTrades() {
    try {
      const rows = await api(`/api/trades?limit=300${state.tradeFilter ? "&status=" + state.tradeFilter : ""}`);
      const tbody = $("#tradesTable tbody");
      tbody.innerHTML = rows.map(r => `
        <tr>
          <td>${timeStr(r.opened_ts)}</td>
          <td><span class="badge ${r.strategy === "SCALP" ? "OPEN" : "UNKNOWN"}">${text(r.strategy, "CORE")}</span></td>
          <td>${text(r.underlying)}</td>
          <td>${text(r.option_type || r.instrument)} ${text(r.strike, "")}</td>
          <td class="feed-dir ${directionClass(r.direction)}">${text(r.direction)}</td>
          <td>${fmt(r.entry, 2)}</td>
          <td>${fmt(r.stop_loss, 2)}</td>
          <td>${fmt(r.target_1, 2)}</td>
          <td>${fmt(r.quantity, 0)}</td>
          <td><span class="badge ${esc(r.status)}">${text(r.status)}</span></td>
          <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmtSigned(r.pnl, 2)}</td>
          <td>${r.status === "OPEN" ? `<button class="btn btn-ghost close-trade-btn" data-id="${esc(r.trade_id)}" data-entry="${esc(r.entry)}">Close</button>` : ""}</td>
        </tr>
      `).join("") || `<tr><td colspan="12" class="hint">No trades yet.</td></tr>`;

      tbody.querySelectorAll(".close-trade-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const px = prompt("Exit price:", btn.dataset.entry);
          if (px === null) return;
          await api("/api/trades/close", { method: "POST", body: JSON.stringify({ trade_id: btn.dataset.id, exit_price: parseFloat(px) }) });
          loadTrades();
        });
      });
    } catch (e) { showError("trades", e); }
  }
  document.querySelectorAll("#tradeFilter .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#tradeFilter .seg-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.tradeFilter = btn.dataset.status;
      loadTrades();
    });
  });

  // ---------------- Run Pipeline (read-only AngelOne market-data snapshot) ----------------
  let runMarketSnapshot = null;
  const valueOrNA = (value) => value === null || value === undefined || value === "" ? "N/A" : String(value);
  function marketSnapshotText(s) {
    if (!s) return "Market data has not been resolved.";
    const lines = ["CHANAKYA AI MARKET DATA", "", `Exchange: ${valueOrNA(s.market)}`, `Symbol: ${valueOrNA(s.symbol)}`,
      `Instrument: ${valueOrNA(s.instrument)}`, `Underlying: ${valueOrNA(s.underlying)}`, `Spot: ${valueOrNA(s.spot)}`,
      `Expiry: ${valueOrNA(s.expiry)}`, `ATM: ${valueOrNA(s.atm)}`, `Status: ${valueOrNA(s.data_status)}`];
    if (s.quote) {
      lines.push("", "FUTURE / SPOT:", `LTP: ${valueOrNA(s.quote.ltp)}`, `Open: ${valueOrNA(s.quote.open)}`,
        `High: ${valueOrNA(s.quote.high)}`, `Low: ${valueOrNA(s.quote.low)}`, `Close: ${valueOrNA(s.quote.close)}`,
        `OI: ${valueOrNA(s.quote.oi)}`, `Change OI: ${valueOrNA(s.quote.oi_change)}`, `Volume: ${valueOrNA(s.quote.volume)}`);
    }
    if (s.chain && s.chain.length) {
      lines.push("", "OPTION CHAIN:");
      s.chain.forEach(row => lines.push(`Strike: ${valueOrNA(row.strike)}`, "CE:", `LTP: ${valueOrNA(row.ce_ltp)}`, `OI: ${valueOrNA(row.ce_oi)}`, `Change OI: ${valueOrNA(row.ce_oi_change)}`, `Volume: ${valueOrNA(row.ce_volume)}`, "IV: N/A", "Delta: N/A", "Gamma: N/A", "Theta: N/A", "Vega: N/A", "PE:", `LTP: ${valueOrNA(row.pe_ltp)}`, `OI: ${valueOrNA(row.pe_oi)}`, `Change OI: ${valueOrNA(row.pe_oi_change)}`, `Volume: ${valueOrNA(row.pe_volume)}`, "IV: N/A", "Delta: N/A", "Gamma: N/A", "Theta: N/A", "Vega: N/A", ""));
    }
    lines.push("", `Data Timestamp: ${valueOrNA(s.timestamp)}`, `Source: ${valueOrNA(s.source)}`);
    if (s.reason) lines.push(`Reason: ${s.reason}`);
    return lines.join("\n");
  }

  $("#runForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const payload = {
      market: fd.get("market"),
      symbol: fd.get("symbol"),
      instrument: fd.get("instrument"),
      timeframe: fd.get("timeframe"),
      underlying: fd.get("underlying"),
      spot: runMarketSnapshot && runMarketSnapshot.spot != null ? runMarketSnapshot.spot : null,
      expiry: fd.get("expiry") || "AUTO",
    };
    if (runMarketSnapshot) {
      const underlying = runMarketSnapshot.underlying_contract || {};
      const contract = runMarketSnapshot.contract || underlying;
      if (underlying.exchange && underlying.token) { payload.exchange = underlying.exchange; payload.symboltoken = underlying.token; }
      if (runMarketSnapshot.instrument === "FUTURE" && contract.exchange && contract.token) { payload.exchange = contract.exchange; payload.symboltoken = contract.token; }
      if (runMarketSnapshot.atm != null) payload.strike = runMarketSnapshot.atm;
      if (runMarketSnapshot.chain) payload.chain = runMarketSnapshot.chain;
    }
    const candlesJson = fd.get("candles_json").trim();
    const chainJson = fd.get("chain_json").trim();
    const accountJson = fd.get("account_json").trim();
    try {
      if (candlesJson) payload.candles = JSON.parse(candlesJson);
      if (chainJson) payload.chain = JSON.parse(chainJson);
      if (accountJson) payload.account = JSON.parse(accountJson);
    } catch (err) {
      $("#runResult").textContent = "JSON parse error in advanced fields: " + err.message;
      return;
    }
    $("#runResult").textContent = "Running pipeline…";
    try {
      const result = await api("/api/run", { method: "POST", body: JSON.stringify(payload) });
      $("#runResult").textContent = marketSnapshotText(runMarketSnapshot) + "\n\nPIPELINE:\n" + JSON.stringify(result.contract || result, null, 2);
    } catch (err) {
      $("#runResult").textContent = "Error: " + err.message;
    }
  });

  const copyMarketSnapshot = $("#copyMarketSnapshot");
  if (copyMarketSnapshot) copyMarketSnapshot.addEventListener("click", async () => {
    if (!runMarketSnapshot || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(marketSnapshotText(runMarketSnapshot));
      copyMarketSnapshot.textContent = "Copied";
      setTimeout(() => { copyMarketSnapshot.textContent = "Copy"; }, 1600);
    } catch (_) { copyMarketSnapshot.textContent = "Copy unavailable"; setTimeout(() => { copyMarketSnapshot.textContent = "Copy"; }, 1600); }
  });

  // ---------------- Live Monitor ----------------
  let monitorRequestSeq = 0;
  let monitorFetchInFlight = false;
  let monitorRefreshQueued = false;

  function loadMonitor() {
    ++monitorRequestSeq;
    if (monitorFetchInFlight) {
      monitorRefreshQueued = true;
      return;
    }
    return fetchMonitor();
  }

  async function fetchMonitor() {
    monitorFetchInFlight = true;
    const requestSeq = monitorRequestSeq;
    const finish = () => {
      monitorFetchInFlight = false;
      if (monitorRefreshQueued || requestSeq !== monitorRequestSeq) {
        monitorRefreshQueued = false;
        fetchMonitor();
      }
    };
    let m;
    try { m = await api("/api/monitor"); }
    catch (e) {
      if (requestSeq === monitorRequestSeq) {
        const el = $("#monErr"); el.hidden = false; el.textContent = "monitor fetch failed: " + e.message;
      }
      finish();
      return;
    }
    // A slower response can never overwrite a newer periodic, WebSocket, or
    // user-triggered refresh.
    if (requestSeq !== monitorRequestSeq) {
      finish();
      return;
    }
    $("#monErr").hidden = true;
    const r = m.runner || {}, feed = m.feed || {};
    const set = (id, txt, cls) => { const el = $("#" + id); if (!el) return; el.textContent = txt; el.className = cls || ""; };
    set("monRunner",
      (r.armed ? "ARMED" : "disarmed") + (r.auto_arm ? " · auto" : "") + (r.fast_mode ? " · fast" : ""),
      r.armed ? "on" : "off");
    const fFresh = feed.connected && feed.last_msg_age_sec !== null && feed.last_msg_age_sec < 15;
    set("monFeed", feed.connected ? (fFresh ? "live" : "idle " + (feed.last_msg_age_sec ?? "—") + "s") : "offline",
      feed.connected ? (fFresh ? "on" : "warn") : "off");
    set("monLatency", r.manage_latency_ms != null ? r.manage_latency_ms + " ms" : "—",
      (r.manage_latency_ms != null && r.manage_latency_ms < 50) ? "on" : "warn");
    set("monSession", r.session_note || (r.session_open ? "open" : "closed"), r.session_open ? "on" : "warn");
    set("monOpen", `${(m.positions || []).filter(x => x.status === "OPEN").length} pos · ${r.open_scalps || 0} scalp`);
    set("monClock", new Date(m.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));

    const pnlCls = v => v > 0 ? "stat-value pos" : v < 0 ? "stat-value neg" : "";
    const near = (v, warn) => v == null ? "" : (Math.abs(v) <= warn ? ' style="color:var(--gold-soft)"' : "");

    const combos = m.combos || [];
    const cp = $("#monComboPanel");
    if (cp) {
      cp.hidden = combos.length === 0;
      $("#monComboTable tbody").innerHTML = combos.map(c => `
        <tr>
          <td>${text(c.kind)}</td>
          <td>${text((c.legs || []).join(" + "))}</td>
          <td>${fmt(c.entry_combined, 2)}</td>
          <td><b>${fmt(c.combined_mark, 2)}</b></td>
          <td class="${pnlCls(c.combined_pnl)}" style="font-size:12px">${fmt(c.combined_pnl, 2)}</td>
          <td${near(c.dist_to_target, 1)}>${fmt(c.dist_to_target, 2)}</td>
          <td${near(c.dist_to_stop, 1)}>${fmt(c.dist_to_stop, 2)}</td>
          <td>${fmt(c.be_upper, 1)}</td>
          <td>${fmt(c.be_lower, 1)}</td>
          <td><span class="badge ${c.status === "OPEN" ? "OPEN" : "TRADE"}">${text(c.status)}</span></td>
          <td>${c.status === "OPEN" ? `<button class="btn btn-ghost combo-lv-btn" data-id="${esc(c.combo_id)}" data-t="${esc(c.target_combined)}" data-s="${esc(c.stop_combined)}">levels</button>` : ""}</td>
        </tr>`).join("");
      cp.querySelectorAll(".combo-lv-btn").forEach(b => b.addEventListener("click", async () => {
        const t = prompt("Combined TARGET (exit both legs when CE+PE mark ≥):", b.dataset.t);
        if (t === null) return;
        const s = prompt("Combined STOP (cut both when CE+PE mark ≤):", b.dataset.s);
        if (s === null) return;
        await api("/api/positions/combo/levels", { method: "POST", body: JSON.stringify({ combo_id: b.dataset.id, target: parseFloat(t), stop: parseFloat(s) }) });
        loadMonitor();
      }));
    }

    const pt = $("#monPosTable tbody");
    pt.innerHTML = (m.positions || []).map(p => {
      const noLevels = p.status === "OPEN" && (p.target_1 == null || p.stop_loss == null);
      const synced = /auto-synced/.test(p.reason || "");
      const monitorStatus = p.monitor_status || p.status || "UNKNOWN";
      const statusText = monitorStatus === "TARGET_HIT" ? "TARGET HIT" :
        monitorStatus === "STOP_HIT" ? "STOP HIT" :
        monitorStatus === "STALE_DATA" ? "STALE DATA" :
        monitorStatus === "INVALID_DATA" ? "INVALID DATA" : monitorStatus;
      const statusClass = monitorStatus === "OPEN" ? "OPEN" :
        monitorStatus === "STALE_DATA" || monitorStatus === "INVALID_DATA" ? "UNKNOWN" : "TRADE";
      return `
      <tr>
        <td>${text(p.underlying)}${synced ? ' <span class="badge UNKNOWN" title="from broker">sync</span>' : ""}</td>
        <td>${text(p.option_type || p.instrument)} ${text(p.strike, "")}</td>
        <td class="feed-dir ${directionClass(p.direction)}">${text(p.direction)}</td>
        <td>${fmt(p.entry, 2)}</td>
        <td><b>${p.mark != null ? fmt(p.mark, 2) : "—"}</b></td>
        <td>${p.freshness === "STALE" && p.stale_age_sec != null ? `STALE ${Math.round(p.stale_age_sec)}s` : (p.mark_age_sec != null ? Math.round(p.mark_age_sec) + "s" : "—")}</td>
        <td class="${pnlCls(p.live_pnl)}" style="font-size:12px">${fmt(p.live_pnl, 2)}</td>
        <td${near(p.dist_to_target, 0.5)}>${p.target_1 != null ? fmt(p.dist_to_target, 2) : `<button class="btn btn-ghost set-levels-btn" data-id="${esc(p.trade_id)}" data-entry="${esc(p.entry)}">set</button>`}</td>
        <td${near(p.dist_to_stop, 0.5)}>${p.stop_loss != null ? fmt(p.dist_to_stop, 2) : (noLevels ? "" : "—")}</td>
        <td>${fmt(p.mfe, 2)}</td><td>${fmt(p.mae, 2)}</td>
        <td><span class="badge ${statusClass}">${text(statusText)}</span>${p.exit_reason ? " " + text(p.exit_reason, "") : ""}</td>
        <td>${p.status === "OPEN" ? `<button class="btn btn-ghost set-levels-btn" data-id="${esc(p.trade_id)}" data-t="${esc(p.target_1 ?? "")}" data-s="${esc(p.stop_loss ?? "")}" data-entry="${esc(p.entry)}">levels</button>` : ""}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="13" class="hint">No positions. Take one in your broker (auto-syncs in ≤30s) or use the Track form on the Scalping tab.</td></tr>`;
    pt.querySelectorAll(".set-levels-btn").forEach(b => b.addEventListener("click", async () => {
      const tgt = prompt("Target price (blank = leave):", b.dataset.t || b.dataset.entry);
      if (tgt === null) return;
      const stp = prompt("Stop price (blank = leave):", b.dataset.s || b.dataset.entry);
      if (stp === null) return;
      const body = { trade_id: b.dataset.id };
      if (tgt !== "") body.target = parseFloat(tgt);
      if (stp !== "") body.stop = parseFloat(stp);
      await api("/api/positions/levels", { method: "POST", body: JSON.stringify(body) });
      loadMonitor();
    }));

    $("#monScalpTable tbody").innerHTML = (m.scalps || []).filter(s => s.status === "OPEN").map(s => {
      const monitorStatus = s.monitor_status || s.status || "UNKNOWN";
      const statusText = monitorStatus === "TARGET_HIT" ? "TARGET HIT" :
        monitorStatus === "STOP_HIT" ? "STOP HIT" :
        monitorStatus === "STALE_DATA" ? "STALE DATA" : monitorStatus;
      const statusClass = monitorStatus === "OPEN" ? "OPEN" : "TRADE";
      return `
      <tr>
        <td>${text(s.setup)}</td>
        <td>${text(s.underlying)}</td>
        <td class="feed-dir ${directionClass(s.direction)}">${text(s.direction)}</td>
        <td>${fmt(s.entry, 2)}</td>
        <td><b>${s.mark != null ? fmt(s.mark, 2) : "—"}</b></td>
        <td class="${pnlCls(s.live_pnl)}" style="font-size:12px">${fmt(s.live_pnl, 2)}</td>
        <td${near(s.dist_to_target, 0.5)}>${fmt(s.dist_to_target, 2)}</td>
        <td${near(s.dist_to_stop, 0.5)}>${fmt(s.dist_to_stop, 2)}</td>
        <td><span class="badge ${statusClass}">${text(statusText)}</span></td>
      </tr>`;
    }).join("") || `<tr><td colspan="9" class="hint">No open scalps.</td></tr>`;

    const revs = m.reversals || [];
    const rp = $("#monRevPanel");
    if (rp) {
      rp.hidden = revs.length === 0;
      $("#monRevTable tbody").innerHTML = revs.map(r => `
        <tr>
          <td>${text(r.symbol)}${r.timeframe ? ` <span class="hint">${text(r.timeframe, "")}</span>` : ""}</td>
          <td class="feed-dir ${r.direction === "SELL" ? "SELL" : "BUY"}">${text(r.reversal)}</td>
          <td>${text((r.kind || "").replace("AT_", ""))}</td>
          <td>${fmt(r.level, 1)}</td>
          <td>${fmt(r.price, 1)}</td>
          <td><b>${text(r.option)}</b></td>
          <td>${fmt(r.entry, 1)}</td>
          <td>${fmt(r.stop, 1)}</td>
          <td>${fmt(r.target_1, 1)}</td>
          <td>${fmt(r.target_2, 1)}</td>
          <td>${fmt(r.risk_reward, 2)}</td>
          <td>${fmt(r.confidence, 0)}%</td>
        </tr>`).join("");
    }

    const tps = m.turning_points || [];
    const tpp = $("#monTpPanel");
    if (tpp) {
      tpp.hidden = tps.length === 0;
      $("#monTpTable tbody").innerHTML = tps.map(t => {
        const tr = t.trade_ref || {};
        return `<tr>
          <td>${text(t.symbol)}${t.timeframe ? ` <span class="hint">${text(t.timeframe, "")}</span>` : ""}</td>
          <td class="feed-dir ${(t.direction || "").includes("UP") ? "BUY" : "SELL"}">${text((t.direction || "").replace("_TURN", ""))}</td>
          <td>${fmt(t.turn, 2)}</td>
          <td>${fmt((t.p_up || 0) * 100, 0)}%</td>
          <td${t.high_confidence ? ' style="color:var(--gold-soft);font-weight:600"' : ""}>${fmt(t.confidence, 0)}%</td>
          <td>${text((t.expected_move || {}).direction)} ${fmt((t.expected_move || {}).pts, 1)}</td>
          <td><b>${text(tr.option)}</b></td>
          <td>${fmt(tr.entry_ref, 1)}</td>
          <td>${fmt(tr.stop_loss, 1)}</td>
          <td>${fmt(tr.target_1, 1)}</td>
          <td>${fmt(tr.target_2, 1)}</td>
          <td>${fmt(tr.risk_reward, 2)}</td>
        </tr>`;
      }).join("");
    }

    const ex = m.execution || {};
    const ks = ex.kill_switch || {};
    const exStatus = $("#monExecStatus");
    if (exStatus) {
      exStatus.textContent =
        `mode ${ex.mode || "PAPER"} · ${ex.enabled ? "enabled" : "disabled"}` +
        (ex.frozen ? " · FROZEN" : "") +
        (ks.active ? ` · KILL SWITCH ON (${ks.policy || "MONITOR"})` : "");
      exStatus.style.color = (ks.active || ex.frozen) ? "var(--danger, #e5484d)" : "";
    }
    const killBtn = $("#monKillBtn");
    if (killBtn) {
      killBtn.textContent = ks.active ? "Kill switch: ON — click to clear" : "Kill switch: OFF — click to activate";
      killBtn.classList.toggle("danger", !!ks.active);
      killBtn.dataset.active = ks.active ? "1" : "0";
      if (!killBtn.dataset.bound) {
        killBtn.dataset.bound = "1";
        killBtn.addEventListener("click", async () => {
          const turnOn = killBtn.dataset.active !== "1";
          if (turnOn && !confirm("Activate the emergency kill switch? No new entries / auto re-entry; open positions stay monitored.")) return;
          try {
            await api("/api/execution/kill", { method: "POST", body: JSON.stringify({ active: turnOn, reason: "dashboard" }) });
          } catch (e) { alert("kill switch: " + e.message); }
          loadMonitor();
        });
      }
    }
    const exOrders = ex.orders || [];
    const exWrap = $("#monExecOrdersWrap");
    if (exWrap) {
      exWrap.hidden = exOrders.length === 0;
      $("#monExecTable tbody").innerHTML = exOrders.map(o => `
        <tr>
          <td class="hint">${text((o.trade_id || "").slice(-8), "")}</td>
          <td>${text(o.leg)}</td>
          <td class="feed-dir ${o.side === "SELL" ? "SELL" : "BUY"}">${text(o.side)}</td>
          <td>${text(o.order_type)}</td>
          <td>${fmt(o.requested_qty, 0)}</td>
          <td>${fmt(o.filled_qty, 0)}</td>
          <td>${o.avg_fill_price != null ? fmt(o.avg_fill_price, 2) : "—"}</td>
          <td><span class="badge">${text(o.status)}</span></td>
          <td class="hint">${text((o.exit_reason || o.error || "").slice(0, 40), "")}</td>
        </tr>`).join("");
    }

    $("#monMarks").innerHTML = Object.entries(feed.marks || {}).map(([t, mk]) =>
      `<div class="mon-mark"><span title="${text(t)}">${text(mk.label || t)}</span><b>${fmt(mk.ltp, 2)}</b><em>${Math.round(mk.age_sec)}s</em></div>`
    ).join("") || `<span class="hint">No live marks yet.</span>`;

    const stream = $("#monStream");
    stream.innerHTML = (m.recent_signals || []).slice(0, 20).map(s => `
      <li class="feed-item ${s.direction === "BUY" ? "buy" : s.direction === "SELL" ? "sell" : "none"}">
        <span class="feed-dir ${directionClass(s.direction)}">${text(s.direction)}</span>
        <span class="feed-meta">${text(s.underlying || s.symbol)} · ${text(s.decision)} · ${text(s.market_regime)} · ${text((s.reason || "").slice(0, 80), "")}</span>
        <span class="feed-time">${timeStr(s.created_ts)}</span>
      </li>`).join("") || `<li class="feed-empty">No signals yet.</li>`;
    finish();
  }

  // ---------------- Scalping ----------------
  const scalpState = { configTouched: false };
  const fmtHold = (s) => (s === null || s === undefined) ? "—" : (s < 90 ? `${Math.round(s)}s` : `${(s / 60).toFixed(1)}m`);

  async function loadScalp() {
    try {
      const [status, trades, research, positions] = await Promise.all([
        api("/api/scalp/status"),
        api("/api/scalp/trades?limit=200"),
        api("/api/research"),
        api("/api/positions?limit=100"),
      ]);
      renderScalpStatus(status);
      renderScalpStats(research.by_strategy && research.by_strategy.SCALP);
      renderScalpBlotter(trades);
      renderPositions(positions, status.feed);
      if (!scalpState.configTouched) fillScalpConfig(status.config);
    } catch (e) { showError("scalp", e); }
  }

  function renderPositions(rows, feed) {
    const tbody = $("#posTable tbody");
    const marks = (feed && feed.marks) || {};
    const now = Date.now();
    tbody.innerHTML = rows.map(r => {
      const m = marks[r.symboltoken];
      const mark = m ? m.ltp : (r.status === "CLOSED" ? r.exit_price : null);
      const since = r.opened_ts ? ((r.closed_ts ? new Date(r.closed_ts) : now) - new Date(r.opened_ts)) / 1000 : null;
      return `<tr>
        <td>${timeStr(r.opened_ts)}${since !== null ? " · " + fmtHold(since) : ""}</td>
        <td>${text(r.underlying)}</td>
        <td>${text(r.option_type || r.instrument)} ${text(r.strike, "")}</td>
        <td class="feed-dir ${directionClass(r.direction)}">${text(r.direction)}</td>
        <td>${fmt(r.entry, 2)}</td>
        <td>${mark !== null ? fmt(mark, 2) : "—"}</td>
        <td>${fmt(r.target_1, 2)}</td>
        <td>${fmt(r.stop_loss, 2)}</td>
        <td>${fmt(r.quantity, 0)}</td>
        <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmtSigned(r.pnl, 2)}</td>
        <td><span class="badge ${esc(r.status)}">${text(r.status)}</span></td>
        <td class="exit-cell">${text(r.exit_reason, "")}</td>
        <td>${r.status === "OPEN" ? `<button class="btn btn-ghost untrack-btn" data-id="${esc(r.trade_id)}" data-mark="${esc(mark ?? r.entry)}">Untrack</button>` : ""}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="13" class="hint">No tracked positions.</td></tr>`;
    tbody.querySelectorAll(".untrack-btn").forEach(b => b.addEventListener("click", async () => {
      await api("/api/positions/untrack", { method: "POST", body: JSON.stringify({ trade_id: b.dataset.id, exit_price: parseFloat(b.dataset.mark) }) });
      loadScalp();
    }));
  }

  const trackForm = $("#trackForm");
  if (trackForm) trackForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { symbol: fd.get("symbol"), option_type: fd.get("option_type"), direction: fd.get("direction") };
    ["symboltoken", "expiry"].forEach(k => { if (fd.get(k)) body[k] = fd.get(k); });
    ["strike", "entry", "target", "stop", "lots", "lot_size", "trailing_stop"].forEach(k => {
      const v = fd.get(k); if (v !== "" && v !== null) body[k] = Number(v);
    });
    try {
      const r = await api("/api/positions/track", { method: "POST", body: JSON.stringify(body) });
      $("#trackMsg").textContent = `Tracking ${r.underlying} ${r.option_type} ${r.strike} — target ${r.target_1} / stop ${r.stop_loss}. Alerts on hit (here + Telegram).`;
      e.target.reset();
      loadScalp();
    } catch (err) { $("#trackMsg").textContent = "Error: " + err.message; }
  });

  function renderScalpStatus(s) {
    const btn = $("#scalpArmBtn");
    btn.dataset.armed = String(!!s.armed);
    btn.textContent = s.armed ? "DISARM" : "ARM";
    const set = (id, txt, cls) => {
      const el = $("#" + id);
      el.textContent = txt;
      el.className = cls || "";
    };
    set("scalpState", s.armed ? (s.running ? "ARMED · running" : "ARMED") : "disarmed", s.armed ? "on" : "off");
    set("scalpSession", s.session_note || (s.session_open ? "open" : "closed"), s.session_open ? "on" : "warn");
    set("scalpCooldown", s.cooldown_sec_remaining ? s.cooldown_sec_remaining + "s" : "—", s.cooldown_sec_remaining ? "warn" : "");
    set("scalpOpen", `${s.open_scalps} / ${s.max_concurrent}`);
    set("scalpToday", `${s.traded_today} / ${s.daily_cap}`, s.traded_today >= s.daily_cap ? "warn" : "");
    set("scalpTick", timeStr(s.last_tick_ts));
    const feed = s.feed || {};
    const fresh = feed.connected && feed.last_msg_age_sec !== null && feed.last_msg_age_sec < 15;
    set("scalpFeed",
      feed.connected ? (fresh ? "WS live" : "WS idle (" + (feed.last_msg_age_sec ?? "—") + "s)") : "offline",
      feed.connected ? (fresh ? "on" : "warn") : "off");
    const marks = Object.entries(feed.marks || {});
    set("scalpMarks", marks.length
      ? marks.map(([t, m]) => `${(m && m.label) || t}: ${fmt(m.ltp, 1)}`).slice(0, 4).join("   ")
      : "—");
    const err = $("#scalpErr");
    if (s.last_error) { err.hidden = false; err.textContent = "last error: " + s.last_error; }
    else err.hidden = true;
  }

  function renderScalpStats(st) {
    const g = (id, v) => { $("#" + id).textContent = v; };
    if (!st || !st.closed) {
      ["scStatClosed", "scStatWin", "scStatExp", "scStatPF", "scStatHold", "scStatPnl"].forEach(id => g(id, "—"));
      $("#scStatClosed").textContent = st ? (st.closed || 0) : "0";
      return;
    }
    g("scStatClosed", st.closed);
    g("scStatWin", (st.win_rate_pct ?? "—") + "%");
    const exp = $("#scStatExp");
    exp.textContent = fmt(st.expectancy_per_trade, 2);
    exp.className = "stat-value " + (st.expectancy_per_trade > 0 ? "pos" : st.expectancy_per_trade < 0 ? "neg" : "");
    g("scStatPF", st.profit_factor !== null && st.profit_factor !== undefined ? fmt(st.profit_factor, 2) : "—");
    g("scStatHold", fmtHold(st.avg_hold_sec));
    const pnl = $("#scStatPnl");
    pnl.textContent = fmt(st.total_realized_pnl, 2);
    pnl.className = "stat-value " + (st.total_realized_pnl > 0 ? "pos" : st.total_realized_pnl < 0 ? "neg" : "");
  }

  function renderScalpBlotter(rows) {
    const tbody = $("#scalpTable tbody");
    const now = Date.now();
    tbody.innerHTML = rows.map(r => {
      const held = r.opened_ts ? ((r.closed_ts ? new Date(r.closed_ts) : now) - new Date(r.opened_ts)) / 1000 : null;
      return `
        <tr>
          <td>${timeStr(r.opened_ts)}</td>
          <td>${text(r.underlying || r.market)}</td>
          <td>${text(r.setup)}</td>
          <td class="feed-dir ${directionClass(r.direction)}">${text(r.direction)}</td>
          <td>${fmt(r.entry, 2)}</td>
          <td>${fmt(r.stop_loss, 2)}</td>
          <td>${fmt(r.target_1, 2)}</td>
          <td>${fmt(r.quantity, 0)}</td>
          <td>${fmtHold(held)}</td>
          <td class="stat-value pos" style="font-size:11px">${fmt(r.mfe, 2)}</td>
          <td class="stat-value neg" style="font-size:11px">${fmt(r.mae, 2)}</td>
          <td><span class="badge ${esc(r.status)}">${text(r.status)}</span></td>
          <td class="exit-cell">${text(r.exit_reason, "")}</td>
          <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmtSigned(r.pnl, 2)}</td>
          <td>${r.status === "OPEN" ? `<button class="btn btn-ghost close-scalp-btn" data-id="${esc(r.trade_id)}" data-entry="${esc(r.entry)}">Close</button>` : ""}</td>
        </tr>`;
    }).join("") || `<tr><td colspan="15" class="hint">No scalps yet. Configure a watchlist and ARM the runner.</td></tr>`;

    tbody.querySelectorAll(".close-scalp-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const px = prompt("Exit price:", btn.dataset.entry);
        if (px === null) return;
        await api("/api/trades/close", { method: "POST", body: JSON.stringify({ trade_id: btn.dataset.id, exit_price: parseFloat(px) }) });
        loadScalp();
      });
    });
  }

  const SCALP_SIMPLE_FIELDS = ["poll_sec", "max_concurrent", "daily_cap", "loss_cooldown_sec",
    "session_start", "session_end", "skip_open_min", "skip_close_min"];

  function fillScalpConfig(cfg) {
    if (!cfg) return;
    const f = $("#scalpConfigForm");
    SCALP_SIMPLE_FIELDS.forEach(k => { if (f[k] != null && cfg[k] != null) f[k].value = cfg[k]; });
    f.ignore_session.checked = !!cfg.ignore_session;
    // LIVE is server-gated and not selectable from the dashboard; if a stored
    // config still carries it, say so rather than silently showing PAPER.
    if ((cfg.execution_mode || "").toUpperCase() === "LIVE") {
      f.execution_mode.value = "SHADOW";
      $("#scalpConfigMsg").textContent = "Stored execution_mode is LIVE (server-gated). Dashboard shows SHADOW; saving from here will set SHADOW.";
    } else {
      f.execution_mode.value = cfg.execution_mode || "PAPER";
    }
    f.execution_enabled.checked = !!cfg.execution_enabled;
    f.watchlist_json.value = JSON.stringify(cfg.watchlist || [], null, 2);
    f.account_json.value = JSON.stringify(cfg.account || {}, null, 2);
    f.scalp_config_json.value = JSON.stringify(cfg.scalp_config || {}, null, 2);
  }

  $("#scalpConfigForm").addEventListener("input", () => { scalpState.configTouched = true; });

  $("#scalpArmBtn").addEventListener("click", async () => {
    const armed = $("#scalpArmBtn").dataset.armed === "true";
    try {
      const s = await api(armed ? "/api/scalp/disarm" : "/api/scalp/arm", { method: "POST" });
      renderScalpStatus(s);
    } catch (e) { showError("scalp arm", e); }
  });

  $("#scalpConfigForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const patch = {
      ignore_session: f.ignore_session.checked,
      execution_mode: f.execution_mode.value,
      execution_enabled: f.execution_enabled.checked,
    };
    if (patch.execution_enabled && patch.execution_mode === "LIVE" &&
        !confirm("Enable LIVE order routing? This only succeeds when all server safeguards are configured. Continue?")) return;
    SCALP_SIMPLE_FIELDS.forEach(k => {
      const v = f[k].value.trim();
      if (v === "") return;
      patch[k] = f[k].type === "number" ? Number(v) : v;
    });
    try {
      if (f.watchlist_json.value.trim()) patch.watchlist = JSON.parse(f.watchlist_json.value);
      if (f.account_json.value.trim()) patch.account = JSON.parse(f.account_json.value);
      if (f.scalp_config_json.value.trim()) patch.scalp_config = JSON.parse(f.scalp_config_json.value);
    } catch (err) {
      $("#scalpConfigMsg").textContent = "JSON parse error: " + err.message;
      return;
    }
    try {
      await api("/api/scalp/config", { method: "POST", body: JSON.stringify(patch) });
      scalpState.configTouched = false;
      $("#scalpConfigMsg").textContent = "Saved — applies on the next tick.";
      loadScalp();
    } catch (err) {
      $("#scalpConfigMsg").textContent = "Error: " + err.message;
    }
  });

  $("#scalpDemoBtn").addEventListener("click", () => {
    const f = $("#scalpConfigForm");
    f.watchlist_json.value = JSON.stringify([demoWatchlistItem()], null, 2);
    f.scalp_config_json.value = JSON.stringify({ max_hold_sec: 45 }, null, 2);
    f.ignore_session.checked = true;
    scalpState.configTouched = true;
    $("#scalpConfigMsg").textContent = "Demo watchlist loaded — Save Config, then ARM. Uses a synthetic momentum-break tape; scalps exit on the 45s TIME clock.";
  });

  // Synthetic 1m tape: gentle uptrend + a final volume-backed breakout bar.
  function demoWatchlistItem() {
    const step = 60_000, bars = 40, start = Date.now() - (bars - 1) * step;
    const candles = [];
    let priorHigh = 0;
    for (let i = 0; i < bars; i++) {
      const t = Math.round((start + i * step) / 1000);
      const base = 100 + i * 0.03;
      let o = +base.toFixed(2), h = +(base + 0.05).toFixed(2), l = +(base - 0.05).toFixed(2), c = +(base + 0.02).toFixed(2), v = 1000;
      if (i === bars - 1) { c = +(priorHigh + 0.30).toFixed(2); h = +(c + 0.05).toFixed(2); o = +base.toFixed(2); l = +(base - 0.02).toFixed(2); v = 3200; }
      candles.push([t, o, h, l, c, v]);
      if (i < bars - 1) priorHigh = Math.max(priorHigh, h);
    }
    return { symbol: "DEMO", underlying: "DEMO", market: "NSE", instrument: "INDEX", timeframe: "1m", replay_candles: candles };
  }

  $("#refreshScalp").addEventListener("click", loadScalp);

  // instrument registry -> Run-form datalist + scalp symbol picker
  let INSTRUMENTS = [];
  async function loadInstruments() {
    try {
      const r = await api("/api/instruments");
      INSTRUMENTS = r.instruments || [];
      const dl = $("#instrumentList");
      if (dl) dl.innerHTML = INSTRUMENTS.map(i => `<option value="${i.name}">${i.exchange} ${i.symboltoken}</option>`).join("");
      const picker = $("#scalpSymPicker");
      if (picker) picker.innerHTML = INSTRUMENTS.map(i =>
        `<label class="chk"><input type="checkbox" value="${i.name}" data-market="${i.market || i.exchange}" /> ${i.name}</label>`).join("");
    } catch (e) { showError("loadInstruments", e); }
  }

  // popular F&O indices float to the top of the searchable symbol list
  const POPULAR_SYMS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];
  async function loadRunSymbols(market) {
    const sel = $("#runSymbol");
    const dl = $("#runSymbolList");
    if (!sel || !dl) return;
    runMarketSnapshot = null;
    if (copyMarketSnapshot) copyMarketSnapshot.disabled = true;
    // Explicit "market instruments unavailable" state — never fabricate a symbol
    // list, spot, or ATM. The Advanced → raw candles JSON path still works.
    const showUnavailable = (note) => {
      dl.innerHTML = "";
      sel.disabled = true;
      sel.placeholder = "— market instruments unavailable —";
      const rr = $("#runResult");
      if (rr) rr.textContent = "Market instruments unavailable — live broker / market data is not reachable"
        + (note ? " (" + note + ")" : "") + ".\n"
        + "Spot / ATM / Underlying stay blank until market data resolves.\n"
        + "You can still run a backtest/replay via Advanced → paste raw candles JSON.";
    };
    try {
      const r = await api(`/api/market-instruments?market=${encodeURIComponent(market)}`);
      const items = r.instruments || [];
      if (!items.length || r.data_status === "DATA_UNAVAILABLE") { showUnavailable(r && r.reason); return; }
      sel.disabled = false;
      sel.placeholder = "Type to search…";
      const names = items.map(i => text(i.name));
      const top = POPULAR_SYMS.filter(s => names.includes(s));
      const rest = names.filter(s => !top.includes(s)).sort();
      dl.innerHTML = top.map(s => `<option value="${s}" label="★ index"></option>`).join("")
        + rest.map(s => `<option value="${s}"></option>`).join("");
      if (!names.includes(sel.value)) sel.value = top[0] || names[0] || "";
      await refreshRunSelection();
    } catch (e) { showUnavailable(e && e.message); }
  }
  async function refreshRunSelection() {
    const form = $("#runForm"), market = form?.elements.market?.value, symbol = $("#runSymbol")?.value;
    if (!market || !symbol) return;
    const instrument = $("#runInstrument"), expiry = $("#runExpiry"), optionType = $("#runOptionType");
    if (market === "MCX") { instrument.value = "FUTURE"; instrument.disabled = true; optionType.disabled = true; }
    else { instrument.disabled = false; optionType.disabled = false; }
    try {
      const s = await api(`/api/market-selection?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}&instrument=${encodeURIComponent(instrument.value)}&expiry=${encodeURIComponent(expiry.value || "AUTO")}&option_type=${encodeURIComponent(optionType.value)}&window=5`);
      runMarketSnapshot = s;
      form.elements.underlying.value = s.underlying || symbol;
      form.elements.spot.value = s.spot == null ? "" : s.spot;
      form.elements.atm.value = s.atm == null ? "" : s.atm;
      if (market === "NSE" && s.instrument === "OPTION") instrument.value = "OPTION";
      const current = expiry.value;
      const expiries = s.available_expiries || (s.expiry ? [s.expiry] : []);
      if (expiries.length) {
        expiry.innerHTML = expiries.map(x => `<option value="${text(x)}">${text(x)}</option>`).join("");
        expiry.value = expiries.includes(current) ? current : (s.expiry || expiries[0]);
      } else expiry.innerHTML = `<option value="AUTO">AUTO</option>`;
      $("#runResult").textContent = marketSnapshotText(s);
      if (copyMarketSnapshot) copyMarketSnapshot.disabled = false;
    } catch (_) {
      runMarketSnapshot = null;
      $("#runResult").textContent = "DATA_UNAVAILABLE — market selection could not be resolved.";
    }
  }
  const runMarket = document.querySelector('#runForm [name="market"]');
  if (runMarket) { runMarket.addEventListener("change", () => loadRunSymbols(runMarket.value)); loadRunSymbols(runMarket.value); }
  const runSymbol = $("#runSymbol");
  if (runSymbol) {
    runSymbol.addEventListener("change", refreshRunSelection);
    // fire as soon as the typed text is an exact match in the datalist
    runSymbol.addEventListener("input", () => {
      const opts = Array.from($("#runSymbolList")?.options || []).map(o => o.value);
      if (opts.includes(runSymbol.value)) refreshRunSelection();
    });
  }
  const runExpiry = $("#runExpiry"), runOptionType = $("#runOptionType"), runInstrument = $("#runInstrument");
  if (runExpiry) runExpiry.addEventListener("change", refreshRunSelection);
  if (runOptionType) runOptionType.addEventListener("change", refreshRunSelection);
  if (runInstrument) runInstrument.addEventListener("change", refreshRunSelection);

  const liveBtn = $("#scalpLiveBtn");
  if (liveBtn) liveBtn.addEventListener("click", () => {
    const tf = $("#scalpWlTf").value;
    const picked = [...document.querySelectorAll("#scalpSymPicker input:checked")];
    if (!picked.length) { $("#scalpConfigMsg").textContent = "Pick at least one symbol."; return; }
    const wl = picked.map(cb => ({
      symbol: cb.value, underlying: cb.value, market: cb.dataset.market,
      instrument: "INDEX", timeframe: tf,
    }));
    const f = $("#scalpConfigForm");
    f.watchlist_json.value = JSON.stringify(wl, null, 2);
    f.ignore_session.checked = false;
    scalpState.configTouched = true;
    $("#scalpConfigMsg").textContent = `Live watchlist for ${picked.map(p => p.value).join(", ")} @ ${tf} — Save Config, then ARM.`;
  });

  // ---------------- Research ----------------
  async function loadResearch() {
    try {
      const r = await api("/api/research");
      $("#researchDisclaimer").textContent = r.probability_disclaimer;
      const grid = $("#researchGrid");
      const decisionRows = Object.entries(r.signals.by_decision || {}).map(([k, v]) => `<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
      const regimeRows = Object.entries(r.signals.by_market_regime || {}).map(([k, v]) => `<div class="kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");
      grid.innerHTML = `
        <div class="rcard">
          <h3>Signals by Decision</h3>
          ${decisionRows || '<span class="hint">No data yet</span>'}
        </div>
        <div class="rcard">
          <h3>Signals by Market Regime</h3>
          ${regimeRows || '<span class="hint">No data yet</span>'}
        </div>
        <div class="rcard">
          <h3>Paper Trade Outcomes</h3>
          <div class="kv"><span>Wins</span><b>${r.paper_trades.wins}</b></div>
          <div class="kv"><span>Losses</span><b>${r.paper_trades.losses}</b></div>
          <div class="kv"><span>Flat</span><b>${r.paper_trades.flat}</b></div>
          <div class="kv"><span>Win rate</span><b>${r.paper_trades.win_rate_pct ?? "—"}%</b></div>
          <div class="kv"><span>Avg PnL/trade</span><b>${fmt(r.paper_trades.avg_pnl_per_closed)}</b></div>
          <div class="kv"><span>Profit factor</span><b>${fmt(r.paper_trades.profit_factor)}</b></div>
          <div class="kv"><span>Net realized</span><b>${fmt(r.paper_trades.total_realized_pnl)}</b></div>
        </div>
        ${["SCALP", "MANUAL", "CORE"].filter(k => (r.by_strategy || {})[k] && r.by_strategy[k].closed).map(k => {
          const s = r.by_strategy[k];
          return `<div class="rcard">
            <h3>${k} — Edge</h3>
            <div class="kv"><span>Closed</span><b>${s.closed}</b></div>
            <div class="kv"><span>Win rate</span><b>${s.win_rate_pct ?? "—"}%</b></div>
            <div class="kv"><span>Expectancy/trade</span><b>${fmt(s.expectancy_per_trade)}</b></div>
            <div class="kv"><span>Payoff ratio</span><b>${fmt(s.payoff_ratio)}</b></div>
            <div class="kv"><span>Profit factor</span><b>${fmt(s.profit_factor)}</b></div>
            <div class="kv"><span>Avg hold</span><b>${s.avg_hold_sec != null ? (s.avg_hold_sec < 90 ? s.avg_hold_sec + "s" : (s.avg_hold_sec/60).toFixed(1) + "m") : "—"}</b></div>
          </div>`;
        }).join("")}
      `;
    } catch (e) { showError("research", e); }
    loadHcr();
  }
  $("#refreshResearch").addEventListener("click", loadResearch);

  // High-Conviction Runner (read-only research strategy)
  async function loadHcr() {
    try {
      const s = await api("/api/research/hcr/summary");
      $("#hcrVerdict").textContent = s.verdict || "";
      if (s.available && s.params) {
        const p = s.params;
        $("#hcrParams").textContent =
          `filter: day range ≥ ${p.day_wide_mult}× median · spike range_x ≥ ${p.spike_x} · before ${p.spike_before} · skip ${p.skip_dow.join("/")} · ` +
          `Leg A ${p.leg_a}, Leg B ${p.leg_b} · ${p.max_lifetime_min}-min lifetime`;
      }
      const card = (title, m) => !m ? "" : `<div class="rcard">
        <h3>${esc(title)}</h3>
        <div class="kv"><span>Signals (span)</span><b>${m.signals} · ${esc(m.span || "")}</b></div>
        <div class="kv"><span>Frequency</span><b>~${m.signals_per_month}/mo</b></div>
        <div class="kv"><span>Close-green rate</span><b>${m.close_green_pct}%</b></div>
        <div class="kv"><span>Leg A +1R hit</span><b>${m.legA_1R_hit_pct}%</b></div>
        <div class="kv"><span>Leg B +3R hit</span><b>${m.legB_3R_hit_pct}%</b></div>
        <div class="kv"><span>Blended E[R]</span><b>${fmt(m.blended_expectancy_R)}</b></div>
        <div class="kv"><span>Profit factor</span><b>${fmt(m.blended_profit_factor)}</b></div>
        <div class="kv"><span>Median R</span><b>${fmt(m.median_blended_R)}</b></div>
        <div class="kv"><span>Worst / max consec losers</span><b>${fmt(m.worst_blended_R)}R · ${m.max_consec_losers}</b></div>
      </div>`;
      $("#hcrGrid").innerHTML = card("Upstox NIFTY 5m", s.upstox) + card("Kaggle NIFTY 5m", s.kaggle) ||
        '<span class="hint">run scripts/high_conviction_runner_research.py to populate</span>';

      const d = await api("/api/research/hcr/signals?limit=20&scan_days=12");
      const ft = d.forward_test || {};
      const ftEl = $("#hcrForwardTest");
      if (ftEl) {
        ftEl.textContent = ft.sessions_observed
          ? `Forward-test: ${ft.sessions_observed} session(s) observed · HCR fired ${ft.fired} · ` +
            `graded ${ft.graded}${ft.graded ? ` (green ${ft.close_green}/${ft.graded}, net ${fmt(ft.net_blended_R)}R)` : ""} · ` +
            `last: ${esc(ft.last_session || "—")} → ${esc(String(ft.last_result))}`
          : "Forward-test: not started yet";
      }
      const rows = [];
      const ls = d.live_scan || {};
      (ls.fired || []).forEach(x => rows.push({ ...x, _tag: "LIVE" }));
      (d.recent_backtest_signals || []).forEach(x => rows.push({ ...x, _tag: "BT" }));
      const rr = (v) => v == null ? "—" : (+v).toFixed(2);
      $("#hcrTableBody").innerHTML = rows.length ? rows.map(x => {
        const res = x.outcome === "TRIGGERED"
          ? `<span class="${x.green ? "pos" : "neg"}">${x.green ? "GREEN" : "RED"} ${rr(x.blended_R)}R</span>`
          : `<span class="hint">${esc(x.outcome || "—")}</span>`;
        return `<tr>
          <td>${esc(x.session)} ${x._tag === "LIVE" ? '<span class="pill pill--paper">scan</span>' : ""}</td>
          <td>${esc(x.dow || "")}</td><td>${esc(x.spike_time || "")}</td>
          <td>${rr(x.range_x)}</td><td>${esc(x.side || "—")}</td>
          <td>${rr(x.legA_R)}</td><td>${rr(x.legB_R)}</td><td>${rr(x.blended_R)}</td><td>${res}</td>
        </tr>`;
      }).join("") : '<tr><td colspan="9" class="hint">no signals</td></tr>';
    } catch (e) { const el = $("#hcrErr"); if (el) { el.hidden = false; el.textContent = String(e.message || e); } }
    loadHcs();
  }

  // HCS meta-engine (shadow)
  async function loadHcs() {
    try {
      const r = await api("/api/hcs/evaluate");
      const g = r.a_plus_gate || {};
      $("#hcsGate").textContent = r.available
        ? `A+ gate: HCS ≥ ${g.hcs_min} · calib p ≥ ${g.prob_min} · confidence ∈ {${(g.allowed_confidence || []).join(", ")}} · 0 hard vetoes` +
          `   —   A+ signals right now: ${r.a_plus_count}`
        : "no live_market_snapshots yet";
      const rr = (v) => v == null ? "—" : (+v).toFixed(v < 1 ? 3 : 1);
      const rows = (r.results || []).map(x => {
        const hard = (x.vetoes || []).filter(v => v.severity === "HARD").map(v => v.filter);
        const dec = x.decision === "NO_TRADE"
          ? `<span class="hint">NO_TRADE</span>`
          : `<span class="${x.direction === "BULLISH" ? "pos" : "neg"}">${esc(x.decision)}</span>`;
        const mem = x.setup_memory || {};
        return `<tr>
          <td>${esc(x.symbol)}</td><td>${dec}</td>
          <td>${x.a_plus ? '<span class="pos">A+</span>' : "—"}</td>
          <td>${rr(x.hcs_score)}</td><td>${rr(x.calibrated_probability)}</td>
          <td>${esc(x.confidence || "—")}</td>
          <td>${mem.status === "OK" ? `${(mem.knn_win_rate * 100).toFixed(0)}% (k${mem.k})` : esc(mem.status || "—")}</td>
          <td>${hard.length ? `<span class="neg">${hard.map(esc).join(", ")}</span>` : "—"}</td>
          <td class="hint">${esc((x.reasons || [])[0] || "")}</td>
        </tr>`;
      });
      $("#hcsTableBody").innerHTML = rows.join("") || '<tr><td colspan="9" class="hint">no rows</td></tr>';
      $("#hcsVerdict").textContent = (r.results && r.results[0] && r.results[0].note) ||
        "SHADOW / advisory — not wired into the live engine.";

      const cal = await api("/api/hcs/calibration-report");
      $("#hcsCalib").textContent = cal.available
        ? `Calibration: ${cal.n_resolved} resolved (base ${(cal.base_win_rate * 100).toFixed(0)}%), ` +
          `global curve k=${cal.global_curve?.k} b=${cal.global_curve?.b} n=${cal.global_curve?.n}, ` +
          `OOS Brier=${cal.oos_holdout?.brier} ECE=${cal.oos_holdout?.ece} — ${esc(cal.verdict)}`
        : "calibration: no resolved outcomes yet";

      try {
        const ft = await api("/api/hcs/forward-test");
        const rp = ft.replay || {};
        const el = $("#hcsForwardTest");
        if (el && rp.available) {
          const a = rp.hcs_a_plus || {}, all = rp.all_resolved || {};
          el.textContent =
            `Forward-test (replay ${all.n} resolved): A+ fired ${a.n} · A+ win ${(a.win_rate * 100).toFixed(0)}% ` +
            `vs base ${(all.win_rate * 100).toFixed(0)}% · live A+ logged ${(ft.live_log || {}).a_plus_total ?? 0} — ${esc(rp.verdict || "")}`;
        }
      } catch (_) { /* forward-test optional */ }

      try {
        const ad = await api("/api/hcs/adaptive");
        const el = $("#hcsAdaptive");
        if (el && ad.available) {
          const wf = ad.walk_forward || {};
          const perSym = (r.results || []).filter(x => x.adaptive_probability != null)
            .map(x => `${x.symbol} ${(x.adaptive_probability).toFixed(2)}` +
              (x.adaptive_vs_calibrated != null ? `(${x.adaptive_vs_calibrated >= 0 ? "+" : ""}${x.adaptive_vs_calibrated.toFixed(2)})` : ""))
            .join(" · ");
          el.textContent =
            `Adaptive online-logit (SHADOW, ${ad.trained_rows} rows): walk-fwd Brier ` +
            `${wf.adaptive?.brier} vs logistic ${wf.existing_logistic?.brier} → winner ${wf.winner}. ` +
            `adaptive p: ${perSym || "—"}`;
        } else if (el) {
          el.textContent = `Adaptive model: ${esc(ad.reason || "insufficient data")}`;
        }
      } catch (_) { /* adaptive optional */ }
    } catch (e) { const el = $("#hcsErr"); if (el) { el.hidden = false; el.textContent = String(e.message || e); } }
    loadAdaptiveLayers();
  }

  // Adaptive research layers (structural break / institutional edge / microstructure):
  // each is an on-demand, read-only evaluation over already-captured data -- no
  // schedule calls these, so this panel IS what triggers the evaluation, same as
  // curling the endpoint yourself. Per-symbol calls are isolated with .catch(()=>null)
  // so one symbol/layer failing (e.g. no captured data yet) never blanks the others.
  const ADAPTIVE_LAYER_SYMBOLS = ["NIFTY", "BANKNIFTY", "NATURALGAS", "CRUDEOIL", "SENSEX"];
  async function loadAdaptiveLayers() {
    try {
      const sbResults = await Promise.all(ADAPTIVE_LAYER_SYMBOLS.map(sym =>
        api(`/api/structural-break/status/${sym}`).catch(() => null)));
      const sbRows = sbResults.filter(Boolean).map(r =>
        `<div class="kv"><span>${esc(r.symbol)}</span><b>${esc(r.state)}</b></div>`).join("");

      const ieCondition = encodeURIComponent("confidence==HIGH");
      const ieResults = await Promise.all(ADAPTIVE_LAYER_SYMBOLS.map(sym =>
        api(`/api/institutional-edge/status/${sym}?condition=${ieCondition}`).catch(() => null)));
      const ieRows = ieResults.filter(Boolean).map(r => {
        const ev = r.ev || {};
        const netEv = ev.net_ev_points != null ? fmt(ev.net_ev_points)
          : (ev.status === "UNCALIBRATED_COST" ? "n/a (cost)" : "—");
        return `<div class="kv"><span>${esc(r.instrument)}</span><b>${esc(r.state)} · net EV ${netEv}</b></div>`;
      }).join("");

      const today = new Date().toISOString().slice(0, 10);
      const msResults = await Promise.all(ADAPTIVE_LAYER_SYMBOLS.map(sym =>
        api(`/api/microstructure/status/${sym}?session_date=${today}`).catch(() => null)));
      const msRows = msResults.filter(Boolean).map(r =>
        `<div class="kv"><span>${esc(r.symbol)}</span><b>${esc(r.state)}</b></div>`).join("");

      $("#adaptiveLayersGrid").innerHTML = `
        <div class="rcard">
          <h3>Structural Break</h3>
          ${sbRows || '<span class="hint">no evaluations yet</span>'}
        </div>
        <div class="rcard">
          <h3>Institutional Edge <span class="hint">(confidence==HIGH)</span></h3>
          ${ieRows || '<span class="hint">no evaluations yet</span>'}
        </div>
        <div class="rcard">
          <h3>Microstructure <span class="hint">(today)</span></h3>
          ${msRows || '<span class="hint">no data for today yet</span>'}
        </div>
      `;
      $("#adaptiveLayersNote").textContent =
        "Each card is an on-demand, dark evaluation over already-captured data -- no schedule, no order path. " +
        "“INSUFFICIENT_DATA” / “CANDIDATE” is expected until enough resolved signals accumulate.";
    } catch (e) {
      const el = $("#adaptiveLayersErr");
      if (el) { el.hidden = false; el.textContent = String(e.message || e); }
    }
  }

  // ---------------- System & Health ----------------
  // GREEN when true; the failing colour depends on whether the check is a hard
  // fault (ERROR) or an expected-when-closed condition (WARN).
  const HEALTH_SOFT = new Set(["feed_fresh", "feed_connected", "armed"]);
  function healthCheckClass(key, ok, marketOpen) {
    if (ok) return "hc-ok";
    return (HEALTH_SOFT.has(key) && !marketOpen) ? "hc-warn" : "hc-err";
  }

  function renderHealthLine(sc) {
    const el = $("#asHealthLine");
    if (!el || !sc) return;
    const parts = [
      sc.ok ? "● healthy" : "● attention",
      sc.paper_mode === false ? "LIVE?!" : "PAPER",
      (sc.checks && sc.checks.live_trading_disabled) ? "LIVE off ✓" : "LIVE ⚠",
      "feed " + (sc.checks && sc.checks.feed_fresh ? "fresh" : (sc.market_open ? "STALE" : "quiet")),
      "aggs " + (sc.checks && sc.checks.all_aggs_seeded ? "ready" : "warming"),
    ];
    if ((sc.config_warnings || []).length) parts.push("⚠ " + sc.config_warnings.length + " cfg");
    el.textContent = parts.join(" · ");
    el.className = "health-line " + (sc.ok ? "hc-ok" : "hc-warn");
  }

  async function loadSystem() {
    // three independent panels — a failure in one must not blank the others
    try {
      const [env, health] = await Promise.all([api("/api/env-check"), api("/api/health")]);
      $("#sysGrid").innerHTML = Object.entries(env).map(([k, v]) => `
        <div class="syscell"><span>${esc(k)}</span><span class="dot ${v ? "on" : "off"}" title="${v ? "configured" : "missing"}"></span></div>
      `).join("") + `
        <div class="syscell"><span>API</span><span class="dot on" title="reachable"></span></div>
        <div class="syscell"><span>Live Trading</span><b class="${health.live_trading ? "hc-err" : "hc-ok"}">${health.live_trading ? "ENABLED ⚠" : "DISABLED ✓"}</b></div>
        <div class="syscell"><span>Mode</span><b class="hc-ok">${health.paper_mode ? "PAPER" : text(health.paper_mode)}</b></div>`;
    } catch (e) { showError("system", e); }

    try {
      const sc = await api("/api/autoscalp/selfcheck");
      $("#healthErr").hidden = true;
      $("#healthGen").textContent = sc.generated ? "as of " + timeStr(sc.generated) : "";
      const banner = $("#healthBanner");
      banner.textContent = sc.ok ? "● HEALTHY — engine operational" : "● ATTENTION — one or more checks failing";
      banner.className = "health-banner " + (sc.ok ? "hc-ok" : "hc-err");
      $("#healthChecks").innerHTML = Object.entries(sc.checks || {}).map(([k, v]) =>
        `<div class="syscell"><span>${esc(k)}</span><b class="${healthCheckClass(k, v, sc.market_open)}">${v ? "OK" : "—"}</b></div>`
      ).join("");
      const seg = Object.entries(sc.segments || {}).map(([k, v]) => `${esc(k)} ${esc(String(v).toLowerCase())}`).join(" · ");
      const bars = Object.entries(sc.bars_ready || {}).map(([k, v]) =>
        `${esc(k)} ${v.ready ? "✓" : v.bars_5m + "/20"}`).join("  ");
      $("#healthDetail").innerHTML =
        `<div class="kv"><span>market</span><b>${sc.market_open ? "OPEN" : "closed"} — ${seg || "—"}</b></div>` +
        `<div class="kv"><span>feed age</span><b>${sc.feed_age_sec == null ? "—" : fmt(sc.feed_age_sec, 0) + "s"}</b></div>` +
        `<div class="kv"><span>aggregators</span><b>${bars || "—"}</b></div>` +
        `<div class="kv"><span>open / calib</span><b>${sc.open_positions ?? 0} · ${text(sc.calibration, "prior")}</b></div>` +
        (sc.segments_error ? `<div class="kv"><span>segments err</span><b class="hc-err">${esc(sc.segments_error)}</b></div>` : "") +
        ((sc.config_warnings || []).length
          ? sc.config_warnings.map(w => `<div class="kv"><span>⚠ config</span><b class="hc-warn">${esc(w)}</b></div>`).join("")
          : `<div class="kv"><span>config</span><b class="hc-ok">no warnings</b></div>`);
      const eb = sc.entry_blocks || {};
      $("#healthBlocks").innerHTML = Object.keys(eb).length
        ? `<div class="kv"><span>entry blocks</span><b></b></div>` + Object.entries(eb).map(([s, b]) =>
            `<div class="kv"><span>${esc(s)}</span><b class="hc-warn">${esc(b.signal)} ×${b.n} — ${esc(b.last)}</b></div>`).join("")
        : "";
    } catch (e) {
      const el = $("#healthErr"); el.hidden = false; el.textContent = "selfcheck: " + e.message;
    }
  }

  // ---------------- Session Report ----------------
  function _istToday() {
    const d = new Date(Date.now() + (5 * 60 + 30) * 60000);   // shift to IST
    return d.toISOString().slice(0, 10);
  }
  async function loadReport() {
    const day = ($("#reportDay").value || "").trim() || _istToday();
    const body = $("#reportBody");
    body.innerHTML = `<span class="hint">Loading ${esc(day)}…</span>`;
    $("#reportErr").hidden = true;
    let rep;
    try { rep = await api(`/api/autoscalp/report?day=${encodeURIComponent(day)}`); }
    catch (e) {
      const el = $("#reportErr"); el.hidden = false;
      el.textContent = "report: " + e.message;
      body.innerHTML = `<span class="hint">Could not load. <button class="btn btn-ghost" id="reportRetry" type="button">Retry</button></span>`;
      const rb = $("#reportRetry"); if (rb) rb.addEventListener("click", loadReport);
      return;
    }
    $("#reportNote").textContent = rep.note || "";
    const t = rep.totals || {};
    const syms = rep.per_symbol || {};
    if (!Object.keys(syms).length && !(t.trades)) {
      body.innerHTML = `<span class="hint">No PAPER activity recorded for ${esc(rep.day_ist || day)}.</span>`;
      return;
    }
    const rows = Object.entries(syms).map(([s, v]) => {
      const wr = v.win_rate == null ? "—" : Math.round(v.win_rate * 100) + "%";
      const ex = Object.entries(v.exit_reasons || {}).map(([k, n]) => `${esc(k)}:${n}`).join(" ") || "—";
      const blk = Object.entries(v.entry_blocks || {}).map(([k, n]) => `${esc(k)}×${n}`).join(" ");
      return `<tr>
        <td>${esc(s)}</td>
        <td>${v.closed}${v.open ? ` <span class="hint">+${v.open} open</span>` : ""}</td>
        <td>${v.wins}/${v.losses}${v.flat ? `/${v.flat}` : ""}</td>
        <td>${wr}</td>
        <td class="${v.net_points > 0 ? 'stat-value pos' : v.net_points < 0 ? 'stat-value neg' : ''}">${fmtSigned(v.net_points, 2)}</td>
        <td>${v.avg_r == null ? "—" : fmtSigned(v.avg_r, 2) + "R"}</td>
        <td class="hint">${ex}</td>
        <td class="hint">${blk || "—"}</td>
      </tr>`;
    }).join("");
    const zth = (rep.zero_to_hero || []).map(z =>
      `<tr><td>${esc(z.symbol)}</td><td colspan="7">${esc(z.option_type)}${esc(z.strike)} · ${esc(z.result || "?")} · ${fmtSigned(z.pnl, 2)} · ${esc(z.exit_reason || "")}</td></tr>`).join("");
    body.innerHTML = `
      <p class="hint">${esc(rep.day_ist || day)} · totals: ${t.trades || 0} closed · ${t.wins || 0}W/${t.losses || 0}L${t.flat ? `/${t.flat}F` : ""} · net <b class="${(t.net_points||0) > 0 ? 'stat-value pos' : (t.net_points||0) < 0 ? 'stat-value neg' : ''}">${fmtSigned(t.net_points, 2)}</b> pts</p>
      <div class="table-wrap"><table class="ledger">
        <thead><tr><th>Symbol</th><th>Closed</th><th>W/L/F</th><th>Win%</th><th>Net pts</th><th>Avg R</th><th>Exits</th><th>Blocks</th></tr></thead>
        <tbody>${rows}${zth ? `<tr><td colspan="8" class="hint">Zero-to-hero legs</td></tr>${zth}` : ""}</tbody>
      </table></div>`;
  }

  // ---------------- Auto-Scalp (spec-16) ----------------
  let AS_UNIVERSE = null;
  function asSelectedSymbol() {
    const v = ($("#asSymPick") && $("#asSymPick").value || "").trim().toUpperCase();
    if (v) return v;
    try { return (localStorage.getItem("asSymbol") || "").toUpperCase() || null; } catch (e) { return null; }
  }
  async function asLoadUniverse(defaultSym) {
    try {
      AS_UNIVERSE = AS_UNIVERSE || await api("/api/autoscalp/universe");
    } catch (e) { AS_UNIVERSE = { watchlist: [], groups: {} }; }
    const dl = $("#asUniverseList"); if (!dl) return;
    const wl = AS_UNIVERSE.watchlist || [];
    const g = AS_UNIVERSE.groups || {};
    const opt = (s, lbl) => `<option value="${text(s)}"${lbl ? ` label="${lbl}"` : ""}></option>`;
    dl.innerHTML =
      wl.map(s => opt(s, "★ trading")).join("") +
      Object.entries(g).flatMap(([grp, syms]) =>
        (syms || []).filter(s => !wl.includes(s)).map(s => opt(s, grp))).join("");
    const pick = $("#asSymPick");
    if (pick && !pick.value) pick.value = defaultSym || wl[0] || "NIFTY";
  }

  async function loadAutoscalp() {
    await asLoadUniverse();
    const sym = asSelectedSymbol();
    const q = sym ? `&symbol=${encodeURIComponent(sym)}` : "";
    let st, sigs, snaps, pos, allTr, allSnap, cal, sc;
    try {
      [st, sigs, snaps, pos, allTr, allSnap, cal, sc] = await Promise.all([
        api("/api/autoscalp/status"),
        api(`/api/autoscalp/signals?limit=60${q}`),
        api(`/api/autoscalp/snapshots?limit=60${q}`),
        api("/api/trades?limit=100&status=OPEN"),
        api("/api/trades?limit=300"),
        api("/api/autoscalp/snapshots?limit=40"),
        api("/api/market/calendar").catch(() => null),
        api("/api/autoscalp/selfcheck").catch(() => null),
      ]);
    } catch (e) {
      const el = $("#asErr"); el.hidden = false; el.textContent = "autoscalp: " + e.message; return;
    }
    $("#asErr").hidden = true;
    if (sc) renderHealthLine({ ...sc, paper_mode: st && st.paper_mode });
    const set = (id, v, cls) => { const el = $("#" + id); if (el) { el.textContent = v; if (cls !== undefined) el.className = cls; } };
    // which symbol's analysis is on screen, and is it actually trading?
    const wl = (AS_UNIVERSE && AS_UNIVERSE.watchlist) || [];
    const inWl = sym && wl.includes(sym);
    set("asSymTag", sym ? `${sym} · ${inWl ? "trading" : "view-only"}` : "—");
    const wlb = $("#asWlBtn");
    if (wlb) { wlb.textContent = inWl ? "− Stop" : "+ Trade"; wlb.disabled = !sym; wlb.dataset.in = inWl ? "1" : "0"; }

    // market session status — so MARKET_CLOSED on NIFTY / a live MCX regime make sense
    const ms = $("#asMktStatus");
    if (ms && cal) {
      const seg = cal.segments || {};
      ms.textContent = "Session — " + Object.entries(seg).map(([k, v]) => `${k} ${String(v).replace("_", " ").toLowerCase()}`).join(" · ")
        + (cal.holiday ? " · HOLIDAY" : "");
    }

    // per-symbol watchlist summary — the whole autonomous operation at a glance
    const ws = $("#asWlSummary");
    if (ws) {
      const asc = (allTr || []).filter(t => (t.strategy || "").toUpperCase() === "AUTOSCALP");
      const lastBy = {};
      (allSnap || []).forEach(s => { if (!lastBy[s.symbol]) lastBy[s.symbol] = s; });
      ws.innerHTML = wl.map(s => {
        const rows = asc.filter(t => String(t.underlying || "").toUpperCase() === s);
        const closed = rows.filter(t => String(t.status || "").toUpperCase() !== "OPEN");
        const w = closed.filter(t => (t.pnl || 0) > 0).length, l = closed.filter(t => (t.pnl || 0) < 0).length;
        const net = closed.reduce((a, t) => a + (t.pnl || 0), 0);
        const open = rows.length - closed.length;
        const dec = (lastBy[s] || {}).decision || "—";
        const cls = net > 0 ? "pos" : net < 0 ? "neg" : "";
        const on = s === sym ? " on" : "";
        return `<button class="as-wl-chip${on}" data-sym="${esc(s)}" type="button">
          <b>${esc(s)}</b> <span class="${cls}">${fmtSigned(net, 1)}</span>
          <em>${w}W/${l}L${open ? ` · ${open} open` : ""}</em>
          <i class="badge ${esc(dec)}">${esc(dec)}</i></button>`;
      }).join("");
      ws.querySelectorAll(".as-wl-chip").forEach(b => b.addEventListener("click", () => {
        const pk = $("#asSymPick"); if (pk) { pk.value = b.dataset.sym; try { localStorage.setItem("asSymbol", b.dataset.sym); } catch (e) {} }
        loadAutoscalp();
      }));
    }
    const armed = !!st.armed;
    $("#asArmBtn").textContent = armed ? "DISARM" : "ARM";
    $("#asArmBtn").dataset.armed = String(armed);
    const ks = (st.safeguards && st.safeguards.kill_switch) || {};
    set("asState", `${armed ? "ARMED" : "disarmed"} · leader ${st.is_leader ? "yes" : "no"} · ${st.paper_mode ? "PAPER" : "?"}` +
      (ks.active ? ` · KILL ON` : "") + (st.last_error ? ` · err: ${text(st.last_error)}` : ""), armed ? "on" : "off");
    $("#asKillBtn").textContent = ks.active ? "Kill switch: ON — clear" : "Kill switch: OFF — activate";
    $("#asKillBtn").dataset.active = ks.active ? "1" : "0";

    // Decision-first strip + Current Decision are driven by the latest *evaluation*
    // (live_market_snapshots), which the runner writes every cycle — not only when
    // a paper trade opens (scalp_signals). Fall back to the last trade row if the
    // snapshot stream is empty (e.g. an old DB before this was wired).
    const latest = (snaps || [])[0] || (sigs || [])[0] || {};
    // Do not let a stale / market-closed snapshot render as fresh live data.
    const marketClosed = (sc && sc.market_open === false) || latest.regime === "MARKET_CLOSED";
    const feedStale = latest.feed_age_sec != null && latest.feed_age_sec > 30;
    const stripEl = $("#asStrip");
    if (stripEl) stripEl.className = "mon-strip" + (marketClosed ? " is-closed" : feedStale ? " is-stale" : "");
    set("asRegime", text(latest.regime));
    set("asIndex", fmt(latest.index_ltp, 1));
    // VWAP is volume-weighted — unavailable for a cash index (no volume). Show
    // the real reason instead of a bare dash, never a fabricated number.
    const vwEl = $("#asVwap");
    if (vwEl) {
      if (latest.vwap != null) {
        vwEl.textContent = fmt(latest.vwap, 1);
        vwEl.removeAttribute("title");
      } else {
        const vs = latest.vwap_status;
        vwEl.textContent = vs === "invalid_volume" ? "— n/a (no volume)"
          : vs === "insufficient_data" ? "— warming up"
          : "—";
        vwEl.title = vs === "invalid_volume"
          ? "VWAP needs traded volume; an NSE cash index has none"
          : vs === "insufficient_data" ? "not enough bars yet" : "";
      }
    }
    set("asAtr", fmt(latest.atr, 2));
    set("asSup", fmt(latest.support, 1)); set("asRes", fmt(latest.resistance, 1));
    set("asSupS", fmt(latest.support_strength, 0)); set("asResS", fmt(latest.resistance_strength, 0));
    set("asMtf", fmt(latest.mtf_alignment, 1));
    const sg = st.safeguards || {};
    set("asOpen", `${st.open_positions ?? 0} / ${sg.trades_today ?? 0}`);
    set("asPnl", fmt(sg.realised_pnl_today, 2), (sg.realised_pnl_today > 0 ? "pos" : sg.realised_pnl_today < 0 ? "neg" : ""));
    set("asCalib", text(st.calibration, "prior"));
    const asEval = $("#asEval");
    if (asEval) asEval.textContent = latest.ts ? `last eval ${timeStr(latest.ts)}` +
      (latest.feed_age_sec != null ? ` · feed ${fmt(latest.feed_age_sec, 0)}s` : "") : "no evaluation yet";

    set("asSignal", text(latest.decision), latest.decision === "BUY_CE" ? "on" : latest.decision === "BUY_PE" ? "warn" : "");
    set("asSetup", text(latest.signal_type));
    set("asProb", latest.probability != null ? Math.round(latest.probability * 100) + "%" : "—");
    set("asConf", text(latest.confidence));
    set("asScore", fmt(latest.signal_score, 0));
    set("asRrEv", `${fmt(latest.rr, 2)} / ${fmt(latest.ev, 1)}`);
    set("asReason", text(latest.reason));

    const asMark = {};
    $("#asPosTable tbody").innerHTML = (pos || [])
      .filter(t => t.strategy === "AUTOSCALP" && (!sym || String(t.underlying || "").toUpperCase() === sym))
      .map(t => `
      <tr><td>${timeStr(t.opened_ts)}</td><td>${text(t.underlying)}</td>
      <td>${text(t.option_type)} ${text(t.strike, "")}</td>
      <td class="feed-dir ${directionClass(t.direction)}">${text(t.direction)}</td>
      <td>${fmt(t.entry, 2)}</td><td><b>${fmt(t.exit_price ?? asMark[t.symboltoken], 2)}</b></td>
      <td>${fmt(t.stop_loss, 2)}</td><td>${fmt(t.target_1, 2)}</td>
      <td class="${(t.pnl || 0) > 0 ? 'stat-value pos' : (t.pnl || 0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmtSigned(t.pnl, 2)}</td>
      <td><span class="badge ${esc(t.status)}">${text(t.status)}</span></td><td>${text(t.exit_reason, "")}</td></tr>`).join("")
      || `<tr><td colspan="11" class="hint">No open PAPER positions.</td></tr>`;

    // Signal Log: the actual PAPER trades (locked contract + outcome) come first;
    // if there are none yet, show the recent decision stream so the operator can
    // see the engine is evaluating and *why* it is holding (NO_TRADE / WATCH).
    const sigRows = (sigs || []).map(s => `
      <tr><td>${timeStr(s.created_ts)}</td><td>${text(s.symbol)}</td>
      <td class="badge ${esc(s.decision)}">${text(s.decision)}</td><td>${text(s.signal_type)}</td>
      <td class="feed-dir ${directionClass(s.direction === "BULLISH" ? "BUY" : s.direction === "BEARISH" ? "SELL" : "")}">${text(s.direction)}</td>
      <td>${text(s.opt_strike, "")}</td><td>${fmt(s.entry, 2)}</td><td>${fmt(s.stop_loss, 2)}</td>
      <td>${fmt(s.target_1, 2)}</td><td>${s.probability != null ? Math.round(s.probability * 100) + "%" : "—"}</td>
      <td>${text(s.confidence, "")}</td><td><span class="badge ${esc(s.status)}">${text(s.status)}</span></td>
      <td>${text(s.outcome, "")}</td>
      <td class="${(s.points || 0) > 0 ? 'stat-value pos' : (s.points || 0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmtSigned(s.points, 1)}</td></tr>`).join("");
    const evalRows = (snaps || []).slice(0, 30).map(s => `
      <tr class="hint"><td>${timeStr(s.ts)}</td><td>${text(s.symbol)}</td>
      <td class="badge ${esc(s.decision)}">${text(s.decision)}</td><td>${text(s.signal_type)}</td>
      <td class="feed-dir ${directionClass(s.direction === "BULLISH" ? "BUY" : s.direction === "BEARISH" ? "SELL" : "")}">${text(s.direction)}</td>
      <td>${text(s.atm, "")}</td><td>—</td><td>—</td><td>—</td>
      <td>${s.probability != null ? Math.round(s.probability * 100) + "%" : "—"}</td>
      <td>${text(s.confidence, "")}</td><td>eval</td><td colspan="2">${text(s.reason, "")}</td></tr>`).join("");
    $("#asSigTable tbody").innerHTML = sigRows || evalRows ||
      `<tr><td colspan="14" class="hint">No evaluations yet.</td></tr>`;

    $("#asGuards").innerHTML = Object.entries(sg.config || {}).map(([k, v]) =>
      `<div class="syscell"><span>${text(k)}</span><span>${text(typeof v === "object" ? JSON.stringify(v) : v)}</span></div>`).join("")
      + `<div class="syscell"><span>consecutive_losses</span><span>${sg.consecutive_losses ?? 0}</span></div>`
      + `<div class="syscell"><span>halt_reason</span><span>${text(sg.halt_reason, "none")}</span></div>`;
  }

  (function bindAutoscalp() {
    const arm = $("#asArmBtn"), kill = $("#asKillBtn");
    if (arm) arm.addEventListener("click", async () => {
      const on = arm.dataset.armed === "true";
      try { await api(on ? "/api/autoscalp/disarm" : "/api/autoscalp/arm", { method: "POST" }); }
      catch (e) { alert("autoscalp: " + e.message); }
      loadAutoscalp();
    });
    if (kill) kill.addEventListener("click", async () => {
      const turnOn = kill.dataset.active !== "1";
      if (turnOn && !confirm("Activate the emergency kill switch? Blocks all new auto-scalp entries.")) return;
      try { await api("/api/autoscalp/kill", { method: "POST", body: JSON.stringify({ active: turnOn, reason: "dashboard" }) }); }
      catch (e) { alert("kill: " + e.message); }
      loadAutoscalp();
    });
    const pick = $("#asSymPick");
    if (pick) {
      const onPick = () => {
        const v = pick.value.trim().toUpperCase();
        if (v) { try { localStorage.setItem("asSymbol", v); } catch (e) {} }
        loadAutoscalp();
      };
      pick.addEventListener("change", onPick);
      pick.addEventListener("input", () => {
        const opts = Array.from($("#asUniverseList") ? $("#asUniverseList").options : []).map(o => o.value.toUpperCase());
        if (opts.includes(pick.value.trim().toUpperCase())) onPick();
      });
    }
    const wlb = $("#asWlBtn");
    if (wlb) wlb.addEventListener("click", async () => {
      const sym = (pick && pick.value || "").trim().toUpperCase();
      if (!sym) return;
      const action = wlb.dataset.in === "1" ? "remove" : "add";
      if (action === "add" && !confirm(`Add ${sym} to the trading watchlist? It will scalp PAPER on its own profile.`)) return;
      if (action === "remove" && !confirm(`Stop trading ${sym}? Open positions keep being monitored to exit.`)) return;
      try {
        await api("/api/autoscalp/watchlist", { method: "POST", body: JSON.stringify({ symbol: sym, action }) });
        AS_UNIVERSE = null;                       // force universe refetch (watchlist changed)
      } catch (e) { alert("watchlist: " + e.message); }
      loadAutoscalp();
    });
  })();

  // A burst of autoscalp_* WS events (common in an active session) used to fire
  // loadAutoscalp() — 8 parallel requests — once per event. Coalesce them.
  let _asReloadTimer = null;
  function scheduleAutoscalpReload() {
    if (_asReloadTimer) return;
    _asReloadTimer = setTimeout(() => { _asReloadTimer = null; loadAutoscalp(); }, 500);
  }
  const _origHandleWs = handleWsMessage;
  handleWsMessage = function (msg) {
    _origHandleWs(msg);
    if (state.view === "autoscalp" && /^autoscalp_/.test(msg.type || "")) scheduleAutoscalpReload();
  };

  // ---------------- Session Report bindings ----------------
  (function bindReport() {
    const day = $("#reportDay"), btn = $("#reportLoadBtn");
    if (day && !day.value) day.value = _istToday();
    if (btn) btn.addEventListener("click", loadReport);
    if (day) day.addEventListener("change", loadReport);
  })();

  // ================= Advanced Mathematical Scalper (slice 6/6) =================
  // RESEARCH / PAPER. Renders the mathematical-confluence + smart-index-scalper
  // engines + the strict-causal replay. Never claims a probability from the
  // confluence score; always shows the sub-score breakdown + reasons + risks.
  const msDir = d => d === "CE" ? "BUY" : d === "PE" ? "SELL" : "UNKNOWN";
  const fmtK = n => (n === null || n === undefined || isNaN(Number(n))) ? "—"
    : Math.abs(n) >= 1e5 ? (n / 1e5).toFixed(2) + "L"
    : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(Math.round(n));
  let MS_PROFILES = null, _msReplayBusy = false, _msBusy = false, _msRankBusy = false;
  let _msBusyAt = 0, _msRankBusyAt = 0;          // watchdog timestamps

  // Every Math Scalper fetch goes through this: a hung request (common right
  // after a backend restart / dropped connection — i.e. an "auto-refresh"
  // moment) would otherwise never settle, wedging the busy guard and stalling
  // the whole page until a manual reload. Promise.race rejects after `ms` so
  // the view degrades gracefully instead.
  function _msFetch(path, ms) {
    return Promise.race([
      api(path),
      new Promise((_, rej) => setTimeout(() => rej(new Error("timeout: " + path)), ms || 12000)),
    ]);
  }
  // In-place data refresh: the 20s poll re-renders every panel, but only touch
  // the DOM when the HTML actually changed — an unchanged poll is then a no-op
  // (no flash, no scroll jump, no lost hover/selection). "Data refreshes, the
  // page doesn't."
  function _msSet(sel, html) {
    const el = typeof sel === "string" ? $(sel) : sel;
    if (el && el.innerHTML !== html) el.innerHTML = html;
  }
  function _msText(sel, txt) {
    const el = typeof sel === "string" ? $(sel) : sel;
    if (el && el.textContent !== txt) el.textContent = txt;
  }

  // ---------------- Focus index selector (custom combobox) ----------------
  // The native <datalist> was unreliable as a searchable selector (esp. Android
  // Chrome): no visible dropdown, inconsistent partial-match, no styling. This
  // is a small self-contained combobox over a data-driven universe spanning
  // NSE / BSE / MCX indices — the same span Auto-Scalp offers. Filtering is
  // local; the ranking API is only hit on an actual valid selection change.
  const MS_EXCH = {
    SENSEX: "BSE", BANKEX: "BSE",
    NATURALGAS: "MCX", CRUDEOIL: "MCX", GOLD: "MCX", SILVER: "MCX",
    CRUDEOILMINI: "MCX", NATGASMINI: "MCX", GOLDMINI: "MCX", SILVERMINI: "MCX",
  };
  const msExchOf = s => MS_EXCH[String(s || "").toUpperCase()] || "NSE";
  // static seed so the selector is searchable before any API responds
  const MS_SEED_UNIVERSE = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                            "SENSEX", "BANKEX", "NATURALGAS", "CRUDEOIL"];
  const _msUni = new Map();                       // sym -> exchange, insertion-ordered
  MS_SEED_UNIVERSE.forEach(s => _msUni.set(s, msExchOf(s)));
  let selectedFocusIndex = "NIFTY";               // the one canonical selection
  let _msReqSeq = 0;                              // request-identity guard (stale-response protection)
  let _msMenuIdx = -1;                            // keyboard highlight position

  function msAddToUniverse(list) {
    (list || []).forEach(s => {
      const k = String(s || "").toUpperCase().trim();
      if (k && !_msUni.has(k)) _msUni.set(k, msExchOf(k));
    });
  }
  function msUniverseList() { return [..._msUni.keys()]; }
  function msFilterUniverse(q) {
    const t = String(q || "").trim().toLowerCase();
    const all = msUniverseList();
    return t ? all.filter(s => s.toLowerCase().includes(t)) : all;
  }
  function msSetFocusMsg(txt) {
    const el = $("#msFocusMsg"); if (!el) return;
    if (txt) { el.textContent = txt; el.hidden = false; }
    else { el.textContent = ""; el.hidden = true; }
  }
  function msCloseMenu() {
    const m = $("#msFocusMenu"); if (m) m.hidden = true;
    const i = $("#msFocus"); if (i) i.setAttribute("aria-expanded", "false");
    _msMenuIdx = -1;
  }
  function msRenderMenu(q) {
    const menu = $("#msFocusMenu"); if (!menu) return;
    _msMenuIdx = -1;
    const matches = msFilterUniverse(q);
    if (!matches.length) {
      menu.innerHTML = `<li class="ms-combo-empty" role="presentation">no matching index — pick from the list</li>`;
    } else {
      const byEx = { NSE: [], BSE: [], MCX: [] };
      matches.forEach(s => { (byEx[msExchOf(s)] || (byEx[msExchOf(s)] = [])).push(s); });
      let html = "", i = 0;
      ["NSE", "BSE", "MCX"].forEach(ex => {
        (byEx[ex] || []).forEach((s, k) => {
          if (k === 0) html += `<li class="ms-combo-grp" role="presentation">${ex}</li>`;
          html += `<li class="ms-combo-item" role="option" id="msOpt-${i}" data-idx="${esc(s)}"` +
                  ` aria-selected="${s === selectedFocusIndex ? "true" : "false"}">${esc(s)}</li>`;
          i++;
        });
      });
      menu.innerHTML = html;
    }
    menu.hidden = false;
    const inp = $("#msFocus"); if (inp) inp.setAttribute("aria-expanded", "true");
  }
  function msMenuItems() {
    const menu = $("#msFocusMenu");
    return menu && menu.querySelectorAll ? Array.from(menu.querySelectorAll(".ms-combo-item")) : [];
  }
  function msHighlight(delta) {
    const items = msMenuItems(); if (!items.length) return;
    _msMenuIdx = (_msMenuIdx + delta + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle("is-active", i === _msMenuIdx));
    const cur = items[_msMenuIdx];
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
    const inp = $("#msFocus"); if (inp && cur) inp.setAttribute("aria-activedescendant", cur.id || "");
  }
  // Canonical selection commit. Validates against the supported universe, updates
  // the one selectedFocusIndex, refreshes the input, and triggers exactly one
  // ranking scan for the new index (never for an unsupported or empty value).
  function msCommitFocus(raw) {
    const idx = String(raw == null ? "" : raw).toUpperCase().trim();
    const inp = $("#msFocus");
    msCloseMenu();
    if (!idx) {
      msSetFocusMsg("Select an index to scan.");
      if (inp) inp.value = selectedFocusIndex;
      return false;
    }
    if (!_msUni.has(idx)) {
      msSetFocusMsg("Select a supported index from the list.");
      if (inp) inp.value = selectedFocusIndex;
      return false;
    }
    msSetFocusMsg("");
    if (inp) inp.value = idx;
    if (idx === selectedFocusIndex) return true;   // no duplicate scan
    selectedFocusIndex = idx;
    loadMathScalp({ force: true, focusChanged: true });
    return true;
  }

  // The ranking scan does a live broker fetch per index — fast when the feed is
  // warm, but ~20s cold (markets closed). Load it on its own so the rest of the
  // Math Scalper view paints immediately, and never stack overlapping scans.
  async function loadMsRanking(prof, reqToken) {
    if (_msRankBusy && Date.now() - _msRankBusyAt < 45000) return;  // watchdog: take over a wedged scan
    _msRankBusy = true; _msRankBusyAt = Date.now();
    const tb = $("#msRankTable tbody");
    if (tb && !tb.innerHTML.trim()) tb.innerHTML = `<tr><td colspan="10" class="hint">scanning the index universe… (slow while markets are closed)</td></tr>`;
    try {
      const ranking = await _msFetch(`/api/smart-scalper/ranking?profile=${encodeURIComponent(prof)}`, 30000);
      // a newer Focus/Profile selection has superseded this scan — drop its result
      if (reqToken != null && reqToken !== _msReqSeq) return ranking;
      if (ranking && ranking.calibration) $("#msCalib").textContent = ranking.calibration;
      const ranked = (ranking && ranking.ranked) || [];
      const notElig = (ranking && ranking.not_eligible) || [];
      const rankRows = ranked.map((r, i) => `<tr>
        <td>${i + 1}</td><td><b>${text(r.index)}</b></td><td>${fmt(r.score, 1)}</td>
        <td><span class="badge ${esc(r.signal_type)}">${text(r.signal_type)}</span></td>
        <td class="feed-dir ${msDir(r.direction)}">${text(r.direction)}</td>
        <td>${text(r.confidence)}</td><td>${fmt(r.confluence_score, 1)}</td><td>${text(r.market_regime)}</td>
        <td>${fmt((r.risk_reward || [])[0], 2)}</td><td class="feed-dir BUY">eligible</td></tr>`).join("");
      const neRows = notElig.map(r => `<tr class="hint">
        <td>·</td><td>${text(r.index)}</td><td>${fmt(r.score, 1)}</td><td colspan="6">—</td>
        <td>${(r.failed || []).map(f => `<span class="ms-tag res">${esc(f)}</span>`).join(" ") || text(r.status)}</td></tr>`).join("");
      _msSet(tb, (rankRows + neRows) || `<tr><td colspan="10" class="hint">No scan result.</td></tr>`);
      return ranking;
    } catch (e) {
      if (tb) tb.innerHTML = `<tr><td colspan="10" class="hint">ranking scan failed: ${esc(e.message)}</td></tr>`;
    } finally { _msRankBusy = false; }
  }

  function msBucketRows(mtx) {
    const out = [];
    const push = (bucket, group, m) => {
      if (!m || !m.n) return;
      out.push(`<tr><td>${esc(bucket)}</td><td>${esc(group)}</td><td>${m.n}</td>
        <td>${m.win_rate != null ? (m.win_rate * 100).toFixed(0) + "%" : "—"}</td>
        <td>${fmt(m.profit_factor, 2)}</td><td>${fmtSigned(m.expectancy, 2)}</td>
        <td>${fmt(m.avg_r_multiple, 2)}</td><td>${fmtSigned(m.max_drawdown, 1)}</td></tr>`);
    };
    push("OVERALL", "all", mtx.overall);
    Object.entries(mtx.by_profile || {}).forEach(([k, v]) => push("profile", k, v));
    Object.entries(mtx.by_instrument || {}).forEach(([k, v]) => push("instrument", k, v));
    Object.entries(mtx.by_market_regime || {}).forEach(([k, v]) => push("regime", k, v));
    return out.join("");
  }

  function renderMsBest(focus, sig, primary) {
    const box = $("#msBest"), why = $("#msWhy"), inval = $("#msInvalidate"), subs = $("#msSubscores");
    const src = sig && (sig.spot_source || "").replace("ACTUAL ", "").replace(/[()]/g, "");
    const feedChip = src ? ` · feed: ${esc(src)}${sig && sig.stale ? " · STALE" : ""}` : "";
    _msText("#msBestMeta", focus + " · score is NOT a probability" + feedChip);
    if (!sig || sig.status !== "OK") {
      const s = sig && sig.status;
      const msg = (!s || s === "DATA_INSUFFICIENT")
        ? `${esc(focus)} — waiting for live market data (broker feed warming up). Retrying automatically…`
        : `${esc(focus)}: ${text(s, "no data")}${(sig && sig.missing || []).length ? " — needs " + esc(sig.missing.join(", ")) : ""}`;
      _msSet(box, `<span class="hint">${msg}</span>`);
      _msSet(why, `<li class="hint">—</li>`); _msSet(inval, `<li class="hint">—</li>`); _msSet(subs, "");
      return;
    }
    const dir = sig.direction || "NONE", st = sig.signal_type || "NO_TRADE", rr = sig.risk_reward || [];
    const so = (primary && primary.index === focus && primary.selected_option) || null;
    _msSet(box, `
      <span class="ms-dir ${esc(dir)}">${text(st)}</span>
      <span class="kv"><span>Direction</span><b>${text(dir)}</b></span>
      <span class="kv"><span>Confidence</span><b>${text(sig.confidence)}</b></span>
      <span class="kv"><span>Confluence</span><b>${fmt(sig.confluence_score, 1)} / 100</b></span>
      <span class="kv"><span>Spot</span><b>${fmt(sig.spot, 1)}</b></span>
      <span class="kv"><span>Entry zone</span><b>${(sig.entry_zone || []).map(x => fmt(x, 1)).join(" – ") || "—"}</b></span>
      <span class="kv"><span>Stop</span><b class="neg">${fmt(sig.stop_loss, 1)}</b></span>
      <span class="kv"><span>T1 / T2 / T3</span><b class="pos">${[sig.target_1, sig.target_2, sig.target_3].map(x => fmt(x, 1)).join(" / ")}</b></span>
      <span class="kv"><span>RR 1/2/3</span><b>${rr.map(x => fmt(x, 2)).join(" / ") || "—"}</b></span>
      ${so ? `<span class="kv"><span>Option leg</span><b>${text(so.selected_strike)} ${text(so.option_type)} @ ${fmt(so.option_ltp, 2)} · sel ${fmt(so.selection_score, 0)}</b></span>` : ``}`);
    const rc = sig.reason_codes || [];
    _msSet(why, rc.length ? rc.map(x => `<li>${esc(x)}</li>`).join("") : `<li class="hint">no confirming evidence yet</li>`);
    const invs = [];
    if (sig.no_trade_reason) invs.push("NOT a trade now — " + sig.no_trade_reason);
    if (sig.support_level && dir === "CE") invs.push(`a decisive close below ${fmt(sig.support_level, 1)} (nearest support zone) breaks the long-CE thesis`);
    if (sig.resistance_level && dir === "PE") invs.push(`a decisive close above ${fmt(sig.resistance_level, 1)} (nearest resistance zone) breaks the short-PE thesis`);
    const w = (sig.oi_matrix && sig.oi_matrix.walls) || {};
    if (w.CALL_RESISTANCE_WALL) invs.push(`heavy CALL wall @ ${text(w.CALL_RESISTANCE_WALL.strike)} caps upside`);
    if (w.PUT_SUPPORT_WALL) invs.push(`heavy PUT wall @ ${text(w.PUT_SUPPORT_WALL.strike)} caps downside`);
    if ((sig.oi_matrix || {}).battle_zone) invs.push("BATTLE ZONE — CE & PE both building; direction unresolved");
    invs.push("UNCALIBRATED thresholds — treat this as a hypothesis, not an established edge");
    _msSet(inval, invs.map(x => `<li>${esc(x)}</li>`).join(""));
    const bd = sig.score_breakdown || {};
    _msSet(subs, Object.entries(bd).map(([k, v]) => {
      const fillPct = v.out_of ? Math.max(0, Math.min(100, 100 * v.raw / v.out_of)) : 0;
      return `<div class="ms-sub"><div class="ms-sub-head"><span>${esc(k)}</span><span>${fmt(v.raw, 1)}/${text(v.out_of)}</span></div>
        <div class="ms-track"><div class="ms-fill" style="width:${fillPct.toFixed(0)}%"></div></div></div>`;
    }).join("") || `<span class="hint">no sub-score breakdown</span>`);
  }

  function renderMsLadder(focus, sig, oi, lv) {
    const el = $("#msLadder");
    _msText("#msLadderSym", focus);
    if (!sig || sig.status !== "OK" || sig.spot == null) { _msSet(el, `<span class="hint">no data</span>`); return; }
    const spot = Number(sig.spot), rungs = {};
    const add = (price, tag, cls) => {
      if (price == null || isNaN(Number(price))) return;
      const k = Number(price).toFixed(1);
      (rungs[k] = rungs[k] || []).push({ tag, cls: cls || "" });
    };
    const piv = (lv && lv.pivots) || (sig.mathematical_levels && sig.mathematical_levels.pivots) || {};
    add(piv.pivot, "PIVOT", ""); add(piv.r1, "R1", "res"); add(piv.r2, "R2", "res"); add(piv.r3, "R3", "res");
    add(piv.s1, "S1", "sup"); add(piv.s2, "S2", "sup"); add(piv.s3, "S3", "sup");
    const g = (lv && lv.gann) || (sig.mathematical_levels && sig.mathematical_levels.gann) || {};
    add(g.gann_balance, "GANN bal", "");
    [1, 2, 3, 4].forEach(k => { add(g["gann_up_" + k], "GANN+" + k, "res"); add(g["gann_down_" + k], "GANN-" + k, "sup"); });
    (sig.confluence_zones || []).forEach(z => add(z.center, `ZONE×${z.evidence_count}`, "zone"));
    const w = (oi && oi.walls) || {};
    if (w.CALL_RESISTANCE_WALL) add(w.CALL_RESISTANCE_WALL.strike, "CALL WALL", "wall");
    if (w.PUT_SUPPORT_WALL) add(w.PUT_SUPPORT_WALL.strike, "PUT WALL", "wall");
    (w.top3_strikes || []).forEach(s => add(s && (s.strike ?? s), "OI", "wall"));
    const spotKey = spot.toFixed(1);
    let keys = Object.keys(rungs).map(Number);
    const near = keys.filter(k => Math.abs(k - spot) <= spot * 0.012);
    keys = (near.length ? near : keys).sort((a, b) => b - a).slice(0, 32);
    if (!keys.includes(spot)) keys.push(spot);
    keys = Array.from(new Set(keys.map(k => Number(k.toFixed(1))))).sort((a, b) => b - a);
    _msSet(el, keys.map(k => {
      const kk = k.toFixed(1), isSpot = kk === spotKey;
      const tags = (rungs[kk] || []).map(t => `<span class="ms-tag ${t.cls}">${esc(t.tag)}</span>`).join("");
      return `<div class="ms-rung${isSpot ? " is-spot" : ""}"><span class="ms-price">${kk}</span>
        <span class="ms-tags">${isSpot ? '<span class="ms-tag">▶ SPOT</span>' : ""}${tags}</span></div>`;
    }).join("") || `<span class="hint">no levels</span>`);
  }

  function renderMsOi(oi) {
    const meta = $("#msOiMeta"), tb = $("#msOiTable tbody");
    if (!oi || oi.status !== "OK") { _msText(meta, text(oi && oi.status, "no data")); _msSet(tb, `<tr><td colspan="8" class="hint">—</td></tr>`); return; }
    const w = oi.walls || {};
    _msText(meta, `PCR ${fmt(oi.pcr, 2)} · ${oi.battle_zone ? "BATTLE ZONE" : "no battle zone"} · `
      + `CALL wall ${text((w.CALL_RESISTANCE_WALL || {}).strike, "—")} · PUT wall ${text((w.PUT_SUPPORT_WALL || {}).strike, "—")}`);
    const rows = (oi.rows || []).slice().sort((a, b) =>
      (b.support_score + b.resistance_score + b.battle_score) - (a.support_score + a.resistance_score + a.battle_score)).slice(0, 14);
    _msSet(tb, rows.map(r => `<tr>
      <td><b>${fmt(r.strike, 0)}</b></td>
      <td>${fmtK(r.ce_oi)}</td><td>${fmt(r.ce_ltp, 2)}</td>
      <td>${fmtK(r.pe_oi)}</td><td>${fmt(r.pe_ltp, 2)}</td>
      <td class="${r.support_score > 1 ? "feed-dir BUY" : ""}">${fmt(r.support_score, 1)}</td>
      <td class="${r.resistance_score > 1 ? "feed-dir SELL" : ""}">${fmt(r.resistance_score, 1)}</td>
      <td>${fmt(r.battle_score, 1)}</td></tr>`).join("") || `<tr><td colspan="8" class="hint">—</td></tr>`);
  }

  function renderMsJournal(j) {
    if (!j) return;
    _msText("#msJournalNote", j.note || "");
    _msSet("#msJournalTable tbody", msBucketRows({
      overall: j.overall, by_profile: j.by_profile,
      by_instrument: j.by_instrument, by_market_regime: j.by_market_regime,
    }) || `<tr><td colspan="8" class="hint">No closed paper trades yet.</td></tr>`);
  }

  async function runMsReplay() {
    if (_msReplayBusy) return;
    _msReplayBusy = true;
    const btn = $("#msReplayBtn"); if (btn) { btn.disabled = true; btn.textContent = "Running…"; }
    const prof = ($("#msProfile") || {}).value || "BALANCED";
    try {
      const r = await api(`/api/smart-scalper/replay?profile=${encodeURIComponent(prof)}&step_min=3`);
      $("#msReplayStrip").hidden = false;
      const m = (r.metrics && r.metrics.overall) || {};
      const set = (id, v) => { const e = $("#" + id); if (e) e.textContent = v; };
      set("msRpStatus", r.status); const se = $("#msRpStatus"); if (se) se.className = "badge " + esc(r.status);
      set("msRpSessions", `${(r.sample || {}).sessions} / ${(r.sample || {}).min_sessions}`);
      set("msRpTrades", `${(r.sample || {}).trades} / ${(r.sample || {}).min_trades}`);
      set("msRpWin", m.win_rate != null ? (m.win_rate * 100).toFixed(0) + "%" : "—");
      set("msRpPF", fmt(m.profit_factor, 2));
      set("msRpExp", fmtSigned(m.expectancy, 2));
      set("msRpDD", fmtSigned(m.max_drawdown, 1));
      set("msRpCalib", (r.calibration && (r.calibration.verdict || r.calibration.status)) || "—");
      $("#msReplayNote").textContent = r.note || "";
      $("#msReplayByWrap").hidden = false;
      $("#msReplayByTable tbody").innerHTML = msBucketRows(r.metrics || {})
        || `<tr><td colspan="8" class="hint">0 simulated trades on the captured sample — the engine did not confirm an entry with stock-profile gates (see note).</td></tr>`;
    } catch (e) { showError("replay", e); }
    finally { _msReplayBusy = false; if (btn) { btn.disabled = false; btn.textContent = "Run backtest"; } }
  }

  async function loadMathScalp(opts) {
    const { force = false, focusChanged = false } = opts || {};
    // watchdog: a poll normally yields while another load is in flight, but if a
    // previous run wedged (a hung request that never settled — typical right
    // after a backend restart) take over instead of stalling forever.
    if (_msBusy && !force && Date.now() - _msBusyAt < 40000) return;
    const myReq = ++_msReqSeq;               // this call's identity; a newer call invalidates it
    const stale = () => myReq !== _msReqSeq;
    _msBusy = true; _msBusyAt = Date.now();
    const prof = ($("#msProfile") && $("#msProfile").value) || "BALANCED";
    const focus = selectedFocusIndex || "NIFTY";

    // keep the visible input in sync with the canonical selection (unless the
    // user is mid-type in it)
    const fEl = $("#msFocus");
    if (fEl && fEl.value !== focus &&
        !(typeof document !== "undefined" && document.activeElement === fEl)) fEl.value = focus;

    if (focusChanged) {
      // never leave a previous index's result on screen while the new one loads
      $("#msBest").innerHTML = `<span class="hint">scanning ${esc(focus)}…</span>`;
      $("#msWhy").innerHTML = $("#msInvalidate").innerHTML = `<li class="hint">—</li>`;
      $("#msSubscores").innerHTML = "";
      $("#msLadder").innerHTML = `<span class="hint">scanning ${esc(focus)}…</span>`;
      $("#msOiTable tbody").innerHTML = `<tr><td colspan="8" class="hint">scanning ${esc(focus)}…</td></tr>`;
      $("#msRankTable tbody").innerHTML = `<tr><td colspan="10" class="hint">scanning ${esc(focus)} + the index universe…</td></tr>`;
    }

    try {
      // every leg is individually guarded + timed out, so one slow/hung/failed
      // endpoint degrades its own panel instead of stalling the whole view
      const [profiles, map, journal, auni] = await Promise.all([
        MS_PROFILES ? Promise.resolve(MS_PROFILES)
          : _msFetch("/api/smart-scalper/profiles").catch(() => null),
        _msFetch("/api/mathematics/market-map").catch(() => null),
        _msFetch("/api/smart-scalper/paper/journal").catch(() => null),
        _msFetch("/api/autoscalp/universe").catch(() => null),
      ]);
      if (stale()) return;
      if (!profiles && !map && !journal && !auni) {
        const el = $("#msErr");
        if (el) { el.hidden = false; el.textContent = "math scalper: data unavailable — retrying on the next refresh"; }
        return;
      }
      MS_PROFILES = profiles || MS_PROFILES;
      $("#msErr").hidden = true;
      $("#msClock").textContent = "updated " + timeStr(new Date().toISOString());

      // grow the Focus universe from every source — never shrink it
      const mm = (map && map.market_map) || [];
      msAddToUniverse(mm.map(r => r.instrument));
      if (auni && auni.groups) {
        msAddToUniverse(auni.groups["NSE Index"]);
        msAddToUniverse(auni.groups["MCX"]);
      }

      _msSet("#msMapTable tbody", mm.map(r => `<tr>
        <td><b>${text(r.instrument)}</b></td><td>${fmt(r.spot, 1)}</td><td>${fmt(r.pivot, 1)}</td>
        <td>${fmt(r.gann_balance, 1)}</td><td>${fmt(r.nearest_support, 1)}</td><td>${fmt(r.nearest_resistance, 1)}</td>
        <td>${text(r.market_regime)}</td><td class="feed-dir ${msDir(r.direction)}">${text(r.direction)}</td>
        <td>${fmt(r.confluence_score, 1)}</td><td>${text(r.confidence)}</td>
        <td><span class="badge ${esc(r.signal)}">${text(r.signal)}</span></td></tr>`).join("")
        || `<tr><td colspan="11" class="hint">—</td></tr>`);
      if (journal) renderMsJournal(journal);

      const [sig, oi, lv] = await Promise.all([
        _msFetch(`/api/mathematics/signal?symbol=${encodeURIComponent(focus)}`).catch(() => null),
        _msFetch(`/api/mathematics/oi?symbol=${encodeURIComponent(focus)}`).catch(() => null),
        _msFetch(`/api/mathematics/levels?symbol=${encodeURIComponent(focus)}`).catch(() => null),
      ]);
      if (stale()) return;                   // a newer Focus/Profile won — do not paint

      renderMsBest(focus, sig, null);
      renderMsLadder(focus, sig, oi, lv);
      renderMsOi(oi);

      // ranking scan runs on its own (slow when the feed is cold); its result is
      // dropped if a newer selection has superseded this request.
      loadMsRanking(prof, myReq).then(ranking => {
        if (myReq !== _msReqSeq) return;
        if (ranking && ranking.universe) msAddToUniverse(ranking.universe);
        const primary = (ranking && ranking.selection && ranking.selection.primary) || null;
        if (primary && primary.index === focus) renderMsBest(focus, sig, primary);
      }).catch(() => {});
    } catch (e) {
      console.error("loadMathScalp", e);      // never let a poll reject unhandled
    } finally {
      // ALWAYS release the guard — a conditional release plus a hung request was
      // the "page stalls after auto-refresh" bug. stale() already stops an old
      // call from painting, so an unconditional release is safe.
      _msBusy = false;
    }
  }

  (function bindMathScalp() {
    // Profile and Refresh reload the data but MUST NOT touch selectedFocusIndex.
    const p = $("#msProfile");
    if (p) p.addEventListener("change", () => loadMathScalp({ force: true }));
    const rb = $("#msRefresh");
    if (rb) rb.addEventListener("click", () => loadMathScalp({ force: true }));
    const rp = $("#msReplayBtn");
    if (rp) rp.addEventListener("click", runMsReplay);

    // --- Focus combobox ---
    const inp = $("#msFocus");
    if (inp) {
      inp.value = selectedFocusIndex;
      // opening (focus / click) shows the WHOLE list; typing filters it
      const openAll = () => { try { inp.select(); } catch (e) {} msRenderMenu(""); };
      inp.addEventListener("focus", openAll);
      inp.addEventListener("click", () => {
        const menu = $("#msFocusMenu");
        if (menu && menu.hidden) msRenderMenu(inp.value); // reopen after an outside-click close
      });
      inp.addEventListener("input", () => { msSetFocusMsg(""); msRenderMenu(inp.value); });
      inp.addEventListener("keydown", (e) => {
        const menu = $("#msFocusMenu");
        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (!menu || menu.hidden) msRenderMenu(inp.value); else msHighlight(1);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          if (!menu || menu.hidden) msRenderMenu(inp.value); else msHighlight(-1);
        } else if (e.key === "Enter") {
          e.preventDefault();
          const items = msMenuItems();
          const pick = (_msMenuIdx >= 0 && items[_msMenuIdx] && items[_msMenuIdx].dataset)
            ? items[_msMenuIdx].dataset.idx : inp.value;
          msCommitFocus(pick);                 // explicit commit: shows a msg on bad input
        } else if (e.key === "Escape") {
          msCloseMenu(); inp.value = selectedFocusIndex; msSetFocusMsg("");
        }
      });
      // leaving the field without an explicit pick: silently snap back to the
      // current valid selection (no scary validation message).
      inp.addEventListener("blur", () => setTimeout(() => {
        const menu = $("#msFocusMenu");
        if (menu && !menu.hidden) return;      // a menu click is handling the commit
        const v = (inp.value || "").toUpperCase().trim();
        if (v && v !== selectedFocusIndex && _msUni.has(v)) { msCommitFocus(v); return; }
        inp.value = selectedFocusIndex; msSetFocusMsg("");
      }, 150));
    }
    const menu = $("#msFocusMenu");
    if (menu) {
      // keep input focus so blur doesn't fire before the click
      menu.addEventListener("mousedown", (e) => e.preventDefault());
      menu.addEventListener("click", (e) => {
        const li = e.target && e.target.closest ? e.target.closest(".ms-combo-item") : null;
        if (li && li.dataset && li.dataset.idx) msCommitFocus(li.dataset.idx);
      });
    }
    if (typeof document !== "undefined" && document.addEventListener) {
      document.addEventListener("click", (e) => {
        const combo = $("#msFocusCombo");
        if (combo && combo.contains && e.target && !combo.contains(e.target)) msCloseMenu();
      });
    }
  })();

  // ================= ORDER FLOW =================
  // Volume Profile + Market Profile (TPO) + smart-money (volume-spike breakout)
  // signals, read from /api/orderflow/* over captured OHLCV bars. Its own small
  // NSE/BSE/MCX index picker (reuses msExchOf for exchange grouping but keeps a
  // separate selection so it never fights the Math Scalper's focus).
  const OF_SEED = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX",
                   "NATURALGAS", "CRUDEOIL"];
  const _ofUni = new Map();
  OF_SEED.forEach(s => _ofUni.set(s, msExchOf(s)));
  let ofSymbol = "NIFTY";
  let ofDate = "";                 // selected session; "" -> newest available
  let _ofMenuIdx = -1;
  let _ofReq = 0;

  function ofAddUni(list) {
    (list || []).forEach(s => {
      const k = String(s || "").toUpperCase().trim();
      if (k && !_ofUni.has(k)) _ofUni.set(k, msExchOf(k));
    });
  }
  function ofFilter(q) {
    const t = String(q || "").trim().toLowerCase();
    const all = [..._ofUni.keys()];
    return t ? all.filter(s => s.toLowerCase().includes(t)) : all;
  }
  function ofSymMsg(txt) {
    const el = $("#ofSymMsg"); if (!el) return;
    if (txt) { el.textContent = txt; el.hidden = false; } else { el.textContent = ""; el.hidden = true; }
  }
  function ofCloseMenu() {
    const m = $("#ofSymMenu"); if (m) m.hidden = true;
    const i = $("#ofSym"); if (i) i.setAttribute("aria-expanded", "false");
    _ofMenuIdx = -1;
  }
  function ofRenderMenu(q) {
    const menu = $("#ofSymMenu"); if (!menu) return;
    _ofMenuIdx = -1;
    const matches = ofFilter(q);
    if (!matches.length) {
      menu.innerHTML = `<li class="ms-combo-empty" role="presentation">no match — pick from the list</li>`;
    } else {
      const byEx = { NSE: [], BSE: [], MCX: [] };
      matches.forEach(s => { (byEx[msExchOf(s)] || (byEx[msExchOf(s)] = [])).push(s); });
      let html = "";
      ["NSE", "BSE", "MCX"].forEach(ex => {
        (byEx[ex] || []).forEach((s, k) => {
          if (k === 0) html += `<li class="ms-combo-grp" role="presentation">${ex}</li>`;
          html += `<li class="ms-combo-item" role="option" data-idx="${esc(s)}"` +
                  ` aria-selected="${s === ofSymbol ? "true" : "false"}">${esc(s)}</li>`;
        });
      });
      menu.innerHTML = html;
    }
    menu.hidden = false;
    const inp = $("#ofSym"); if (inp) inp.setAttribute("aria-expanded", "true");
  }
  function ofMenuItems() {
    const m = $("#ofSymMenu");
    return m && m.querySelectorAll ? Array.from(m.querySelectorAll(".ms-combo-item")) : [];
  }
  function ofHighlight(delta) {
    const items = ofMenuItems(); if (!items.length) return;
    _ofMenuIdx = (_ofMenuIdx + delta + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle("is-active", i === _ofMenuIdx));
    const cur = items[_ofMenuIdx];
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: "nearest" });
  }
  function ofCommitSymbol(raw) {
    const idx = String(raw == null ? "" : raw).toUpperCase().trim();
    const inp = $("#ofSym");
    ofCloseMenu();
    if (!idx || !_ofUni.has(idx)) {
      ofSymMsg("Pick a supported index from the list.");
      if (inp) inp.value = ofSymbol;
      return false;
    }
    ofSymMsg("");
    if (inp) inp.value = idx;
    if (idx === ofSymbol) return true;
    ofSymbol = idx;
    ofDate = "";                              // reset session to newest for the new symbol
    loadOrderflow({ symbolChanged: true });
    return true;
  }

  function ofNum(n, d = 2) {
    return (n === null || n === undefined || n === "" || isNaN(Number(n))) ? "—" : Number(n).toFixed(d);
  }
  function ofRenderProfile(elId, prof, kind, extra) {
    const el = $("#" + elId); if (!el) return;
    if (!prof || prof.status !== "OK" || !Array.isArray(prof.bins) || !prof.bins.length) {
      el.innerHTML = `<span class="hint">${esc((prof && prof.status) || "no data")}</span>`;
      return;
    }
    const valKey = kind === "volume" ? "volume" : "tpo";
    const max = Math.max(1, ...prof.bins.map(b => Number(b[valKey]) || 0));
    const poc = prof.poc, vah = prof.vah, val = prof.val;
    const vwap = extra && extra.vwap;
    const lo = Math.min(val, vah), hi = Math.max(val, vah);
    // price descending (high at top)
    const rows = prof.bins.slice().sort((a, b) => b.price - a.price).map(b => {
      const v = Number(b[valKey]) || 0;
      const pct = Math.round(v / max * 100);
      const cls = ["of-row"];
      if (b.price === poc) cls.push("is-poc");
      if (b.price >= lo && b.price <= hi) cls.push("in-va");
      if (vwap != null && Math.abs(b.price - vwap) <= (prof.tick_size || 0) / 2) cls.push("is-vwap");
      if (kind === "tpo" && b.tpo === 1) cls.push("is-single");
      if (kind === "volume") {
        return `<div class="${cls.join(" ")}"><span class="of-px">${ofNum(b.price, 2)}</span>` +
               `<span class="of-bar-wrap"><span class="of-bar" style="width:${pct}%"></span>` +
               `<span class="of-bar-val">${Math.round(v)}</span></span></div>`;
      }
      return `<div class="${cls.join(" ")}"><span class="of-px">${ofNum(b.price, 2)}</span>` +
             `<span class="of-letters" title="${esc(b.letters || "")}">${esc(b.letters || "·")}</span></div>`;
    }).join("");
    el.innerHTML = rows;
  }
  function ofRenderBacktest(bt) {
    const badge = $("#ofBtBadge"), warn = $("#ofBtWarn"), sumEl = $("#ofBtSummary");
    const tb = $("#ofBtTable tbody"), meta = $("#ofBtMeta");
    if (!bt || bt.status !== "OK") {
      if (badge) { badge.textContent = (bt && bt.status) || "—"; badge.className = "pill pill--paper"; }
      if (sumEl) sumEl.innerHTML = `<span class="hint">${esc((bt && (bt.note || bt.status)) || "no backtest")}</span>`;
      if (tb) tb.innerHTML = "";
      if (warn) warn.hidden = true;
      return;
    }
    const o = bt.overall;
    if (meta) meta.textContent = `${bt.symbol} · ${bt.sessions_scanned} sessions · spike ×${bt.volume_mult} · ${o.signals} signals`;
    if (badge) {
      badge.textContent = o.reliable ? "SAMPLE OK" : "INSUFFICIENT";
      badge.className = "pill " + (o.reliable ? "pill--paper" : "pill--warn");
    }
    if (warn) {
      if (o.reliability_reason) { warn.textContent = o.reliability_reason; warn.hidden = false; }
      else warn.hidden = true;
    }
    const kv = (label, val, cls) => `<div class="of-kv ${cls || ""}"><b>${esc(val)}</b><span>${esc(label)}</span></div>`;
    const netCls = o.net_points > 0 ? "pos" : o.net_points < 0 ? "neg" : "";
    const isPrem = bt.basis === "premium";
    const bcov = bt.basis_coverage || {};
    if (sumEl) sumEl.innerHTML = [
      isPrem ? kv("basis (premium coverage)",
        `option premium · ${bcov.premium_repriced || 0}/${bcov.resolved_priced || 0}` +
        (bcov.premium_thin_quotes ? ` · ${bcov.premium_thin_quotes} thin` : "") +
        (bcov.index_fallback ? ` · ${bcov.index_fallback} idx-fallback` : "") +
        (bt.premium_stop_pct ? ` · stop −${Math.round(bt.premium_stop_pct * 100)}% (${bcov.premium_stop_hits || 0} hit)` : ""),
        "warn") : kv("basis", "index points"),
      kv("wins / losses / open", `${o.wins} / ${o.losses} / ${o.open}`),
      kv(`win rate (breakeven ${(o.breakeven_win_rate * 100).toFixed(0)}%)`,
        o.win_rate == null ? "—" : (o.win_rate * 100).toFixed(1) + "%",
        o.win_rate != null && o.win_rate >= o.breakeven_win_rate ? "pos" : "neg"),
      kv("winning points", "+" + ofNum(o.gross_win_points, 1), "pos"),
      kv("SL-hit points", "−" + ofNum(o.gross_loss_points, 1), "neg"),
      kv("net points", ofNum(o.net_points, 1), netCls),
      kv("expectancy / trade", o.expectancy_points == null ? "—" : ofNum(o.expectancy_points, 2), o.expectancy_points > 0 ? "pos" : "neg"),
      kv("profit factor", o.profit_factor == null ? "—" : ofNum(o.profit_factor, 2), o.profit_factor != null && o.profit_factor >= 1 ? "pos" : "neg"),
      kv("max drawdown", ofNum(o.max_drawdown_points, 1), "neg"),
      kv("BUY  W/L", `${bt.by_side.BUY.wins} / ${bt.by_side.BUY.losses}`),
      kv("SELL W/L", `${bt.by_side.SELL.wins} / ${bt.by_side.SELL.losses}`),
    ].join("");
    if (tb) {
      const rows = (bt.trades || []).map(t => {
        const time = (() => { const d = new Date(t.candle_ts); return isNaN(d) ? esc(t.candle_ts) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); })();
        const pcls = t.points > 0 ? "pos" : t.points < 0 ? "neg" : "";
        const psign = t.points > 0 ? "+" : "";
        return `<tr><td>${esc(t.session)}</td><td>${time}</td><td class="${esc(t.side)}">${esc(t.side)}</td>` +
          `<td>${ofNum(t.entry)}</td><td>${ofNum(t.stop_loss)}</td><td>${ofNum(t.target)}</td>` +
          `<td><span class="of-oc ${t.result === "WIN" ? "TARGET_HIT" : t.result === "LOSS" ? "STOP_HIT" : "TRIGGERED"}">${esc(t.result)}</span></td>` +
          `<td>${t.exit_price == null ? "—" : ofNum(t.exit_price)}</td>` +
          `<td class="${pcls}">${t.result === "OPEN" ? "—" : psign + ofNum(t.points, 1)}</td></tr>`;
      });
      tb.innerHTML = rows.join("") || `<tr><td colspan="9" class="hint">no resolved trades</td></tr>`;
    }
  }

  function ofRenderSignals(sm) {
    const tb = $("#ofSmTable tbody"); if (!tb) return;
    if (!sm || sm.status !== "OK" || !Array.isArray(sm.setups) || !sm.setups.length) {
      tb.innerHTML = `<tr><td colspan="10" class="hint">${esc((sm && (sm.reason || sm.status)) || "no spike setups")}</td></tr>`;
      return;
    }
    const rows = [];
    sm.setups.forEach(s => {
      ["buy", "sell"].forEach(side => {
        const L = s[side] || {};
        const oc = (L.outcome || {}).status || "—";
        const t = new Date(s.candle.bar_start);
        const tstr = isNaN(t) ? esc(s.candle.bar_start) : t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const bkt = L.breakout_bar ? new Date(L.breakout_bar) : null;
        const bstr = bkt && !isNaN(bkt) ? bkt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—";
        rows.push(
          `<tr><td>${side === "buy" ? tstr : ""}</td><td>${side === "buy" ? esc(s.volume_x_avg) + "×" : ""}</td>` +
          `<td>${side === "buy" ? ofNum(s.range_points, 2) : ""}</td>` +
          `<td class="${esc(L.side)}">${esc(L.side)}</td>` +
          `<td>${ofNum(L.entry)}</td><td>${ofNum(L.stop_loss)}</td><td>${ofNum(L.target)}</td>` +
          `<td>1:${esc(L.rr)}</td><td>${bstr}</td>` +
          `<td><span class="of-oc ${esc(oc)}">${esc(oc)}</span></td></tr>`
        );
      });
    });
    tb.innerHTML = rows.join("");
  }

  async function loadOrderflow(opts) {
    opts = opts || {};
    const myReq = ++_ofReq;
    const stale = () => myReq !== _ofReq;
    const inp = $("#ofSym"); if (inp && document.activeElement !== inp) inp.value = ofSymbol;
    const errEl = $("#ofErr");
    const setErr = (m) => { if (errEl) { if (m) { errEl.textContent = m; errEl.hidden = false; } else errEl.hidden = true; } };
    setErr("");

    // 1. session dates for this symbol -> populate the picker
    try {
      const sess = await api(`/api/orderflow/sessions?symbol=${encodeURIComponent(ofSymbol)}`);
      if (stale()) return;
      const sel = $("#ofDate");
      const dates = (sess && sess.sessions) || [];
      if (sel) {
        const keep = ofDate && dates.includes(ofDate) ? ofDate : (dates[0] || "");
        ofDate = keep;
        sel.innerHTML = dates.length
          ? dates.map(d => `<option value="${esc(d)}"${d === keep ? " selected" : ""}>${esc(d)}</option>`).join("")
          : `<option value="">no captured sessions</option>`;
      }
    } catch (e) { if (!stale()) showError("orderflow/sessions", e); }

    if (!ofDate) {
      ofRenderProfile("ofVp", null, "volume"); ofRenderProfile("ofMp", null, "tpo");
      ofRenderSignals(null);
      const m = $("#ofMeta"); if (m) m.textContent = "no captured session data for " + ofSymbol;
      return;
    }

    const mult = Number(($("#ofMult") || {}).value || 2);
    const stopFrac = Number(($("#ofStop") || {}).value || 1);
    const rr = Number(($("#ofRr") || {}).value || 3);
    const sigFilter = (($("#ofFilterSig") || {}).value || "none");
    const pattern = (($("#ofPattern") || {}).value || "spike");
    const trail = !!(($("#ofTrail") || {}).checked);
    const basis = (($("#ofBasis") || {}).value || "index");
    const premStop = Number(($("#ofPremStop") || {}).value || 0);
    const smQ = `symbol=${encodeURIComponent(ofSymbol)}&volume_mult=${mult}&rr=${rr}&stop_frac=${stopFrac}`
              + `&sig_filter=${encodeURIComponent(sigFilter)}&trail=${trail}&pattern=${encodeURIComponent(pattern)}`;
    const btQ = `${smQ}&basis=${encodeURIComponent(basis)}&premium_stop_pct=${premStop}`;

    // 2. profile + smart-money, in parallel
    try {
      const [prof, sm, bt, h1h7] = await Promise.all([
        api(`/api/orderflow/profile?symbol=${encodeURIComponent(ofSymbol)}&date=${encodeURIComponent(ofDate)}`),
        api(`/api/orderflow/smart-money?${smQ}&date=${encodeURIComponent(ofDate)}`),
        api(`/api/orderflow/backtest?${btQ}`),
        api(`/api/orderflow/h1h7-state?symbol=${encodeURIComponent(ofSymbol)}&date=${encodeURIComponent(ofDate)}`),
      ]);
      if (stale()) return;

      const meta = $("#ofMeta");
      if (meta) meta.textContent = `${ofSymbol} · ${ofDate} · ${prof.bar_count || 0} bars · VWAP ${ofNum(prof.vwap, 2)}`;

      const vp = prof.volume_profile, mp = prof.market_profile;
      ofRenderProfile("ofVp", vp, "volume", { vwap: prof.vwap });
      ofRenderProfile("ofMp", mp, "tpo", { vwap: prof.vwap });
      const vpm = $("#ofVpMeta");
      if (vpm) vpm.textContent = vp && vp.status === "OK"
        ? `POC ${ofNum(vp.poc, 2)} · VA ${ofNum(vp.val, 2)}–${ofNum(vp.vah, 2)} · ${vp.method}` : "—";
      const mpm = $("#ofMpMeta");
      if (mpm) mpm.textContent = mp && mp.status === "OK"
        ? `POC ${ofNum(mp.poc, 2)} · VA ${ofNum(mp.val, 2)}–${ofNum(mp.vah, 2)} · ${mp.n_brackets} brackets · ${(mp.single_prints || []).length} single prints` : "—";

      ofRenderSignals(sm);
      const smm = $("#ofSmMeta");
      if (smm) smm.textContent = sm && sm.status === "OK"
        ? `${sm.spike_count} spike candle(s) · avg vol ${ofNum(sm.session_avg_volume, 0)} · ×${mult} threshold` : (sm && sm.reason) || "—";

      ofRenderBacktest(bt);
      ofRenderH1H7(h1h7);
    } catch (e) { if (!stale()) { setErr((e && e.message) || String(e)); showError("orderflow", e); } }
  }

  function ofRenderH1H7(st) {
    const tb = $("#ofH1H7Table tbody");
    const sum = $("#ofH1H7Summary");
    const meta = $("#ofH1H7Meta");
    const leg = $("#ofH1H7Legend");
    const events = (st && st.events) || [];
    const ACT_CLASS = { AVOID: "of-oc bad", NO_ACTION: "of-oc", CONTINUATION_CANDIDATE: "of-oc warn" };
    if (meta) {
      meta.textContent = st && st.mode
        ? `${st.mode} · live_trading ${String(st.live_trading)} · ${st.bar_count || 0} bars · ${events.length} classified event(s)`
        : "no captured session data";
    }
    if (sum) {
      const s = (st && st.summary) || {};
      const parts = Object.keys(s).sort().map(k => `${esc(k)} <strong>${s[k]}</strong>`);
      sum.innerHTML = parts.length
        ? `<span class="hint">state counts:</span> ${parts.join(" · ")}`
        : `<span class="hint">no abnormal-spike events on this session (deterministic; nothing to classify)</span>`;
    }
    if (tb) {
      tb.innerHTML = events.length ? events.map(e => {
        const rd = e.reclaim_distance_ratio == null ? "—"
          : `${ofNum(e.reclaim_distance, 2)} (${ofNum(e.reclaim_distance_ratio, 2)}×)`;
        const aR = e.available_R == null
          ? `<span class="hint" title="no opposing structural level in the bar-derived set — never fabricated">UNOBSERVABLE</span>`
          : ofNum(e.available_R, 2);
        return `<tr>
          <td>${esc((e.timestamp || "").slice(11, 16))}</td>
          <td>${esc(e.spike_direction || "—")} · ${ofNum(e.spike_range, 2)} · p${ofNum(e.range_pctile, 2)}</td>
          <td>${e.broken_level == null ? "—" : ofNum(e.broken_level, 2) + " <span class='hint'>" + esc(e.broken_level_kind || "") + "</span>"}</td>
          <td>${rd}</td>
          <td>${aR}</td>
          <td>${e.n1_agreement == null ? "—" : (e.n1_agreement ? "agree" : "no")}</td>
          <td>${ofNum(e.disp_atr, 2)}</td>
          <td>${ofNum(e.body_fraction, 2)} <span class="hint">info</span></td>
          <td><strong>${esc(e.state || "—")}</strong></td>
          <td class="${ACT_CLASS[e.action] || "of-oc"}">${esc(e.action || "—")}</td>
          <td>${esc(e.research_status || "—")}</td>
        </tr>`;
      }).join("") : `<tr><td colspan="11" class="hint">—</td></tr>`;
    }
    if (leg) {
      const L = (st && st.research_status_legend) || {};
      const order = ["SUPPORTED", "RESEARCH_ONLY_PROMISING", "NOT_VALIDATED", "NO_ESTABLISHED_EDGE", "UNOBSERVABLE", "PROVEN"];
      leg.innerHTML = order.filter(k => L[k]).map(k =>
        `<p><strong>${esc(k)}</strong> — ${esc(L[k])}</p>`).join("")
        || `<p class="hint">research confidence legend unavailable</p>`;
    }
  }

  (function wireOrderflow() {
    const inp = $("#ofSym");
    if (inp) {
      inp.value = ofSymbol;
      const openAll = () => { try { inp.select(); } catch (e) {} ofRenderMenu(""); };
      inp.addEventListener("focus", openAll);
      inp.addEventListener("click", () => { const m = $("#ofSymMenu"); if (m && m.hidden) ofRenderMenu(inp.value); });
      inp.addEventListener("input", () => { ofSymMsg(""); ofRenderMenu(inp.value); });
      inp.addEventListener("keydown", (e) => {
        const m = $("#ofSymMenu");
        if (e.key === "ArrowDown") { e.preventDefault(); (!m || m.hidden) ? ofRenderMenu(inp.value) : ofHighlight(1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); (!m || m.hidden) ? ofRenderMenu(inp.value) : ofHighlight(-1); }
        else if (e.key === "Enter") {
          e.preventDefault();
          const items = ofMenuItems();
          const pick = (_ofMenuIdx >= 0 && items[_ofMenuIdx]) ? items[_ofMenuIdx].dataset.idx : inp.value;
          ofCommitSymbol(pick);
        } else if (e.key === "Escape") { ofCloseMenu(); inp.value = ofSymbol; ofSymMsg(""); }
      });
      inp.addEventListener("blur", () => setTimeout(() => {
        const m = $("#ofSymMenu"); if (m && !m.hidden) return;
        const v = (inp.value || "").toUpperCase().trim();
        if (v && v !== ofSymbol && _ofUni.has(v)) { ofCommitSymbol(v); return; }
        inp.value = ofSymbol; ofSymMsg("");
      }, 150));
    }
    const menu = $("#ofSymMenu");
    if (menu) {
      menu.addEventListener("mousedown", (e) => e.preventDefault());
      menu.addEventListener("click", (e) => {
        const li = e.target && e.target.closest ? e.target.closest(".ms-combo-item") : null;
        if (li && li.dataset && li.dataset.idx) ofCommitSymbol(li.dataset.idx);
      });
    }
    if (typeof document !== "undefined" && document.addEventListener) {
      document.addEventListener("click", (e) => {
        const c = $("#ofSymCombo");
        if (c && c.contains && e.target && !c.contains(e.target)) ofCloseMenu();
      });
    }
    const dsel = $("#ofDate");
    if (dsel) dsel.addEventListener("change", () => { ofDate = dsel.value; loadOrderflow(); });
    ["#ofMult", "#ofStop", "#ofRr", "#ofFilterSig", "#ofPattern", "#ofTrail", "#ofBasis", "#ofPremStop"].forEach(id => {
      const s = $(id); if (s) s.addEventListener("change", () => loadOrderflow());
    });
    const rb = $("#ofRefresh");
    if (rb) rb.addEventListener("click", () => loadOrderflow({ force: true }));
  })();

  // ================= Option Chain — structure & qualification =================
  // Read-only. GET /api/optionchain/{sym} -> {chain, analytics, structure,
  // quality, qualification}. One row per strike (CE cols | strike | PE cols),
  // ATM highlighted; header strip: spot / PCR / max-pain / ATM-IV / skew / GEX /
  // DQ / source; a split OI-profile bar; a ΔOI-vs-baseline toggle.
  let ocSymbol = "NIFTY";
  let _ocInit = false;
  let _ocReq = 0;

  const ocFmtOi = (n) => {
    if (n === null || n === undefined || isNaN(Number(n))) return "—";
    const v = Math.abs(Number(n));
    const s = Number(n) < 0 ? "-" : "";
    if (v >= 1e7) return s + (v / 1e7).toFixed(2) + "Cr";
    if (v >= 1e5) return s + (v / 1e5).toFixed(1) + "L";
    if (v >= 1e3) return s + (v / 1e3).toFixed(0) + "k";
    return s + v.toFixed(0);
  };
  const ocPct = (n, d = 2) => (n === null || n === undefined || isNaN(Number(n)))
    ? "—" : (Number(n) * (Math.abs(Number(n)) <= 2 ? 100 : 1)).toFixed(d) + "%";
  const ocVerdictClass = (v) => v === "QUALIFIED" ? "ok" : v === "NO_TRADE" ? "bad" : "warn";

  async function ocEnsureSymbols() {
    if (_ocInit) return;
    _ocInit = true;
    const sel = $("#ocSymbol");
    let groups = { NSE: ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
                   BSE: ["SENSEX", "BANKEX"], MCX: ["CRUDEOIL", "NATURALGAS"] };
    try {
      const r = await api("/api/optionchain/underlyings");
      if (r && r.groups && typeof r.groups === "object" && Object.keys(r.groups).length) {
        groups = r.groups;
      } else if (r && Array.isArray(r.underlyings) && r.underlyings.length) {
        groups = { "": r.underlyings };
      }
    } catch (e) { /* fall back to the static groups */ }
    if (sel && !(sel.options && sel.options.length)) {
      sel.innerHTML = Object.entries(groups).map(([g, arr]) => {
        const opts = arr.map(u => `<option value="${esc(u)}">${esc(u)}</option>`).join("");
        return g ? `<optgroup label="${esc(g)}">${opts}</optgroup>` : opts;
      }).join("");
      try { sel.value = ocSymbol; } catch (e) { /* stub select in tests */ }
    }
  }

  function ocCommitSymbol(v) {
    const u = String(v || "").toUpperCase().trim();
    if (!u) return false;
    ocSymbol = u;
    const sel = $("#ocSymbol"); if (sel) sel.value = u;
    loadOptionchain();
    return true;
  }

  async function loadOptionchain() {
    await ocEnsureSymbols();
    const sel = $("#ocSymbol"); if (sel && sel.value) ocSymbol = sel.value;
    const exp = ($("#ocExpiry") || {}).value || "AUTO";
    const live = ($("#ocLive") || {}).checked ? 1 : 0;
    const baseOn = ($("#ocBaseChk") || {}).checked;
    const baseTs = (($("#ocBaseTs") || {}).value || "").trim();
    const bq = (baseOn && baseTs) ? `&baseline=${encodeURIComponent(baseTs)}` : "";
    const tsInput = $("#ocBaseTs"); if (tsInput) tsInput.hidden = !baseOn;

    const req = ++_ocReq;
    const stale = () => req !== _ocReq;
    const errEl = $("#ocErr");
    try {
      const j = await api(`/api/optionchain/${encodeURIComponent(ocSymbol)}?expiry=${encodeURIComponent(exp)}&live=${live}&atm_window=12${bq}`);
      if (stale()) return;
      if (errEl) errEl.hidden = true;
      if (!j || j.status === "NO_DATA") {
        $("#ocMeta").textContent = `${ocSymbol}: ${(j && j.note) || "no data"}`;
        const tb = $("#ocTable tbody");
        if (tb) tb.innerHTML = `<tr><td colspan="14" class="hint">no chain available</td></tr>`;
        $("#ocStrip").innerHTML = ""; $("#ocQual").innerHTML = ""; $("#ocNote").textContent = "";
        return;
      }
      ocRenderMeta(j);
      ocRenderExpiryBanner(j);
      ocRenderQual(j.qualification || {});
      ocRenderStrip(j);
      ocRenderTable(j);
      ocRenderCharts(j);
      const notes = [].concat((j.structure && j.structure.notes) || [],
        (j.chain && j.chain.notes) || []).filter(Boolean);
      $("#ocNote").textContent = notes.slice(0, 6).join("  ·  ");
    } catch (e) {
      if (stale()) return;
      if (errEl) { errEl.textContent = (e && e.message) || String(e); errEl.hidden = false; }
      showError("optionchain", e);
    }
  }

  function ocRenderExpiryBanner(j) {
    const box = $("#ocExpiryBanner");
    if (!box) return;
    const ec = j.expiry_ctx || (j.structure && j.structure.expiry_context) || {};
    const phase = ec.phase;
    if (!phase || phase === "NORMAL") { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;
    let cls = "warn", head = "";
    if (phase === "EXPIRY_DAY") {
      cls = "bad";
      head = `⚠ EXPIRY DAY — ${esc(j.expiry)} (0 DTE). Expiring-series OI is unwinding: ` +
        `the Δ column and PCR / OI-walls are structural noise today, not sentiment. ` +
        `Watch Max Pain / GEX pin.`;
    } else if (phase === "EXPIRED") {
      cls = "bad";
      head = `⚠ ${esc(j.expiry)} HAS EXPIRED — switch to a live expiry.`;
    } else if (phase === "EXPIRY_WEEK") {
      cls = "warn";
      head = `${esc(ec.dte)} DTE to ${esc(j.expiry)} — rollover under way, gamma rising.`;
    }
    const nxt = ec.next_expiry;
    const btn = (phase === "EXPIRY_DAY" || phase === "EXPIRED")
      ? `<button class="btn btn-ghost oc-nextexp" data-next="${esc(nxt || "NEXT")}">View next expiry →</button>`
      : "";
    box.className = `oc-expiry ${cls}`;
    box.innerHTML = `<span>${head}</span>${btn}`;
    const b = box.querySelector(".oc-nextexp");
    if (b) b.addEventListener("click", () => {
      const sel = $("#ocExpiry");
      if (sel) sel.value = "NEXT";
      const live = $("#ocLive"); if (live) live.checked = true;   // next expiry usually needs the network source
      loadOptionchain();
    });
  }

  function ocRenderMeta(j) {
    const cap = j.capability || {};
    const bits = [
      `source ${esc(j.source || "—")}`,
      `expiry ${esc(j.expiry || "—")}`,
      `spot ${text(fmt(j.spot, 1))}`,
      `ATM ${text(j.atm_strike)}`,
      cap.cadence_sec ? `cadence ~${esc(cap.cadence_sec)}s` : null,
      j.ts ? `as of ${timeStr(j.ts)}` : null,
      cap.oi_stale ? "OI STALE" : null,
      cap.oi_change_source
        ? `Δ vs ${esc(cap.oi_change_baseline_date || "prev")} close (${Math.round((cap.oi_change_coverage || 0) * 100)}% of strikes)`
        : "Δ n/a",
    ].filter(Boolean);
    $("#ocMeta").textContent = bits.join("  ·  ");
  }

  function ocRenderQual(q) {
    const box = $("#ocQual");
    if (!box) return;
    if (!q || !q.verdict) { box.innerHTML = ""; return; }
    const reasons = [].concat(q.blocking && q.blocking.length
      ? q.blocking.map(b => "✕ " + b) : [], (q.watch_reasons || []).map(r => "△ " + r));
    box.innerHTML =
      `<span class="oc-verdict ${ocVerdictClass(q.verdict)}">${esc(q.verdict)}` +
      `${q.direction && q.direction !== "NEUTRAL" ? " " + esc(q.direction) : ""}</span>` +
      `<span class="oc-qbits">structure ${esc((q.structure_bias || {}).bias || "—")}` +
      ` (net ${text((q.structure_bias || {}).net)}) · regime ${esc(q.regime_context || "—")}` +
      `${q.ann_p_win != null ? " · ANN p " + esc(q.ann_p_win) : ""}</span>` +
      (reasons.length ? `<span class="oc-qreasons">${reasons.map(esc).join(" &nbsp; ")}</span>` : "") +
      `<span class="oc-qnote">research-only — not wired to any order path</span>`;
  }

  function ocRenderStrip(j) {
    const a = j.analytics || {}, st = j.structure || {}, qy = j.quality || {};
    const mp = a.max_pain || {}, pcr = a.pcr || {}, sk = a.iv_skew || {}, gx = a.gex || {};
    const card = (label, val, cls) =>
      `<div class="stat-card"><div class="stat-label">${esc(label)}</div>` +
      `<div class="stat-value ${cls || ""}">${val}</div></div>`;
    const skTxt = (st.iv_skew_bias || {}).bias || sk.status || "—";
    const gxTxt = (st.gex_regime || {}).regime || gx.status || "—";
    $("#ocStrip").innerHTML = [
      card("Spot", text(fmt(j.spot, 1))),
      card("PCR (OI)", text(pcr.pcr_oi), (pcr.pcr_oi > 1.1 ? "pos" : pcr.pcr_oi < 0.9 && pcr.pcr_oi != null ? "neg" : "")),
      card("Max Pain", text(mp.max_pain_strike) + (mp.distance_pct != null ? ` <small>${mp.distance_pct > 0 ? "+" : ""}${esc(mp.distance_pct)}%</small>` : "")),
      card("ATM IV", sk.atm_iv != null ? esc((sk.atm_iv * 100).toFixed(2)) + "%" : "—"),
      card("IV skew", esc(skTxt)),
      card("GEX", esc(gxTxt) + ((st.gex_regime || {}).flip_strike ? ` <small>flip ${esc(st.gex_regime.flip_strike)}</small>` : "")),
      card("DQ", text(qy.dqs) + (qy.verdict ? ` <small>${esc(qy.verdict)}</small>` : ""),
        qy.verdict === "PASS" ? "pos" : qy.verdict === "FAIL" ? "neg" : ""),
    ].join("");
  }

  function ocRenderTable(j) {
    const tb = $("#ocTable tbody");
    if (!tb) return;
    const rows = ((j.chain || {}).rows) || [];
    if (!rows.length) { tb.innerHTML = `<tr><td colspan="14" class="hint">empty chain</td></tr>`; return; }
    const atm = j.atm_strike;
    // ΔOI baseline map (strike -> {ce_doi, pe_doi}) when the toggle is on
    const bmap = {};
    const bl = j.oi_baseline;
    if (bl && bl.status === "ok") for (const r of (bl.rows || [])) bmap[r.strike] = r;
    const usingBase = !!Object.keys(bmap).length;
    let maxOi = 1;
    for (const r of rows) maxOi = Math.max(maxOi, Number((r.ce || {}).oi) || 0, Number((r.pe || {}).oi) || 0);
    const bar = (ceOi, peOi) => {
      const cw = Math.round(100 * (Number(ceOi) || 0) / maxOi);
      const pw = Math.round(100 * (Number(peOi) || 0) / maxOi);
      return `<div class="oc-oibar"><span class="oc-oibar-ce" style="width:${cw / 2}%"></span>` +
        `<span class="oc-oibar-pe" style="width:${pw / 2}%"></span></div>`;
    };
    const dcell = (leg, side, strike) => {
      if (usingBase) {
        const b = bmap[strike] || {};
        const v = side === "ce" ? b.ce_doi : b.pe_doi;
        return v == null ? "—" : `<span class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${ocFmtOi(v)}</span>`;
      }
      const oc = (leg || {}).oi_change;
      return oc == null ? "—" : `<span class="${oc > 0 ? "pos" : oc < 0 ? "neg" : ""}">${ocFmtOi(oc)}</span>`;
    };
    tb.innerHTML = rows.map(r => {
      const ce = r.ce || {}, pe = r.pe || {};
      const isAtm = atm != null && Math.abs(r.strike - atm) < 1e-6;
      const itm = (j.spot != null)
        ? (r.strike < j.spot ? "ce-itm" : r.strike > j.spot ? "pe-itm" : "")
        : "";
      return `<tr class="${isAtm ? "is-atm" : ""} ${itm}">` +
        `<td>${ocFmtOi(ce.oi)}</td><td>${dcell(ce, "ce", r.strike)}</td><td>${ocFmtOi(ce.volume)}</td>` +
        `<td>${ce.iv != null ? esc((ce.iv * 100).toFixed(1)) : "—"}</td>` +
        `<td>${text(fmt(ce.delta, 2))}</td><td class="oc-ltp">${text(fmt(ce.ltp, 1))}</td>` +
        `<td class="oc-k">${text(r.strike)}</td>` +
        `<td class="oc-ltp">${text(fmt(pe.ltp, 1))}</td><td>${text(fmt(pe.delta, 2))}</td>` +
        `<td>${pe.iv != null ? esc((pe.iv * 100).toFixed(1)) : "—"}</td>` +
        `<td>${ocFmtOi(pe.volume)}</td><td>${dcell(pe, "pe", r.strike)}</td><td>${ocFmtOi(pe.oi)}</td>` +
        `<td class="oc-prof">${bar(ce.oi, pe.oi)}</td></tr>`;
    }).join("");
  }

  // ---------------- Option Analytics charts ----------------
  // Dependency-free inline SVG (no charting library anywhere else in this
  // codebase — matches the existing OI-bar's plain-CSS-div convention).
  // Every chart here either (a) re-renders data already present in the
  // GET /api/optionchain/{u} response the table above already fetched
  // (IV smile, GEX profile, Max Pain curve — analytics.*.per_strike/curve),
  // or (b) calls one of the three new read-only endpoints added alongside
  // this view (vol-surface / straddle-pnl / oi-profile) or the pre-existing
  // /api/greeks-engine/exposure endpoint (Greeks history / PCR history —
  // that endpoint already existed with zero frontend consumer before this).
  const OC_W = 480, OC_H = 160, OC_PAD = { l: 34, r: 8, t: 8, b: 18 };

  function ocEmpty(id, msg) {
    const el = $(id);
    if (el) el.innerHTML = `<div class="oc-chart-empty">${esc(msg || "no data")}</div>`;
  }

  function ocScale(lo, hi, outLo, outHi) {
    const span = hi - lo || 1;
    return (v) => outLo + ((v - lo) / span) * (outHi - outLo);
  }

  function ocPath(pts) {
    return pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  }

  // Generic multi-series line/scatter chart. series = [{label, color, points:[[x,y],...]}]
  function ocLineChart(series, { width = OC_W, height = OC_H, xLabel, yFmt } = {}) {
    const all = series.flatMap(s => s.points);
    if (!all.length) return null;
    const xs = all.map(p => p[0]), ys = all.map(p => p[1]);
    const xLo = Math.min(...xs), xHi = Math.max(...xs);
    const yLo = Math.min(0, Math.min(...ys)), yHi = Math.max(...ys) || 1;
    const sx = ocScale(xLo, xHi, OC_PAD.l, width - OC_PAD.r);
    const sy = ocScale(yLo, yHi, height - OC_PAD.b, OC_PAD.t);
    const zeroY = sy(0);
    const gridY = [yLo, (yLo + yHi) / 2, yHi];
    let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet">`;
    for (const gy of gridY) {
      svg += `<line x1="${OC_PAD.l}" y1="${sy(gy).toFixed(1)}" x2="${width - OC_PAD.r}" y2="${sy(gy).toFixed(1)}" stroke="var(--line-soft)" stroke-width="1"/>`;
      svg += `<text class="oc-axis-label" x="2" y="${(sy(gy) + 3).toFixed(1)}">${esc(yFmt ? yFmt(gy) : gy.toFixed(1))}</text>`;
    }
    if (yLo < 0 && yHi > 0) svg += `<line class="oc-atm-marker" x1="${OC_PAD.l}" y1="${zeroY.toFixed(1)}" x2="${width - OC_PAD.r}" y2="${zeroY.toFixed(1)}"/>`;
    for (const s of series) {
      const pts = s.points.map(p => [sx(p[0]), sy(p[1])]);
      svg += `<path d="${ocPath(pts)}" fill="none" stroke="${s.color || "var(--teal)"}" stroke-width="1.6" class="${s.cls || ""}"/>`;
    }
    if (xLabel) svg += `<text class="oc-axis-label" x="${OC_PAD.l}" y="${height - 4}">${esc(xLabel(xLo))}</text>` +
      `<text class="oc-axis-label" x="${width - OC_PAD.r - 40}" y="${height - 4}">${esc(xLabel(xHi))}</text>`;
    svg += "</svg>";
    return svg;
  }

  // Diverging (or single-side) bar chart. bars = [{x, y, cls}]
  function ocBarChart(bars, { width = OC_W, height = OC_H, xTick } = {}) {
    if (!bars.length) return null;
    const xs = bars.map(b => b.x), ys = bars.map(b => b.y);
    const xLo = Math.min(...xs), xHi = Math.max(...xs);
    const yAbs = Math.max(1, ...ys.map(Math.abs));
    const sx = ocScale(xLo, xHi, OC_PAD.l, width - OC_PAD.r);
    const sy = ocScale(-yAbs, yAbs, height - OC_PAD.b, OC_PAD.t);
    const zeroY = sy(0);
    const bw = Math.max(2, (width - OC_PAD.l - OC_PAD.r) / bars.length - 1);
    let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet">`;
    svg += `<line x1="${OC_PAD.l}" y1="${zeroY.toFixed(1)}" x2="${width - OC_PAD.r}" y2="${zeroY.toFixed(1)}" stroke="var(--line-soft)"/>`;
    for (const b of bars) {
      const x = sx(b.x) - bw / 2, y = sy(Math.max(0, b.y)), h = Math.abs(sy(b.y) - zeroY);
      svg += `<rect class="${b.y >= 0 ? "oc-bar-pos" : "oc-bar-neg"}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, h).toFixed(1)}"/>`;
    }
    if (xTick) svg += `<text class="oc-axis-label" x="${OC_PAD.l}" y="${height - 4}">${esc(xTick(xLo))}</text>` +
      `<text class="oc-axis-label" x="${width - OC_PAD.r - 40}" y="${height - 4}">${esc(xTick(xHi))}</text>`;
    svg += "</svg>";
    return svg;
  }

  // Strike x expiry heatmap (Volatility Surface, 2D projection — a true 3D
  // plot needs a charting library this codebase deliberately doesn't carry).
  function ocHeatmap(expiries, strikes, valueAt, { width = OC_W, height = OC_H } = {}) {
    if (!expiries.length || !strikes.length) return null;
    const vals = [];
    for (const e of expiries) for (const s of strikes) { const v = valueAt(e, s); if (v != null) vals.push(v); }
    if (!vals.length) return null;
    const vLo = Math.min(...vals), vHi = Math.max(...vals) || vLo + 1;
    const cw = (width - OC_PAD.l - OC_PAD.r) / expiries.length;
    const ch = (height - OC_PAD.t - OC_PAD.b) / strikes.length;
    let svg = `<svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet">`;
    strikes.forEach((s, si) => expiries.forEach((e, ei) => {
      const v = valueAt(e, s);
      const t = v == null ? 0 : (v - vLo) / (vHi - vLo || 1);
      const color = v == null ? "var(--bg-panel)" : `hsl(${(1 - t) * 200}, 55%, ${28 + t * 30}%)`;
      svg += `<rect x="${(OC_PAD.l + ei * cw).toFixed(1)}" y="${(OC_PAD.t + si * ch).toFixed(1)}" ` +
        `width="${cw.toFixed(1)}" height="${ch.toFixed(1)}" fill="${color}"><title>${esc(e)} @ ${esc(s)}: ${v == null ? "—" : (v * 100).toFixed(1) + "%"}</title></rect>`;
    }));
    svg += "</svg>";
    return svg;
  }

  function ocRenderSmileChart(a) {
    const per = ((a.iv_skew || {}).per_strike) || [];
    const ce = per.filter(p => p.ce_iv != null).map(p => [p.strike, p.ce_iv * 100]);
    const pe = per.filter(p => p.pe_iv != null).map(p => [p.strike, p.pe_iv * 100]);
    if (!ce.length && !pe.length) return ocEmpty("#ocChartSmile", "no IV in this chain");
    const svg = ocLineChart(
      [{ label: "CE", color: "var(--teal)", points: ce }, { label: "PE", color: "var(--ember)", points: pe }],
      { xLabel: (x) => x.toFixed(0), yFmt: (y) => y.toFixed(0) + "%" });
    $("#ocChartSmile").innerHTML = svg || "";
  }

  function ocRenderGexChart(a) {
    const per = ((a.gex || {}).per_strike) || [];
    if (!per.length) return ocEmpty("#ocChartGex", (a.gex || {}).status === "no_oi" ? "no OI" : "no data");
    const bars = per.map(p => ({ x: p.strike, y: p.shape }));
    const svg = ocBarChart(bars, { xTick: (x) => x.toFixed(0) });
    $("#ocChartGex").innerHTML = svg || "";
  }

  function ocRenderMaxPainChart(a) {
    const curve = ((a.max_pain || {}).curve) || [];
    if (!curve.length) return ocEmpty("#ocChartMaxPain", (a.max_pain || {}).status === "no_oi" ? "no OI" : "no data");
    const pts = curve.map(([k, p]) => [k, p]);
    const svg = ocLineChart([{ color: "var(--gold-soft)", points: pts }],
      { xLabel: (x) => x.toFixed(0), yFmt: (y) => (y / 1e5).toFixed(1) + "L" });
    $("#ocChartMaxPain").innerHTML = svg || "";
  }

  async function ocRenderGreeksHistory(underlying) {
    try {
      const rows = await api(`/api/greeks-engine/exposure?underlying=${encodeURIComponent(underlying)}&limit=500`);
      if (!Array.isArray(rows) || !rows.length) return ocEmpty("#ocChartGreeksHist", "no captured history yet");
      const xs = rows.map((_, i) => i);
      const mk = (key, color) => ({ color, points: rows.map((r, i) => [i, Number(r[key]) || 0]) });
      const svg = ocLineChart([mk("net_delta_exp", "var(--teal)"), mk("net_gamma_exp", "var(--violet)"),
        mk("net_theta_exp", "var(--ember)"), mk("net_vega_exp", "var(--gold-soft)")],
        { xLabel: () => "", yFmt: (y) => (Math.abs(y) >= 1e5 ? (y / 1e5).toFixed(1) + "L" : y.toFixed(0)) });
      $("#ocChartGreeksHist").innerHTML = (svg || "") +
        `<p class="hint" style="margin:4px 0 0">teal Δ · violet Γ · ember Θ · gold V · ${rows.length} snapshots, ${rows[0].as_of_ts ? timeStr(rows[0].as_of_ts) : "—"} → ${rows[rows.length - 1].as_of_ts ? timeStr(rows[rows.length - 1].as_of_ts) : "—"}</p>`;
    } catch (e) { ocEmpty("#ocChartGreeksHist", "unavailable"); }
  }

  async function ocRenderPcrHistory(underlying) {
    try {
      const rows = await api(`/api/greeks-engine/exposure?underlying=${encodeURIComponent(underlying)}&limit=500`);
      const pts = (Array.isArray(rows) ? rows : []).map((r, i) => [i, Number(r.pcr_oi)]).filter(p => !isNaN(p[1]));
      if (!pts.length) return ocEmpty("#ocChartPcrHist", "no captured PCR history yet");
      const svg = ocLineChart([{ color: "var(--gold-soft)", points: pts }], { xLabel: () => "", yFmt: (y) => y.toFixed(2) });
      $("#ocChartPcrHist").innerHTML = svg || "";
    } catch (e) { ocEmpty("#ocChartPcrHist", "unavailable"); }
  }

  async function ocRenderVolSurface(underlying) {
    try {
      const j = await api(`/api/optionchain/${encodeURIComponent(underlying)}/vol-surface`);
      if (!j || j.status !== "ok" || !j.points || !j.points.length) return ocEmpty("#ocChartVolSurface", "no captured multi-expiry Greeks yet");
      const expiries = j.expiries;
      const strikes = [...new Set(j.points.map(p => p.strike))].sort((a, b) => a - b);
      const byKey = {};
      for (const p of j.points) byKey[`${p.expiry}|${p.strike}|${p.option_type}`] = p.iv;
      // average CE/PE IV per (expiry,strike) when both exist, else whichever is present
      const valueAt = (e, s) => {
        const c = byKey[`${e}|${s}|CE`], pv = byKey[`${e}|${s}|PE`];
        if (c != null && pv != null) return (c + pv) / 2;
        return c != null ? c : pv;
      };
      const svg = ocHeatmap(expiries, strikes, valueAt);
      $("#ocChartVolSurface").innerHTML = (svg || "") +
        `<p class="hint" style="margin:4px 0 0">${expiries.length} expiries × ${strikes.length} strikes, darker/redder = higher IV</p>`;
    } catch (e) { ocEmpty("#ocChartVolSurface", "unavailable"); }
  }

  async function ocRenderStraddlePnl(underlying, expiry) {
    if (!expiry) return ocEmpty("#ocChartStraddle", "no resolved expiry");
    try {
      const j = await api(`/api/optionchain/${encodeURIComponent(underlying)}/straddle-pnl?expiry=${encodeURIComponent(expiry)}`);
      if (!j || j.status !== "ok" || !j.series || !j.series.length) return ocEmpty("#ocChartStraddle", "no captured straddle history for this expiry");
      const pts = j.series.map((r, i) => [i, r.straddle]);
      const pnl = j.series.map((r, i) => [i, r.pnl]);
      const svg = ocLineChart([{ color: "var(--gold-soft)", points: pts, cls: "oc-line-straddle" },
        { color: "var(--violet)", points: pnl, cls: "oc-line-pnl" }], { xLabel: () => "", yFmt: (y) => y.toFixed(0) });
      $("#ocChartStraddle").innerHTML = (svg || "") +
        `<p class="hint" style="margin:4px 0 0">strike ${text(j.strike)} · entry ₹${text(j.entry_premium)} at ${j.entry_ts ? timeStr(j.entry_ts) : "—"} ` +
        `· gold=straddle premium, violet=P&amp;L vs entry · ${j.series.length} pts</p>`;
    } catch (e) { ocEmpty("#ocChartStraddle", "unavailable"); }
  }

  async function ocRenderOiProfile(underlying, expiry) {
    if (!expiry) return ocEmpty("#ocChartOiProfile", "no resolved expiry");
    try {
      const j = await api(`/api/optionchain/${encodeURIComponent(underlying)}/oi-profile?expiry=${encodeURIComponent(expiry)}`);
      if (!j || j.status !== "ok" || !j.strikes || !j.strikes.length) return ocEmpty("#ocChartOiProfile", "no captured OI history for this expiry");
      // latest OI per strike (last row seen for each strike in the series)
      const latest = {};
      for (const row of j.oi_series) latest[row.strike] = row;
      const bars = j.strikes.map(s => {
        const r = latest[s] || {};
        return { x: s, y: (Number(r.ce_oi) || 0) - (Number(r.pe_oi) || 0) };
      });
      const svg = ocBarChart(bars, { xTick: (x) => x.toFixed(0) });
      const cand = j.underlying_candles || [];
      const rng = cand.length ? `${text(cand[0].c)} → ${text(cand[cand.length - 1].c)}` : "—";
      $("#ocChartOiProfile").innerHTML = (svg || "") +
        `<p class="hint" style="margin:4px 0 0">bars = CE OI − PE OI per strike (latest captured) · underlying moved ${rng} over the same window · ${cand.length} candles</p>`;
    } catch (e) { ocEmpty("#ocChartOiProfile", "unavailable"); }
  }

  function ocRenderCharts(j) {
    const a = j.analytics || {};
    ocRenderSmileChart(a);
    ocRenderGexChart(a);
    ocRenderMaxPainChart(a);
    ocRenderGreeksHistory(j.underlying || ocSymbol);
    ocRenderPcrHistory(j.underlying || ocSymbol);
    ocRenderVolSurface(j.underlying || ocSymbol);
    ocRenderStraddlePnl(j.underlying || ocSymbol, j.expiry);
    ocRenderOiProfile(j.underlying || ocSymbol, j.expiry);
    $("#ocChartsNote").textContent = (a.gex || {}).status === "no_oi" || (a.pcr || {}).status === "no_oi"
      ? "This underlying has no captured OI (quote-only source) — OI-dependent charts above stay empty by design, not an error."
      : "";
  }

  (function ocWire() {
    const sel = $("#ocSymbol");
    if (sel) sel.addEventListener("change", () => ocCommitSymbol(sel.value));
    ["#ocExpiry", "#ocLive", "#ocBaseChk", "#ocBaseTs"].forEach(id => {
      const el = $(id);
      if (el) el.addEventListener("change", () => loadOptionchain());
    });
    const rb = $("#ocRefresh");
    if (rb) rb.addEventListener("click", () => loadOptionchain());
  })();

  // Test seam — inert in production (window.__CHK_TEST__ is never set there).
  // Lets the dependency-free render smoke test drive view loaders without a DOM
  // framework or a build step.
  if (typeof window !== "undefined" && window.__CHK_TEST__) {
    window.__chk = { setView, loadOverview, loadSignals, loadTrades, loadScalp,
      loadResearch, loadSystem, loadReport, loadAutoscalp, loadMonitor, loadMathScalp,
      loadOrderflow, ofFilter, ofCommitSymbol, ofRenderMenu, ofSelected: () => ofSymbol,
      loadOptionchain, ocCommitSymbol, ocSelected: () => ocSymbol,
      prependFeed, renderHealthLine,
      // Focus combobox seam
      msFilterUniverse, msUniverseList, msCommitFocus, msRenderMenu, msExchOf,
      msSelectedFocus: () => selectedFocusIndex,
      msReqToken: () => _msReqSeq,
      msIsStale: (t) => t !== _msReqSeq,
      msFocusMsg: () => (($("#msFocusMsg") || {}).textContent) || "" };
  }

  // ---------------- Boot ----------------
  connectWs();
  loadInstruments();
  setView("signalshub");
  setInterval(() => { if (state.view === "signalshub") loadSignalsHub(); }, 15000);
  setInterval(() => { if (state.view === "overview") loadOverview(); }, 15000);
  setInterval(() => { if (state.view === "scalp") loadScalp(); }, 3000);
  setInterval(() => { if (state.view === "monitor") loadMonitor(); }, 1500);
  setInterval(() => { if (state.view === "autoscalp") loadAutoscalp(); }, 3000);
  setInterval(() => { if (state.view === "mathscalp") loadMathScalp(); }, 12000);
  setInterval(() => { if (state.view === "orderflow") loadOrderflow(); }, 20000);
  setInterval(() => { if (state.view === "optionchain") loadOptionchain(); }, 20000);
  // refresh the health panel while it is on screen (selfcheck is cheap)
  setInterval(() => { if (state.view === "system") loadSystem(); }, 10000);
})();
