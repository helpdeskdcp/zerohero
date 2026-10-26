"use strict";
// Same smoke harness, but with the optional exit countdown enabled (150 ms): second tap during the countdown cancels, one POST after it, no duplicates.
const test = require("node:test"), assert = require("node:assert"), { spawnSync } = require("node:child_process"), path = require("node:path");
test("terminal exit with countdown: cancel + single send", () => {
  const r = spawnSync(process.execPath, [path.join(__dirname, "arena_terminal_smoke.test.js")], { env: { ...process.env, TM_DELAY: "150" }, encoding: "utf8", timeout: 60000 });
  assert.strictEqual(r.status, 0, (r.stdout || "") + (r.stderr || ""));
});
