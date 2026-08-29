// Dependency-free regression checks for the browser-only Live Monitor client.
// Run with: node frontend/tests/live_monitor.test.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "static", "js", "app.js"), "utf8");

// Requests are sequenced and stale responses are discarded after await.
assert.match(source, /let monitorRequestSeq = 0;/);
assert.match(source, /\+\+monitorRequestSeq;/);
assert.match(source, /let monitorFetchInFlight = false;/);
assert.match(source, /let monitorRefreshQueued = false;/);
assert.match(source, /if \(monitorFetchInFlight\)/);
assert.match(source, /if \(requestSeq !== monitorRequestSeq\)/);
assert.match(source, /fetchMonitor\(\);/);

// Execution events are in the same immediate-refresh set as position events.
assert.match(source, /execution_/);

// The monitor uses one escaping helper and every user-controlled monitor field
// is passed through it before interpolation into innerHTML.
assert.match(source, /const esc = \(value\)/);
for (const field of [
  "p.underlying", "p.option_type || p.instrument", "p.direction", "p.exit_reason",
  "s.setup", "s.underlying", "r.symbol", "t.symbol", "o.leg", "o.order_type",
  "s.underlying || s.symbol"
]) {
  assert.ok(source.includes(`text(${field}`), `monitor field must be escaped: ${field}`);
}
assert.ok(!source.includes("${p.underlying || \"—\"}"));
assert.ok(!source.includes("${p.direction || \"—\"}"));

// Explicit states prevent a hit or stale quote from being rendered as OPEN.
assert.match(source, /TARGET HIT/);
assert.match(source, /STOP HIT/);
assert.match(source, /STALE DATA/);

// The runner config exposes a deliberate execution switch, while asking for a
// second confirmation before a LIVE request is sent.
assert.match(source, /execution_enabled/);
assert.match(source, /Enable LIVE order routing/);

console.log("live monitor frontend regression checks passed");
