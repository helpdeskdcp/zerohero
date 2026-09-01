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

// ---- frontend audit (2026-09-01) regression guards ----
const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");

// The two self-reporting endpoints are now consumed by the UI.
assert.match(source, /\/api\/autoscalp\/selfcheck/, "selfcheck must be wired");
assert.match(source, /\/api\/autoscalp\/report\?day=/, "report endpoint must be wired");
assert.match(html, /id="healthBanner"/);
assert.match(html, /id="reportDay"/);
assert.match(html, /id="reportBody"/);

// WS keepalive timer is cleared before a reconnect stacks another.
assert.match(source, /clearInterval\(wsPingTimer\)/);
assert.match(source, /wsReconnectTimer/);

// A burst of autoscalp_* events is coalesced, not one reload per event.
assert.match(source, /scheduleAutoscalpReload/);

// Data-derived values in the non-monitor views are escaped before innerHTML.
assert.ok(!/<td class="feed-dir \$\{r\.direction\}">\$\{r\.direction \|\| "—"\}/.test(source),
  "signals row must not interpolate raw r.direction");
assert.ok(!/data-id="\$\{r\.trade_id\}"/.test(source),
  "trade_id must be escaped in data attributes");
assert.match(source, /class="badge \$\{esc\(r\.decision\)\}"/);

// LIVE trading status is shown as explicit text, never a bare colour dot.
assert.match(source, /DISABLED ✓/);
assert.match(source, /ENABLED ⚠/);
// The SCALP execution-mode LIVE option is disabled in the dashboard.
assert.match(html, /<option value="LIVE" disabled>/);

// Fetch failures surface to the operator, not just console.error.
assert.match(source, /function showError/);
assert.ok(!/catch \(e\) \{ console\.error\(e\); \}/.test(source),
  "no silent console-only catch should remain");

// Stale / market-closed market data is visually distinguished.
assert.match(source, /is-closed|is-stale/);

console.log("live monitor frontend regression checks passed");
