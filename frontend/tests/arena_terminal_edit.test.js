/* Order editor: which fields an order allows, and the /amend events a form produces. Run: node tests/arena_terminal_edit.test.js */
const assert = require("assert");
const T = require("../static/js/arena_terminal.js");
const W = { id: "H1", state: "WAITING", entry_mode: "LIMIT", entry: 100, sl: 90, targets: [120, 140], lots: 1, label: "NIFTY 25000 CE" };
const A = { id: "H2", state: "ACTIVE", entry_mode: "NEAR", entry: 100, fill: 101, sl: null, targets: [], lots: 2, bid: 105, label: "X" };
assert.deepStrictEqual(T.tmEditable(W), { entry: true, sl: true, targets: true, lots: true, trail: false, cancel: true });
assert.strictEqual(T.tmEditable(A).entry, false); assert.strictEqual(T.tmEditable(A).lots, false); assert.strictEqual(T.tmEditable(A).trail, true);
assert.strictEqual(T.tmEditable({ state: "CLOSED" }), null);
assert.strictEqual(T.tmEditable({ ...W, entry_mode: "NEAR" }).entry, false);                      // NEAR / MARKET has no trigger price

let x = T.tmEditEvents(W, { entry: "101.5", sl: "90", targets: "120, 140", lots: "1" });          // only the entry changed
assert.deepStrictEqual(x.ev, [{ action: "AMEND_ENTRY", entry: 101.5 }]); assert.deepStrictEqual(x.err, []);
x = T.tmEditEvents(A, { sl: "100", targets: "110, 120" });                                           // an ACTIVE trade with no SL / targets: set them
assert.deepStrictEqual(x.ev, [{ action: "AMEND_SL", sl: 100 }, { action: "AMEND_TARGETS", targets: [110, 120] }]);
assert.ok(x.warn.length === 0);
x = T.tmEditEvents(A, { sl: "106" }); assert.ok(x.warn.some((w) => /current bid|at\/above/.test(w)));  // SL above the bid exits at once
x = T.tmEditEvents(A, { trail_points: "3" }); assert.deepStrictEqual(x.ev, [{ action: "TRAIL_SL", trail_points: 3 }]);
assert.ok(T.tmEditEvents(A, { trail_points: "3", trail_pct: "2" }).err.length);
assert.ok(T.tmEditEvents(W, { entry: "-1" }).err.length); assert.ok(T.tmEditEvents(W, { targets: "a,b" }).err.length); assert.ok(T.tmEditEvents(W, { lots: "1.5" }).err.length);
assert.ok(T.tmEditEvents({ state: "CLOSED" }, {}).err.length);
const h = T.tmEditHtml(W); assert.ok(h.includes('data-f="entry"') && h.includes('data-act="cancel"')); assert.ok(!T.tmEditHtml(A).includes('data-f="entry"') && T.tmEditHtml(A).includes('data-f="trail_points"'));
assert.strictEqual(T.tmEditHtml({ state: "CLOSED" }), "");
console.log("arena_terminal_edit: ok");
