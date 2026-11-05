const assert = require("assert"); const T = require("../static/js/arena_ti.js");
const ti = { dash: [["TI v2 · NIFTY", "paper-only"], ["Edge", "NO VALIDATED EDGE (5/5 experiments REJECT)"], ["Now", "NO TRADE"]], reference: { sl_pct: -0.2, targets_pct: [0.056, 0.109, 0.187, 0.255], p_target_first_10pct: 0.274, p_stop_20pct: 0.065 },
  paper: { trades: [{ opened: 1000, closed: null, entry: 100, sl: 80, targets: [105, 110] }, { opened: 500, closed: 900, entry: 90, sl: 72, targets: [95], reason: "SL", net_rs: -300 }] } };
assert.strictEqual(T.tiDashText(ti), "TI v2 · NIFTY  paper-only\nEdge  NO VALIDATED EDGE (5/5 experiments REJECT)\nNow  NO TRADE"); assert.strictEqual(T.tiDashText(null), ""); assert.strictEqual(T.tiDashText({ error: "x" }), "TI: x");
const L = T.tiRefLines(100, ti, { strike: 24400, option_type: "CE" }); assert.strictEqual(L.filter((x) => x.title.startsWith("TI plan")).length, 6); assert.strictEqual(L[0].title, "TI plan ENTRY"); assert.strictEqual(L[0].price, 100); assert.strictEqual(L[1].price, 80); assert.ok(L[1].title.includes("stop hit 7%") || L[1].title.includes("stop hit 6%")); assert.strictEqual(L[1].color, T.TI_COLORS.sl);
assert.strictEqual(L[2].price, 105.6); assert.strictEqual(L[2].color, T.TI_COLORS.t[0]); assert.strictEqual(L[5].price, 125.5); assert.ok(L.some((x) => x.title === "TI paper ENTRY" && x.price === 100)); assert.ok(L.some((x) => x.title === "TI paper T2" && x.price === 110)); assert.strictEqual(L.filter((x) => x.title.startsWith("TI paper")).length, 4, "only the OPEN paper trade draws lines");
assert.deepStrictEqual(T.tiRefLines(0, ti), []); assert.deepStrictEqual(T.tiRefLines(100, null), []);
// a real BUY decision for THIS contract replaces the plan with solid levels; for another contract the plan stays
const buy = { ...ti, decision: { action: "BUY CE", strike: 24400, option_type: "CE", entry: 101, sl: 90, targets: [105, 110, 118, 125] } };
const B = T.tiRefLines(100, buy, { strike: 24400, option_type: "CE" }); assert.ok(B.some((x) => x.title === "TI BUY CE ENTRY" && x.price === 101 && x.style === 0)); assert.ok(B.some((x) => x.title === "TI T4" && x.price === 125)); assert.ok(!B.some((x) => x.title.startsWith("TI plan")));
assert.ok(T.tiRefLines(100, buy, { strike: 24500, option_type: "CE" }).some((x) => x.title === "TI plan ENTRY"));
const snap = (times, t) => { let a = null; for (const x of times) if (x <= t) a = x; return a; }; const m = T.tiMarkers([0, 600, 1200, 19800 + 1000], ti, snap, 19800);
assert.deepStrictEqual(m.map((x) => x.text), ["TI paper BUY", "TI paper BUY", "TI SL"]); assert.strictEqual(m[2].color, "#f87171");
// compact mode (phone): title + Now + Edge + Cost only; tap text; legend explains every row in Marathi
const full = { dash: [["TI · CRUDEOIL", "paper only"], ["Edge", "none validated · 4 tests rejected"], ["Now", "NO TRADE"], ["Mode", "data only (no model)"], ["Cost", "Rs184/lot · BE +1.845 pts"], ["ATM", "8500 · spread 0.21% · liquid"]] };
assert.strictEqual(T.tiDashText(full, true), "TI · CRUDEOIL  paper only\nEdge  none validated · 4 tests rejected\nNow  NO TRADE\nCost  Rs184/lot · BE +1.845 pts");
assert.ok(T.tiDashText(full, false).includes("ATM") && T.tiDashText(full, false).includes("Mode"));
assert.ok(T.tiBoxText(full, true, false).endsWith("ⓘ टॅप = अर्थ")); assert.ok(T.tiBoxText(null, true, false) === "");
const ex = T.tiBoxText(full, true, true); assert.ok(ex.includes("Mode") && ex.includes("NO TRADE = ट्रेड घेऊ नका") && ex.includes("खरे order नाहीत") && ex.includes("बंद करायला"));
["Edge", "Now", "Mode", "Score", "Cost", "ATM", "Paper"].forEach((k) => assert.ok(T.TI_LEGEND.includes(k + "  ="), "legend covers " + k));
assert.strictEqual(T.tiBoxText({ error: "x" }, true, false), "TI: x");
console.log("arena_ti ok");
