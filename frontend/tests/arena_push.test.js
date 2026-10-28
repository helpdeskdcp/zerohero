/* Run: node tests/arena_push.test.js */
const assert = require("assert");
const P = require("../static/js/arena_push.js");
let r = P.pushParse('data: {"hello":true}\n\ndata: {"signal_id":"H1","type":"HUMAN_SIGNAL"}\n\n: keep-alive\n\ndata: {"sig');
assert.strictEqual(r.events.length, 2); assert.strictEqual(r.rest, 'data: {"sig');                      // keep-alive comments are skipped, the unfinished tail is kept
assert.deepStrictEqual(r.events.map(P.pushIsChange), [false, true]);                                       // the greeting is not a change
assert.strictEqual(P.pushParse("data: {not json}\n\n").events.length, 0);                                  // a malformed block is ignored
assert.deepStrictEqual([0, 1, 2, 10].map(P.pushBackoff), [500, 1000, 2000, 15000]);
console.log("arena_push ok");
