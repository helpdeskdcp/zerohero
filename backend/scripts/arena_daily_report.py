"""End-of-day arena report: leaderboard (today + overall), every signal of the day with its settlement, channel posts read/claims, and what the AI side did. Writes data/arena/daily/REPORT_<date>.md and, with --send,
a short summary to the OWNER'S PRIVATE Telegram chat only. Read-only; numbers are unedited.   python scripts/arena_daily_report.py [--date YYYY-MM-DD] [--send]"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from datetime import datetime, timedelta, timezone
from app.arena import api, leaderboard as LB, run as R, submit as SU
IST = timezone(timedelta(hours=5, minutes=30)); c = api.cfg()
day = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else datetime.now(IST).strftime("%Y-%m-%d")
sigs = R.ai_signals(os.path.join(HERE, "data", "chanakya.db"), c["start"], c["end"]) + R.human_signals(start=c["start"], end=c["end"], exclude_ids=c["exclude"])
from app.energy_event import shadow as _ev
sigs += _ev.scoring_signals(c["start"], c["end"])
out = R.run(sigs, market_db=os.path.join(HERE, "data", "market_history.db")); res = {k: v["results"] for k, v in out.items() if not k.startswith("_")}
names = list(dict.fromkeys(["ZEROHERO_AI", "ZEROHERO_AI_EVENT", *c["participants"], *c["hashes"]]))
t = LB.build(res, names, c["display"], scope="today", today=day, capital=R.CAPITAL); a = LB.build(res, names, c["display"], scope="all", today=day, capital=R.CAPITAL)
f = lambda x: datetime.fromtimestamp(x, IST).strftime("%H:%M:%S") if x else "-"
lines = [f"# Arena daily report {day}", "", "Paper trading, identical settlement for everyone (ask in / bid out, 20 s delay, SL / first target, costs). Ranked by net per lot (1-lot equivalent): the quantity a participant chooses does not decide the rank. Small samples prove nothing.", ""]
for title, d in (("Today", t), ("Overall", a)):
    lines += [f"## Leaderboard: {title}", "", "| # | Participant | Net Rs per lot (rank) | Net Rs (their lots) | Settled | Not filled | Win |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['rank'] or '-'} | {r['display']} | {r['net_per_lot'] if r['net_per_lot'] is not None else '-'} | {r['net_rs'] if r['net_rs'] is not None else '-'} | {r['settled']} | {r['not_filled']} | {r['win_rate'] if r['win_rate'] is not None else '-'} |" for r in d["rows"]] + [""]
lines += [f"## Signals of {day} and their settlement", ""]
n = 0
for p, rs in sorted(res.items()):
    for r in rs:
        if LB._day(r) != day: continue
        n += 1; lines.append(f"- {p} {r['id']} {r['underlying']} {r['strike']:g} {r['type']} stamp {f(r['t'])}: {r['status']}" + (f", fill {r['fill']:.2f}@{f(r['fill_t'])}, exit {r['exit']:.2f} ({r['exit_reason']}), net Rs {r['net_rs']:.0f}" if r["status"] == "SETTLED" else ""))
if not n: lines.append("- none")
day_sigs = [x for x in sigs if datetime.fromtimestamp(x["t"], tz=IST).strftime("%Y-%m-%d") == day]
if day_sigs:
    lines += ["## Exit-rule variants (DESCRIPTIVE only, never ranked)", "",
              "Official scoring exits at the FIRST target. The variants below only show how the same signals would have ended under other exit habits (parameters chosen once when this report was written, not tuned): "
              "LAST = exit at the last target; LAST+TRAIL = last target with a 25% trailing stop from the fill (the trail event is applied after the usual delay). Same stop, same fills, same costs.", "",
              "| Participant | Official (first target) | LAST target | LAST + 25% trail |", "|---|---|---|---|"]
    def tot(sg, pol):
        o = R.run(sg, market_db=os.path.join(HERE, "data", "market_history.db"), policy=pol)
        return {k: (sum(r["net_rs"] for r in v["results"] if r["status"] == "SETTLED"), sum(1 for r in v["results"] if r["status"] == "SETTLED")) for k, v in o.items() if not k.startswith("_")}
    trail = [{**x, "events": list(x.get("events") or []) + [{"t": x["t"], "action": "TRAIL_SL", "trail_pct": 25}]} for x in day_sigs]
    v1, v2, v3 = tot(day_sigs, "T1"), tot(day_sigs, "LAST"), tot(trail, "LAST")
    for p in sorted(v1):
        cell = lambda v: f"Rs {v[p][0]:.0f} ({v[p][1]} settled)" if p in v else "-"
        lines.append(f"| {c['display'].get(p) or LB.DEFAULT_DISPLAY.get(p) or p} | {cell(v1)} | {cell(v2)} | {cell(v3)} |")
    lines += ["", "A single day is noise; these columns are about exit habits, not about who is better.", ""]
log = [json.loads(x) for x in open(os.path.join(HERE, "data", "arena", "channel_ingest_log.jsonl"))] if os.path.exists(os.path.join(HERE, "data", "arena", "channel_ingest_log.jsonl")) else []
day_log = [r for r in log if r.get("tg_date") and datetime.fromisoformat(r["tg_date"]).astimezone(IST).strftime("%Y-%m-%d") == day]            # edit/other rows carry no Telegram date
cnt = {}
for r in day_log: cnt[r["outcome"]] = cnt.get(r["outcome"], 0) + 1
lines += ["", "## Channel reader", "", f"- posts read: {len(day_log)}; by outcome: {cnt or 'none'}", "- claims ('target hit', 'gaining') are recorded, never scored.", ""]
ai_n = sum(1 for r in res.get("ZEROHERO_AI", []) if LB._day(r) == day)
lines += ["## AI side", "", f"- ZEROHERO_AI signals today: {ai_n} (frozen AUTOSCALP engine; no signal = its filters/EV gate did not allow one).", ""]
d = os.path.join(HERE, "data", "arena", "daily"); os.makedirs(d, exist_ok=True); path = os.path.join(d, f"REPORT_{day}.md"); open(path, "w").write("\n".join(lines))
print("\n".join(lines))
if "--send" in sys.argv:
    from app.connectors import telegram
    top = [f"{r['rank']}. {r['display']}: Rs {r['net_rs']}" for r in t["rows"] if r["rank"]] or ["no settled trades today"]
    msg = f"Arena report {day}\n" + "\n".join(top) + f"\nsignals today: {n}, channel posts: {len(day_log)}, AI signals: {ai_n}\nFull: data/arena/daily/REPORT_{day}.md\n(paper trading, small sample)"
    print("telegram ok:", telegram._send(msg, os.environ.get("TELEGRAM_CHAT_ID")).get("ok"))
