"""Evidence pack: for every signal of a day (every participant, the AI included) one JSON file that lets anyone re-check the leaderboard number: the ledger row (+ corrections), the source Telegram post, the captured
bid/ask snapshots around the trade, the settlement under the official rule and the descriptive variant, the rules in force and the code version, plus a MANIFEST with the sha256 of every file.
Read-only.   python scripts/arena_evidence_pack.py [--date YYYY-MM-DD]"""
import hashlib, json, os, subprocess, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from datetime import datetime, timedelta, timezone
from app.arena import api, leaderboard as LB, run as R, settle as S, submit as SU
IST = timezone(timedelta(hours=5, minutes=30)); c = api.cfg()
day = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else datetime.now(IST).strftime("%Y-%m-%d")
mdb = os.path.join(HERE, "data", "market_history.db")
sigs = R.ai_signals(os.path.join(HERE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
from app.energy_event import shadow as _ev
sigs += _ev.scoring_signals(c["start"], c["end"])
sigs = [s for s in sigs if datetime.fromtimestamp(s["t"], tz=IST).strftime("%Y-%m-%d") == day]
iso = lambda x: datetime.fromtimestamp(x, tz=IST).isoformat(timespec="seconds")
git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
rules = {}
try: rules = json.load(open(os.path.join(HERE, "data", "arena", "rule_effective.json")))
except Exception: pass
led = {r["id"]: r for r in SU.load() if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL"}
corr = {}
for r in SU.load():
    if r.get("type") in ("HUMAN_TS_CORRECTION", "HUMAN_PARTICIPANT_CORRECTION", "HUMAN_STATUS_CORRECTION"): corr.setdefault(r["signal_id"], []).append(r)
posts = {}
lp = os.path.join(HERE, "data", "arena", "channel_ingest_log.jsonl")
if os.path.exists(lp):
    for l in open(lp):
        r = json.loads(l)
        if r.get("signal_id"): posts[r["signal_id"]] = {k: r.get(k) for k in ("chat_id", "message_id", "tg_date", "received_iso", "latency_sec", "text")}
out_dir = os.path.join(HERE, "data", "arena", "evidence", day); os.makedirs(out_dir, exist_ok=True); manifest = {}
for s in sigs:
    res = {}
    for pol in ("T1", "LAST"):
        o = R.run([s], market_db=mdb, policy=pol); rr = [r for k, v in o.items() if not k.startswith("_") for r in v["results"]][0]; res[pol] = rr
    r1 = res["T1"]; end = (r1["fill_t"] + r1["hold_sec"] + 180) if r1["status"] == "SETTLED" else s["t"] + (s.get("valid_sec") or 3600) + 300
    snaps = [x for x in S.load_snaps(s["underlying"], s["expiry"], s["strike"], s["option_type"], day, mdb) if s["t"] - 300 <= x["t"] <= end]
    doc = {"signal": {**s, "stamp_ist": iso(s["t"])}, "ledger_row": led.get(s["id"]), "corrections": corr.get(s["id"], []), "source_post": posts.get(s["id"]), "settlement_official_first_target": r1, "settlement_descriptive_last_target": res["LAST"],
           "snapshots_used": [{"time_ist": iso(x["t"]), "bid": x["bid"], "ask": x["ask"], "ltp": x["ltp"]} for x in snaps],
           "rules_in_force": {"ask_in_bid_out": True, "execution_delay_sec": S.LATENCY_SEC, "sl_at_fill_effective_from_epoch": rules.get("sl_at_fill_from") or rules, "fill_window_sec": s.get("valid_sec"), "settlement": "app/arena/settle.py",
                              "costs": "app/arena/settle.py cost_points (brokerage, STT/CTT, exchange, GST, stamp)"}, "code_version_git": git, "generated_ist": iso(datetime.now(IST).timestamp())}
    path = os.path.join(out_dir, f"{s['id']}.json"); data = json.dumps(doc, indent=1, default=str); open(path, "w").write(data); manifest[f"{s['id']}.json"] = hashlib.sha256(data.encode()).hexdigest()
json.dump({"date": day, "n_signals": len(sigs), "code_version_git": git, "files_sha256": manifest}, open(os.path.join(out_dir, "MANIFEST.json"), "w"), indent=1)
print(f"{len(sigs)} signal(s) -> {out_dir}  (manifest sha256 per file)")
