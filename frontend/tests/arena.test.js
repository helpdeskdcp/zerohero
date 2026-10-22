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
  assert.ok(A.verdictText({ verdict: "DRAW", days: 12, diff_total_rs: 800, ci95_rs: [-500, 2000] }, "INSTITUTION_TRADER").startsWith("DRAW"));
  assert.ok(A.verdictText({ verdict: "A_AHEAD", days: 12, diff_total_rs: 5000, ci95_rs: [100, 9000] }, "INSTITUTION_TRADER").includes("Ahead ≠ proven"));
  assert.ok(A.verdictText({ verdict: "INSUFFICIENT_DAYS", days: 2 }, "X").includes("Too early"));
});
test("side card handles empty and populated summaries", () => {
  assert.deepEqual(A.sideCard("AI", {}).lines, ["no signals yet"]);
  const c = A.sideCard("AI", { signals: 3, settled: 2, by_status: { SETTLED: 2, MISSED: 1 }, net_rs: -500, return_pct: -1, capital: 50000, win_rate: 0.5, profit_factor: null, max_drawdown_rs: 700, max_drawdown_pct: 1.4, gross_rs: -400, cost_rs: 100, real_quote_fill_share: 1 });
  assert.ok(c.lines.some((l) => l.includes("net −₹500")) && c.lines.some((l) => l.includes("PF —")));
});

test("management event form mirrors server rules", () => {
  const base = { signal_id: "H00001", confirm: true };
  assert.deepEqual(A.validateEvent({ ...base, action: "EXIT_NOW" }).payload, { signal_id: "H00001", action: "EXIT_NOW" });
  assert.deepEqual(A.validateEvent({ ...base, action: "AMEND_TARGETS", targets: "180, 150,150" }).payload.targets, [150, 180]);
  assert.equal(A.validateEvent({ ...base, action: "TRAIL_SL", trail_pct: "5" }).payload.trail_pct, 5);
  for (const bad of [{ action: "AMEND_SL", sl: "" }, { action: "AMEND_TARGETS", targets: "" }, { action: "TRAIL_SL" }, { action: "TRAIL_SL", trail_points: "1", trail_pct: "5" }, { action: "TRAIL_SL", trail_pct: "100" }, { action: "NOPE" }, { action: "CANCEL", confirm: false }, { action: "CANCEL", signal_id: "" }]) {
    assert.equal(A.validateEvent({ ...base, ...bad }).ok, false, JSON.stringify(bad));
  }
});

test("every time on the page is India Standard Time (UTC+5:30), never UTC", () => {
  assert.equal(A.fmtIsoIST("2026-10-22T04:15:00+00:00", false), "2026-10-22 09:45");               // Mohit's example: 04:15 UTC is 09:45 IST
  assert.equal(A.fmtIsoIST("2026-10-21T03:48:42Z", true), "2026-10-21 09:18:42");                  // H00003's Telegram post time
  assert.equal(A.fmtIST(Date.UTC(2026, 9, 21, 18, 30), false), "2026-10-22 00:00");                // rolls over midnight IST
});
test("entry-only profile: stop and targets may be blank, everyone else still needs them", () => {
  const blank = { ...ok, sl: "", targets: "" };
  assert.equal(A.validateForm(blank).ok, false); A.setRiskModule(true);
  assert.equal(A.validateForm(blank).ok, true);
  assert.equal(A.validateForm({ ...ok, sl: "120" }).ok, false, "a stop that is given must still be below the entry");
  A.setRiskModule(false); assert.equal(A.validateForm(blank).ok, false);
});

test("live watch rows: WAITING stays amber with the reason, ACTIVE shows ltp and green/red open P&L, nothing for empty", () => {
  const d = { now_ist: "10:15:03", total_open_pnl_rs: 1200, realised_today_rs: -300, rows: [
    { id: "H1", label: "SENSEX 73600 PE (22/10)", state: "ACTIVE", entry_mode: "LIMIT", entry: 520, fill: 513.95, ltp: 560.5, bid: 560, ask: 561, quote_age_s: 3, pnl_rs: 1200.4, pnl_pct: 8.9, sl: 380, to_sl: 180, targets: [600, 700], to_t1: 40, posted_ist: "09:19:54" },
    { id: "H2", label: "NIFTY 23100 PE (27/10)", state: "WAITING", entry_mode: "LIMIT", entry: 175, ltp: 190, bid: 189, ask: 190.5, quote_age_s: 5, sl: 90, targets: [220], waiting_note: "waiting for the ask to come down to 175 (ask now 190.5)", valid_until_ist: "09:37:52", posted_ist: "09:22:52" },
    { id: "H3", label: "NG 270 CE (23/10)", state: "ACTIVE", ltp: 5, bid: 4.9, ask: 5, fill: 5.4, sl: 4.5, targets: [6], pnl_rs: -640, pnl_pct: -9, to_sl: 0.4, to_t1: 1.1, quote_age_s: 80, posted_ist: "10:00:00" }] };
  const h = A.liveRows(d);
  assert.ok(h.includes("chip ACTIVE") && h.includes("chip WAITING") && h.includes("pnlpos") && h.includes("pnlneg") && h.includes("+₹1,200") && h.includes("−₹640"));
  assert.ok(h.includes("waiting for the ask to come down to 175") && h.includes("valid till") && h.includes("quote 80 s old"));
  assert.ok(A.liveRows({ ...d, rows: [] }).includes("No signals today yet"));
  const d2 = { ...d, rows: [{ ...d.rows[0], ltp: 565 }] }; assert.ok(A.liveRows(d2).includes("tickup"), "an up-tick flashes");                // second render with a higher ltp
  const d3 = { ...d, rows: [{ ...d.rows[0], ltp: 561 }] }; assert.ok(A.liveRows(d3).includes("tickdn"), "a down-tick flashes");
  assert.ok(!A.liveRows({ ...d, rows: [{ id: "<b>", label: "<script>x</script>", state: "CLOSED", pnl_rs: 5, fill: 1, exit: 2, why: "TARGET", posted_ist: "1" }] }).includes("<script>"), "labels are escaped");
});
