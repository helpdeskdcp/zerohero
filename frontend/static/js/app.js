// Chanakya AI — dashboard client. No build step; vanilla JS.
(() => {
  const state = { view: "overview", tradeFilter: "" };

  // ---------------- View routing (shared by sidebar nav + bottom tab bar) ----------------
  function setView(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === `view-${view}`));
    document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
    document.querySelectorAll(".tab-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
    if (view === "overview") loadOverview();
    if (view === "signals") loadSignals();
    if (view === "trades") loadTrades();
    if (view === "monitor") loadMonitor();
    if (view === "scalp") loadScalp();
    if (view === "research") loadResearch();
    if (view === "system") loadSystem();
    if (view === "autoscalp") loadAutoscalp();
  }
  document.querySelectorAll(".nav-item, .tab-item").forEach(btn => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  // ---------------- Helpers ----------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const fmt = (n, d = 2) => (n === null || n === undefined || isNaN(n)) ? "—" : Number(n).toFixed(d);
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
  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = apiToken ? `?token=${encodeURIComponent(apiToken)}` : "";
    ws = new WebSocket(`${proto}://${location.host}/ws${q}`);
    ws.onopen = () => setWsStatus(true);
    ws.onclose = () => { setWsStatus(false); setTimeout(connectWs, 2500); };
    ws.onerror = () => ws.close();
    ws.onmessage = (evt) => {
      try { handleWsMessage(JSON.parse(evt.data)); } catch { /* ignore */ }
    };
    // keepalive ping
    setInterval(() => { if (ws.readyState === 1) ws.send("ping"); }, 20000);
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
      <span class="feed-dir ${sig.direction}">${sig.direction}</span>
      <span class="feed-meta">${sig.underlying || sig.symbol || "—"} · ${sig.decision} · ${sig.market_regime || "—"} · prob ${fmt(sig.probability, 1)}%</span>
      <span class="feed-time">${timeStr(sig.created_ts)}</span>
    `;
    feed.prepend(li);
    while (feed.children.length > 40) feed.removeChild(feed.lastChild);
  }

  // ---------------- Overview ----------------
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
    } catch (e) { console.error(e); }
  }

  // ---------------- Signal Ledger ----------------
  async function loadSignals() {
    try {
      const rows = await api("/api/signals?limit=300");
      const tbody = $("#signalsTable tbody");
      tbody.innerHTML = rows.map(r => `
        <tr>
          <td>${timeStr(r.created_ts)}</td>
          <td>${r.underlying || r.symbol || "—"}</td>
          <td class="feed-dir ${r.direction}">${r.direction || "—"}</td>
          <td><span class="badge ${r.decision}">${r.decision || "—"}</span></td>
          <td>${r.market_regime || "—"}</td>
          <td>${fmt(r.probability, 1)}%</td>
          <td>${fmt(r.confidence, 1)}%</td>
          <td>${fmt(r.risk_reward, 2)}</td>
          <td><span class="badge ${r.risk_status}">${r.risk_status || "—"}</span></td>
          <td class="reason-cell">${(r.reason || "").slice(0, 160)}</td>
        </tr>
      `).join("") || `<tr><td colspan="10" class="hint">No signals logged yet.</td></tr>`;
    } catch (e) { console.error(e); }
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
          <td><span class="badge ${r.strategy === "SCALP" ? "OPEN" : "UNKNOWN"}">${r.strategy || "CORE"}</span></td>
          <td>${r.underlying || "—"}</td>
          <td>${r.option_type || r.instrument || "—"} ${r.strike || ""}</td>
          <td class="feed-dir ${r.direction}">${r.direction || "—"}</td>
          <td>${fmt(r.entry, 2)}</td>
          <td>${fmt(r.stop_loss, 2)}</td>
          <td>${fmt(r.target_1, 2)}</td>
          <td>${fmt(r.quantity, 0)}</td>
          <td><span class="badge ${r.status}">${r.status}</span></td>
          <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmt(r.pnl, 2)}</td>
          <td>${r.status === "OPEN" ? `<button class="btn btn-ghost close-trade-btn" data-id="${r.trade_id}" data-entry="${r.entry}">Close</button>` : ""}</td>
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
    } catch (e) { console.error(e); }
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
    } catch (e) { console.error(e); }
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
        <td>${r.underlying || "—"}</td>
        <td>${r.option_type || r.instrument || "—"} ${r.strike || ""}</td>
        <td class="feed-dir ${r.direction}">${r.direction || "—"}</td>
        <td>${fmt(r.entry, 2)}</td>
        <td>${mark !== null ? fmt(mark, 2) : "—"}</td>
        <td>${fmt(r.target_1, 2)}</td>
        <td>${fmt(r.stop_loss, 2)}</td>
        <td>${fmt(r.quantity, 0)}</td>
        <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmt(r.pnl, 2)}</td>
        <td><span class="badge ${r.status}">${r.status}</span></td>
        <td class="exit-cell">${r.exit_reason || ""}</td>
        <td>${r.status === "OPEN" ? `<button class="btn btn-ghost untrack-btn" data-id="${r.trade_id}" data-mark="${mark ?? r.entry}">Untrack</button>` : ""}</td>
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
          <td>${r.underlying || r.market || "—"}</td>
          <td>${r.setup || "—"}</td>
          <td class="feed-dir ${r.direction}">${r.direction || "—"}</td>
          <td>${fmt(r.entry, 2)}</td>
          <td>${fmt(r.stop_loss, 2)}</td>
          <td>${fmt(r.target_1, 2)}</td>
          <td>${fmt(r.quantity, 0)}</td>
          <td>${fmtHold(held)}</td>
          <td class="stat-value pos" style="font-size:11px">${fmt(r.mfe, 2)}</td>
          <td class="stat-value neg" style="font-size:11px">${fmt(r.mae, 2)}</td>
          <td><span class="badge ${r.status}">${r.status}</span></td>
          <td class="exit-cell">${r.exit_reason || ""}</td>
          <td class="${(r.pnl||0) > 0 ? 'stat-value pos' : (r.pnl||0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmt(r.pnl, 2)}</td>
          <td>${r.status === "OPEN" ? `<button class="btn btn-ghost close-scalp-btn" data-id="${r.trade_id}" data-entry="${r.entry}">Close</button>` : ""}</td>
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
    f.execution_mode.value = cfg.execution_mode || "PAPER";
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
    } catch (e) { console.error(e); }
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
    } catch (e) { console.error(e); }
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
      const decisionRows = Object.entries(r.signals.by_decision).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
      const regimeRows = Object.entries(r.signals.by_market_regime).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
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
    } catch (e) { console.error(e); }
  }
  $("#refreshResearch").addEventListener("click", loadResearch);

  // ---------------- System ----------------
  async function loadSystem() {
    try {
      const env = await api("/api/env-check");
      const health = await api("/api/health");
      const grid = $("#sysGrid");
      grid.innerHTML = Object.entries(env).map(([k, v]) => `
        <div class="syscell"><span>${k}</span><span class="dot ${v ? "on" : "off"}"></span></div>
      `).join("") + `
        <div class="syscell"><span>API</span><span class="dot on"></span></div>
        <div class="syscell"><span>Live Trading</span><span class="dot ${health.live_trading ? "on" : "off"}"></span></div>
      `;
    } catch (e) { console.error(e); }
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
    let st, sigs, snaps, pos, allTr, allSnap;
    try {
      [st, sigs, snaps, pos, allTr, allSnap] = await Promise.all([
        api("/api/autoscalp/status"),
        api(`/api/autoscalp/signals?limit=60${q}`),
        api(`/api/autoscalp/snapshots?limit=60${q}`),
        api("/api/trades?limit=100&status=OPEN"),
        api("/api/trades?limit=300"),
        api("/api/autoscalp/snapshots?limit=40"),
      ]);
    } catch (e) {
      const el = $("#asErr"); el.hidden = false; el.textContent = "autoscalp: " + e.message; return;
    }
    $("#asErr").hidden = true;
    const set = (id, v, cls) => { const el = $("#" + id); if (el) { el.textContent = v; if (cls !== undefined) el.className = cls; } };
    // which symbol's analysis is on screen, and is it actually trading?
    const wl = (AS_UNIVERSE && AS_UNIVERSE.watchlist) || [];
    const inWl = sym && wl.includes(sym);
    set("asSymTag", sym ? `${sym} · ${inWl ? "trading" : "view-only"}` : "—");
    const wlb = $("#asWlBtn");
    if (wlb) { wlb.textContent = inWl ? "− Stop" : "+ Trade"; wlb.disabled = !sym; wlb.dataset.in = inWl ? "1" : "0"; }

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
        return `<button class="as-wl-chip${on}" data-sym="${s}" type="button">
          <b>${s}</b> <span class="${cls}">${net >= 0 ? "+" : ""}${fmt(net, 1)}</span>
          <em>${w}W/${l}L${open ? ` · ${open} open` : ""}</em>
          <i class="badge ${dec}">${dec}</i></button>`;
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
    set("asRegime", text(latest.regime));
    set("asIndex", fmt(latest.index_ltp, 1));
    set("asVwap", fmt(latest.vwap, 1));
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
      <td class="${(t.pnl || 0) > 0 ? 'stat-value pos' : (t.pnl || 0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmt(t.pnl, 2)}</td>
      <td><span class="badge ${t.status}">${t.status}</span></td><td>${text(t.exit_reason, "")}</td></tr>`).join("")
      || `<tr><td colspan="11" class="hint">No open PAPER positions.</td></tr>`;

    // Signal Log: the actual PAPER trades (locked contract + outcome) come first;
    // if there are none yet, show the recent decision stream so the operator can
    // see the engine is evaluating and *why* it is holding (NO_TRADE / WATCH).
    const sigRows = (sigs || []).map(s => `
      <tr><td>${timeStr(s.created_ts)}</td><td>${text(s.symbol)}</td>
      <td class="badge ${s.decision}">${text(s.decision)}</td><td>${text(s.signal_type)}</td>
      <td class="feed-dir ${directionClass(s.direction === "BULLISH" ? "BUY" : s.direction === "BEARISH" ? "SELL" : "")}">${text(s.direction)}</td>
      <td>${text(s.opt_strike, "")}</td><td>${fmt(s.entry, 2)}</td><td>${fmt(s.stop_loss, 2)}</td>
      <td>${fmt(s.target_1, 2)}</td><td>${s.probability != null ? Math.round(s.probability * 100) + "%" : "—"}</td>
      <td>${text(s.confidence, "")}</td><td><span class="badge ${s.status}">${text(s.status)}</span></td>
      <td>${text(s.outcome, "")}</td>
      <td class="${(s.points || 0) > 0 ? 'stat-value pos' : (s.points || 0) < 0 ? 'stat-value neg' : ''}" style="font-size:12px">${fmt(s.points, 1)}</td></tr>`).join("");
    const evalRows = (snaps || []).slice(0, 30).map(s => `
      <tr class="hint"><td>${timeStr(s.ts)}</td><td>${text(s.symbol)}</td>
      <td class="badge ${s.decision}">${text(s.decision)}</td><td>${text(s.signal_type)}</td>
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

  const _origHandleWs = handleWsMessage;
  handleWsMessage = function (msg) {
    _origHandleWs(msg);
    if (state.view === "autoscalp" && /^autoscalp_/.test(msg.type || "")) loadAutoscalp();
  };

  // ---------------- Boot ----------------
  connectWs();
  loadInstruments();
  setView("overview");
  setInterval(() => { if (state.view === "overview") loadOverview(); }, 15000);
  setInterval(() => { if (state.view === "scalp") loadScalp(); }, 3000);
  setInterval(() => { if (state.view === "monitor") loadMonitor(); }, 1500);
  setInterval(() => { if (state.view === "autoscalp") loadAutoscalp(); }, 3000);
})();
