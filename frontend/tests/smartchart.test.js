// Dependency-free checks for the Smart Chart browser logic. Run: node frontend/tests/smartchart.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const C = require("../static/js/smartchart_core.js");

// timeframe + symbol mapping: only what the backend can serve
for (const [vela, api] of [["1", "1m"], ["3", "3m"], ["5", "5m"], ["15", "15m"], ["30", "30m"], ["60", "1h"], ["1h", "1h"], ["5m", "5m"], ["D", "1d"], ["1D", "1d"], ["1d", "1d"]]) assert.equal(C.tfToApi(vela), api, vela);
for (const bad of ["1s", "S", "240", "2h", "W", "1M", "", null, undefined, "abc", "7"]) assert.equal(C.tfToApi(bad), null, String(bad));
assert.equal(C.symbolToApi("nifty"), "NIFTY");
assert.equal(C.symbolToApi("SENSEX"), "SENSEX");
assert.equal(C.symbolToApi("RELIANCE"), "NSE:RELIANCE");
assert.equal(C.symbolToApi("NSE:TCS"), "NSE:TCS");
assert.equal(C.symbolToApi("BSE:SENSEX"), "SENSEX");

// live bar gate: duplicates, out-of-order, forming-bar updates, new bars
const g = new C.BarGate();
const bar = (t, c, v = 0) => ({ time: t, open: 10, high: 12, low: 9, close: c, volume: v });
assert.equal(g.accept(bar(1000, 10)), "new");
assert.equal(g.accept(bar(1000, 10)), "dup");
assert.equal(g.accept(bar(1000, 11)), "update");
assert.equal(g.accept(bar(940, 10)), "old");
assert.equal(g.accept(bar(1060, 11)), "new");
assert.equal(g.accept(bar(1000, 99)), "old");          // never rewinds past the newest bar
assert.equal(g.accept({ time: NaN }), "old");
assert.equal(g.accept(null), "old");
assert.equal(g.last.time, 1060);

// data-status honesty
const now = 1_700_000_000_000;
const base = { market_open: true, stale: false, live_ttl_sec: 3, tail_refreshed_ms: now - 2000, last_bar_open_ms: now - 120000 };
assert.equal(C.statusFromMeta(base, now, 300).state, "LIVE");
assert.match(C.statusFromMeta(base, now, 300).label, /polled/);                         // REST polling is never presented as a socket
assert.match(C.statusFromMeta(base, now, 300).detail, /REST poll/);
assert.equal(C.statusFromMeta({ ...base, tail_refreshed_ms: now - 60000 }, now, 300).state, "DELAYED");
assert.equal(C.statusFromMeta({ ...base, last_bar_open_ms: now - 20 * 60000 }, now, 300).state, "STALE");
assert.equal(C.statusFromMeta({ ...base, stale: true, error: "token expired" }, now, 300).state, "STALE");
assert.match(C.statusFromMeta({ ...base, stale: true, error: "token expired" }, now, 300).detail, /cached bars/);
assert.equal(C.statusFromMeta({ ...base, market_open: false }, now, 300).state, "CLOSED");
assert.equal(C.statusFromMeta(null, now, 300).state, "NONE");
assert.equal(C.statusFromMeta(base, now, 300, "network down").state, "ERROR");

// escaping + highlighting never emit raw HTML from script text
assert.equal(C.esc(`<img src=x onerror="a('b')">&`), "&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;");
const hl = C.highlight('//@version=5\nindicator("<script>alert(1)</script>") // <b>x</b>\nplot(ta.sma(close, 20))');
assert.ok(!/<script>|<b>x/.test(hl), "highlight output must be escaped");
assert.match(hl, /tk-ver/); assert.match(hl, /tk-str/); assert.match(hl, /tk-ns">ta</); assert.match(hl, /tk-num">20</); assert.match(hl, /tk-fn">plot</);
assert.equal(C.highlight("a <b> & c"), "a &lt;b&gt; &amp; c");

// number/time formatting + equity sparkline
assert.equal(C.fmtNum(null), "—"); assert.equal(C.fmtNum(NaN), "—"); assert.equal(C.fmtNum(1234.5), "1,234.50");
assert.equal(C.cls(1), "pos"); assert.equal(C.cls(-1), "neg"); assert.equal(C.cls(0), "");
const p = C.sparkPath([1, 2, 3], 100, 50);
assert.ok(p.startsWith("M") && (p.match(/L/g) || []).length === 2);
assert.equal(C.sparkPath([1], 100, 50), "");

// static wiring checks on the page + app script
const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "static", "smartchart.html"), "utf8");
const app = fs.readFileSync(path.join(root, "static", "js", "smartchart.js"), "utf8");
const index = fs.readFileSync(path.join(root, "index.html"), "utf8");
assert.match(index, /href="\/static\/smartchart\.html"[^>]*>Smart Chart</, "navigation entry");
for (const id of ["chart", "pillData", "pillRuntime", "edTa", "scList", "btRun", "ppCreate", "aiRun", "tabs"]) assert.ok(html.includes(`id="${id}"`), id);
assert.match(html, /PAPER ONLY/);
assert.match(html, /velacharts\.dev/, "Vela attribution link must stay visible on the page (Apache NOTICE)");
assert.match(html, /AGPL/); assert.match(html, /independent of TradingView/);
assert.match(app, /X-SmartChart-CSRF/, "mutations send the CSRF header");
assert.equal((app.match(/\bfetch\(/g) || []).length, 1, "exactly one network call site (the api() helper)");
assert.ok(app.includes('fetch("/api/smart-chart" + path'), "every request is confined to /api/smart-chart");
assert.ok(!/\/api\/(execution|orders|positions|scalp|autoscalp)|place_?order|kill/i.test(app), "UI has no order / execution endpoint");
assert.ok(!/WebSocket\(/.test(app), "live data is REST polling; the UI must not claim or open a socket");
assert.ok(!/eval\(|new Function|document\.write/.test(app), "no dynamic code in the UI");
// every dynamic innerHTML segment in the app goes through esc()/numeric formatters: no raw script/user field is concatenated
for (const raw of ["s.name +", "s.description +", "t.name +", "d.message +", "e.message +", "p.name +"]) {
  const re = new RegExp("[^c(]" + raw.replace(/[.+]/g, "\\$&"), "g");
  const hits = (app.match(re) || []).filter((h) => !/esc\($/.test(h));
  assert.equal(hits.length, 0, "unescaped " + raw + ": " + hits.join("|"));
}
console.log("smartchart frontend checks passed");
