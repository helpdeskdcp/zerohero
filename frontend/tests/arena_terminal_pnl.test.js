/* Run: node tests/arena_terminal_pnl.test.js — the open-position card shows gross, charges and net separately (RELIANCE BUY 1000 sh, fill 1175.10, LTP 1175.20). */
const assert = require("assert");
const T = require("../static/js/arena_terminal.js");
const row = { id: "H1", state: "ACTIVE", instrument: "EQUITY", label: "RELIANCE BUY 1000 sh", lots: 1000, fill: 1175.1, ltp: 1175.2, bid: 1175.15, ask: 1175.25, sl: 1170, targets: [1180], pnl_rs: -363.7,
  gross_pnl_rs: 100, est_charges_rs: 463.8, net_pnl_rs: -363.7, mark_price: 1175.2, mark_basis: "ltp", exit_now_net_rs: -413.7, stale: false, quote_age_s: 1 };
const t = T.tmBreakdown(row);
assert.ok(t.includes("gross +₹100") && t.includes("est. charges −₹464") && t.includes("net −₹364") && t.includes("mark 1,175.2") && t.includes("(ltp)") && t.includes("exit now −₹414"), t);
const html = T.tmRowHtml(row, 1175.1, null);
assert.ok(html.includes("gross +₹100") && html.includes("−₹364") && html.includes("1000 sh"), html);                      // the big P&L is the net, the line under it explains it
assert.strictEqual(T.tmBreakdown({ ...row, stale: true }), "quote is stale: no P&L shown until a fresh price arrives");
assert.strictEqual(T.tmBreakdown({ ...row, state: "WAITING" }), "");
assert.strictEqual(T.tmBreakdown({ ...row, gross_pnl_rs: null }), "");
console.log("arena_terminal_pnl ok");
