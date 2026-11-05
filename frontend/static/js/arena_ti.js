/* Trading Intelligence (ZeroHero V2) overlay helpers for the Arena charts. Pure functions (node-testable) shared by the Terminal chart and the Submit chart.
   The backend sends dashboard rows, REFERENCE level percentages (statistics, not a signal) and paper trades; nothing here can place an order. */
const TI_COLORS = { sl: "#ef4444", t: ["#22c55e", "#84cc16", "#22d3ee", "#e879f9"], ref: "#9ca3af", paper: "#60a5fa" };
function tiDashText(ti, compact) {
  if (!ti) return ""; if (ti.error) return "TI: " + ti.error; const d = ti.dash || [];
  if (!d.length) return "TI: waiting for data";
  const pick = compact ? d.filter((r, i) => i === 0 || ["Now", "Edge", "Cost"].includes(r[0])) : d;      // narrow chart (phone): title + Now + Edge + Cost only
  return pick.map((r) => r.filter(Boolean).join("  ")).join("\n");
}
/* plain-language meaning of every line, shown when the box is tapped */
const TI_LEGEND = [
  "Edge  = सिद्ध झालेला फायदा (none validated = अजून कोणताही नियम खर्चानंतर नफा देत नाही)",
  "Now  = सध्याचा निर्णय. NO TRADE = ट्रेड घेऊ नका",
  "Mode  = data only: या instrument चे मॉडेल नाही, फक्त खरे भाव आणि खर्च दाखवतो",
  "Score  = मॉडेलचा अंदाज; threshold च्या खाली = निवडला जात नाही",
  "Cost  = १ lot चा खर्च; BE = नफ्यासाठी option ची किमान वाढ (points)",
  "ATM  = मधला strike; spread = खरेदी/विक्री भावातील फरक; thin = कमी liquid",
  "Paper  = कागदी (खोटे) ट्रेड, खरे order नाहीत",
  "राखाडी रेषा = सरासरी आकडेवारीवरून संदर्भ पातळ्या, signal नाही",
].join("\n");
function tiBoxText(ti, compact, expanded) { return expanded ? tiDashText(ti, false) + "\n\n" + TI_LEGEND + "\n(बंद करायला पुन्हा टॅप करा)" : tiDashText(ti, compact) + (ti && !ti.error ? "\nⓘ टॅप = अर्थ" : ""); }
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
if (typeof module !== "undefined") module.exports = { tiDashText, tiBoxText, TI_LEGEND, tiRefLines, tiMarkers, TI_COLORS };
