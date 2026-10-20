const test = require("node:test"), assert = require("node:assert");
const A = require("../static/js/arena.js");
const ok = { underlying: "nifty", expiry: "27OCT2026", strike: "23000", option_type: "ce", entry_mode: "above", entry: "100", sl: "85", targets: "140, 120,120", note: "x", confirmNotEntered: true };

test("valid form -> normalised payload and preview", () => {
  const r = A.validateForm(ok); assert.equal(r.ok, true);
  assert.deepEqual(r.payload.targets, [120, 140]); assert.equal(r.payload.underlying, "NIFTY"); assert.equal(r.payload.option_type, "CE");
  assert.equal(r.preview.risk_per_unit, 15); assert.ok(Math.abs(r.preview.rr_to_t1 - 20 / 15) < 1e-9);
});
test("every server rule is mirrored client-side", () => {
  for (const [patch, frag] of [[{ underlying: "TCS" }, "Underlying"], [{ expiry: "soon" }, "Expiry"], [{ strike: "" }, "Strike"], [{ entry: "0" }, "Entry"], [{ sl: "120" }, "Stop-loss"], [{ sl: "0" }, "Stop-loss"],
    [{ targets: "" }, "target"], [{ targets: "90" }, "target"], [{ option_type: "XX" }, "CE or PE"], [{ confirmNotEntered: false }, "not entered"]]) {
    const r = A.validateForm({ ...ok, ...patch }); assert.equal(r.ok, false, JSON.stringify(patch)); assert.ok(r.errors.join(" ").includes(frag), frag);
  }
});
test("markdown renderer escapes HTML (no script injection) and renders structure", () => {
  const h = A.renderMarkdown("# T\n<script>alert(1)</script> **b** `c`\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |");
  assert.ok(!h.includes("<script>") && h.includes("&lt;script&gt;")); assert.ok(h.includes("<h2>T</h2>") && h.includes("<b>b</b>") && h.includes("<code>c</code>") && h.includes("<ul>") && h.includes("<td>1</td>"));
});
test("number formatting and verdict wording never overclaims", () => {
  assert.equal(A.rs(-1234.4), "−₹1,234"); assert.equal(A.rs(0), "₹0"); assert.equal(A.pct(0.256), "25.6%");
  assert.ok(A.verdictText({ verdict: "DRAW", days: 12, diff_total_rs: 800, ci95_rs: [-500, 2000] }, "SEBI_TRADER").startsWith("DRAW"));
  assert.ok(A.verdictText({ verdict: "A_AHEAD", days: 12, diff_total_rs: 5000, ci95_rs: [100, 9000] }, "SEBI_TRADER").includes("Ahead ≠ proven"));
  assert.ok(A.verdictText({ verdict: "INSUFFICIENT_DAYS", days: 2 }, "X").includes("Too early"));
});
test("side card handles empty and populated summaries", () => {
  assert.deepEqual(A.sideCard("AI", {}).lines, ["no signals yet"]);
  const c = A.sideCard("AI", { signals: 3, settled: 2, by_status: { SETTLED: 2, MISSED: 1 }, net_rs: -500, return_pct: -1, capital: 50000, win_rate: 0.5, profit_factor: null, max_drawdown_rs: 700, max_drawdown_pct: 1.4, gross_rs: -400, cost_rs: 100, real_quote_fill_share: 1 });
  assert.ok(c.lines.some((l) => l.includes("net −₹500")) && c.lines.some((l) => l.includes("PF —")));
});
