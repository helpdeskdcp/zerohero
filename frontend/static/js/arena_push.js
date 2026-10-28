/* Arena order push: ONE server-sent-events stream (/api/arena/public/signals/stream) that says the moment one of the participant's own orders changes in the ledger
   (a signal or edit that came in from the Telegram channel, an amendment, a cancel). It carries no prices: a subscriber just reloads its list. The pages keep their own polling, so a dropped stream only costs speed.
   Pure helpers are exported for node tests (node tests/arena_push.test.js). */
"use strict";
function pushParse(buf) {
  const events = []; let rest = buf, i;
  while ((i = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, i); rest = rest.slice(i + 2);
    const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
    if (data) { try { events.push(JSON.parse(data)); } catch (e) { /* ignore a malformed block */ } }
  }
  return { events, rest };
}
const pushIsChange = (ev) => !!(ev && !ev.hello && ev.signal_id);
const pushBackoff = (fails) => Math.min(15000, 500 * Math.pow(2, Math.max(0, fails)));
if (typeof module !== "undefined") module.exports = { pushParse, pushIsChange, pushBackoff };
if (typeof window !== "undefined") {
  window.ArenaPush = (() => {
    const subs = new Set(); let ctl = null, timer = null, running = false, getToken = () => "";
    function fire(ev) { if (timer) return; timer = setTimeout(() => { timer = null; for (const f of subs) { try { f(ev); } catch (e) { /* a subscriber must not stop the others */ } } }, 120); }
    async function loop() {
      let fails = 0;
      while (running) {
        const tok = getToken();
        if (!tok || document.hidden) { await new Promise((r) => setTimeout(r, 1500)); continue; }
        ctl = new AbortController();
        try {
          const r = await fetch("/api/arena/public/signals/stream", { headers: { "X-Arena-Token": tok }, signal: ctl.signal, cache: "no-store" });
          if (!r.ok || !r.body) throw new Error("stream " + r.status);
          fails = 0; const rd = r.body.getReader(), dec = new TextDecoder(); let buf = "";
          for (;;) { const { done, value } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true }); const p = pushParse(buf); buf = p.rest; for (const ev of p.events) if (pushIsChange(ev)) fire(ev); }
        } catch (e) { if (!running) return; fails += 1; }
        if (!running) return; await new Promise((r) => setTimeout(r, pushBackoff(fails)));
      }
    }
    return {
      subscribe(f) { subs.add(f); return () => subs.delete(f); },
      start(tokenFn) { getToken = tokenFn || getToken; if (running) return; running = true; loop(); },
      stop() { running = false; if (ctl) ctl.abort(); },
    };
  })();
}
