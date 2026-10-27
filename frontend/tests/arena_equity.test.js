/* Equity ticket helpers + the Terminal's equity awareness. Run: node tests/arena_equity.test.js */
const assert = require("assert");
const Q = require("../static/js/arena_equity.js"), T = require("../static/js/arena_terminal.js");
const base = { symbol: "reliance", side: "BUY", qty: "10", entry_mode: "NEAR", entry: "1000", sl: "990", targets: "1010, 1020", confirmNotEntered: true };
let r = Q.eqValidate(base); assert.ok(r.ok, r.errors.join()); assert.deepStrictEqual(r.payload, { instrument: "EQUITY", underlying: "RELIANCE", side: "BUY", entry_mode: "NEAR", entry: 1000, qty: 10, targets: [1010, 1020], note: "", sl: 990 });
r = Q.eqValidate({ ...base, side: "SELL", sl: "1010", targets: "990, 980" }); assert.ok(r.ok, r.errors.join()); assert.deepStrictEqual(r.payload.targets, [990, 980]);
for (const bad of [{ sl: "1010" }, { targets: "990" }, { qty: "0" }, { qty: "1.5" }, { symbol: "BAD SYM" }, { entry: "" }, { confirmNotEntered: false }, { qty: "5000", entry: "1000" }]) assert.ok(!Q.eqValidate({ ...base, ...bad }).ok, JSON.stringify(bad));
assert.ok(!Q.eqValidate({ ...base, side: "SELL", sl: "990" }).ok); assert.ok(!Q.eqValidate({ ...base, side: "SELL", targets: "1010" }).ok);
assert.ok(Q.eqValidate({ ...base, sl: "" }).ok && !("sl" in Q.eqValidate({ ...base, sl: "" }).payload));                // stop-loss is optional
assert.deepStrictEqual(Q.eqAutoLevels("BUY", 1000), { sl: 995, targets: [1005, 1010] }); assert.deepStrictEqual(Q.eqAutoLevels("SELL", 1000), { sl: 1005, targets: [995, 990] });
assert.ok(Q.eqQuoteLine({ symbol: "RELIANCE", name: "RELIANCE", last: { ltp: 1000, bid: 999.9, ask: 1000.1 }, age_s: 2 }).includes("bid 999.9 / ask 1,000.1")); assert.ok(Q.eqQuoteLine(null).includes("3-second"));

// Terminal: chart path, editor, row text
assert.ok(T.tmChartPath({ instrument: "EQUITY", underlying: "INFY" }, 5, 100).startsWith("/chart/equity?symbol=INFY&tf=5&since=100"));
assert.ok(T.tmChartPath({ underlying: "NIFTY", expiry: "2026-10-29", strike: 25000, option_type: "CE" }, 1).startsWith("/chart?underlying=NIFTY"));
const S = { id: "H9", state: "ACTIVE", instrument: "EQUITY", side: "SELL", fill: 1000, sl: null, targets: [], lots: 10, ask: 1000.1, bid: 999.9, label: "RELIANCE SELL 10 sh" };
assert.strictEqual(T.tmEditable(S).trailPct, false); assert.ok(!T.tmEditHtml(S).includes('data-f="trail_pct"') && T.tmEditHtml(S).includes('data-f="trail_points"'));
let x = T.tmEditEvents(S, { sl: "1005", targets: "990, 980" }); assert.deepStrictEqual(x.ev, [{ action: "AMEND_SL", sl: 1005 }, { action: "AMEND_TARGETS", targets: [980, 990] }]); assert.deepStrictEqual(x.warn, []);
x = T.tmEditEvents(S, { sl: "995" }); assert.ok(x.warn.some((w) => /short's stop must be above/.test(w))); x = T.tmEditEvents(S, { trail_pct: "1" }); assert.deepStrictEqual(x.ev, []);
x = T.tmEditEvents(S, { targets: "1010" }); assert.ok(x.warn.some((w) => /short's targets must be below/.test(w)));
const W = { id: "H8", state: "WAITING", instrument: "EQUITY", side: "BUY", entry_mode: "LIMIT", entry: 995, sl: 990, targets: [1010], lots: 10, label: "RELIANCE BUY 10 sh" };
assert.ok(T.tmEditHtml(W).includes("Qty (shares)") && T.tmEditHtml(W).includes('data-f="entry"')); assert.ok(T.tmRowHtml({ ...W, ltp: 1000, bid: 999.9, ask: 1000.1 }, null, null).includes("10 sh"));
console.log("arena_equity: ok");
