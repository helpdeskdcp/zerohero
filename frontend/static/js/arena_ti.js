/* Trading Intelligence (ZeroHero V2) overlay helpers for the Arena charts. Pure functions (node-testable) shared by the Terminal chart and the Submit chart.
   The backend sends dashboard rows, REFERENCE level percentages (statistics, not a signal) and paper trades; nothing here can place an order. */
const TI_COLORS = { sl: "#ef4444", t: ["#22c55e", "#84cc16", "#22d3ee", "#e879f9"], ref: "#9ca3af", paper: "#60a5fa" };
function tiDashText(ti) {
  if (!ti) return ""; if (ti.error) return "TI: " + ti.error; const d = ti.dash || [];
  return d.length ? d.map((r) => r.filter(Boolean).join("  ")).join("\n") : "TI: waiting for data";
}
/* price lines for the selected OPTION premium chart: grey dashed REFERENCE levels around the last price + the levels of OPEN paper trades of this contract */
function tiRefLines(lastPrice, ti) {
  const out = []; if (!ti || !(lastPrice > 0)) return out; const r = ti.reference;
  if (r) {
    out.push({ price: +(lastPrice * (1 + r.sl_pct)).toFixed(2), color: TI_COLORS.ref, style: 2, title: `TI ref SL ${Math.round(r.sl_pct * 100)}%` + (r.p_stop_20pct != null ? ` (p stop ${Math.round(r.p_stop_20pct * 100)}%)` : "") });
    (r.targets_pct || []).forEach((p, i) => out.push({ price: +(lastPrice * (1 + p)).toFixed(2), color: TI_COLORS.ref, style: 2, title: `TI ref T${i + 1} +${(p * 100).toFixed(1)}%` + (i === 1 && r.p_target_first_10pct != null ? ` (p +10% before -20%: ${Math.round(r.p_target_first_10pct * 100)}%)` : "") }));
  }
  ((ti.paper && ti.paper.trades) || []).filter((t) => t.closed == null).forEach((t) => {
    out.push({ price: t.entry, color: TI_COLORS.paper, style: 0, title: "TI paper ENTRY" }); out.push({ price: t.sl, color: TI_COLORS.sl, style: 0, title: "TI paper SL" });
    (t.targets || []).forEach((p, i) => out.push({ price: p, color: TI_COLORS.t[i] || TI_COLORS.ref, style: 0, title: "TI paper T" + (i + 1) }));
  });
  return out;
}
/* markers for paper trades of the selected contract: BUY when opened, EXIT (with reason) when closed. snap(times, t) maps to the chart's bar time. */
function tiMarkers(times, ti, snap, shift) {
  const out = []; if (!ti || !ti.paper) return out;
  for (const t of ti.paper.trades || []) {
    const a = snap(times, Math.floor(t.opened) + shift); if (a != null) out.push({ time: a, position: "belowBar", color: "#60a5fa", shape: "arrowUp", text: "TI paper BUY" });
    if (t.closed != null) { const b = snap(times, Math.floor(t.closed) + shift); if (b != null) out.push({ time: b, position: "aboveBar", color: t.net_rs >= 0 ? "#22c55e" : "#f87171", shape: "square", text: "TI " + (t.reason || "EXIT") }); }
  }
  return out;
}
if (typeof module !== "undefined") module.exports = { tiDashText, tiRefLines, tiMarkers, TI_COLORS };
