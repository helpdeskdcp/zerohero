"""Daily forward-test run: verify frozen manifest -> evaluate AUTOSCALP trades opened since the epoch start -> append a hash-chained ledger line -> write LATEST.md.
usage: forward_test_run.py [--init]   (--init writes the frozen manifest ONCE; refuses to overwrite)"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.forward_test import ledger as L

D = "data/forward_test"; MAN = f"{D}/frozen_manifest.json"; LED = f"{D}/ledger.jsonl"; START = "2026-10-21"
os.makedirs(D, exist_ok=True)
if "--init" in sys.argv:
    if os.path.exists(MAN): sys.exit("manifest exists; refusing to overwrite (a rule change must create a NEW epoch via the daily run)")
    json.dump({"epoch_start_date": START, "created": L.now_iso(), "git_head": L.git_head(), "files": L.file_hashes(), "population": "ai_paper_trades strategy=AUTOSCALP opened>=epoch start"}, open(MAN, "w"), indent=1)
    print("manifest written", MAN); sys.exit(0)
man = json.load(open(MAN)); cur = L.file_hashes(); changed = [f for f, h in cur.items() if h != man["files"].get(f)]
epoch_start = man["epoch_start_date"]
if changed:                                                       # rule-bearing code changed: close the epoch, start a new one today, record it
    L.append(LED, {"type": "EPOCH_BREAK", "ts": L.now_iso(), "changed_files": changed, "old_epoch_start": epoch_start, "new_epoch_start": L.now_iso()[:10], "git_head": L.git_head()})
    man = {"epoch_start_date": L.now_iso()[:10], "created": L.now_iso(), "git_head": L.git_head(), "files": cur, "population": man["population"]}; json.dump(man, open(MAN, "w"), indent=1); epoch_start = man["epoch_start_date"]
trades = L.load_trades("data/chanakya.db", epoch_start); res = L.evaluate(trades); v2 = L.candidate_v2(trades)
try:                                                                                   # OF-1 (standalone futures order-flow candidate, pre-registered 57973ae): cumulative over all tick days; descriptive until n>=60 and days>=20
    from app.of_candidate import run as OF
    of1 = OF.run_all()["summary"]
except Exception as e:
    of1 = {"error": f"{type(e).__name__}: {e}"}
try:                                                                                   # ACTIVE arm (spec 716e898): shadow-recorded signals settled by the arena engine; cumulative
    from app.active_arm import run as ARM
    _a = ARM.run(market_db="data/market_history.db"); arm = {"selected": _a["summary"]["selected"], "days": _a["summary"]["days"], "trades_per_day": _a["summary"]["trades_per_day"], "all": _a["summary"]["all"],
                                                           "groups": _a["summary"]["groups"], "verdict": _a["summary"]["verdict"]}
except Exception as e:
    arm = {"error": f"{type(e).__name__}: {e}"}
rec = L.append(LED, {"type": "DAILY", "ts": L.now_iso(), "epoch_start": epoch_start, "git_head": L.git_head(), "result": res, "candidate_v2": v2, "candidate_of1": of1, "candidate_active_arm": arm})
ok, n, err = L.verify_chain(LED)
f = lambda x, d=2: "n/a" if x is None else f"{x:.{d}f}"
md = [f"# Forward test (frozen rules) — {rec['ts'][:19]}Z", f"epoch start {epoch_start} · ledger lines {n} · chain {'OK' if ok else 'BROKEN: ' + str(err)}",
      f"**verdict: {res['verdict']}** (claims allowed only after n>={L.MIN_N} and days>={L.MIN_DAYS} with PASS_FOR_NEXT_STAGE; PASS is never 'proven')", "",
      f"- trades n={res['n']} (skipped w/o cost model {res['skipped_no_cost_model']}) days={res['days']}"]
if res["n"]: md.append(f"- win rate {res['win_rate']:.1%} (Wilson95 {res['wilson95'][0]:.1%}-{res['wilson95'][1]:.1%}) vs break-even {f(res['breakeven_win_rate'], 3)} · mean gross {f(res['mean_gross_pct'])}% cost {f(res['mean_cost_pct'])}% NET {f(res['mean_net_pct'])}% · p {f(res['p_day_clustered_one_sided'], 3)}")
md.append(f"- CANDIDATE V2 (same trades filtered by unified policy; subset, not a separate engine): n={v2['n']} blocked={v2['blocked']} NET {f(v2.get('mean_net_pct'))}% verdict {v2['verdict']}")
md.append(f"- CANDIDATE OF-1 (order-flow, futures ticks, cumulative): " + (f"trades={of1.get('n_trades')} days={of1.get('days')} NET {f(of1.get('mean_net_bps'))} bps verdict {of1.get('verdict')}" if "error" not in of1 else of1["error"]))
md.append("- ACTIVE ARM (shadow, own spec 716e898): " + (f"selected={arm['selected']} settled={arm['all'].get('settled', 0)} net Rs {arm['all'].get('net_rs', 0):+.0f} trades/day {arm['trades_per_day']:.2f} verdict {arm['verdict']}" if "error" not in arm else arm["error"]))
open(f"{D}/LATEST.md", "w").write("\n".join(md)); print("\n".join(md))
