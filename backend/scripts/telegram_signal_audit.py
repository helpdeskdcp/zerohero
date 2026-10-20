"""Audit option signals from a Telegram export against real option bars. READ-ONLY.
usage: telegram_signal_audit.py INPUT [--format json|paste] [--policy T1|LAST] [--db PATH] [--out DIR]
  INPUT: Telegram Desktop 'Export chat history' result.json  (or a pasted-text file, --format paste)
  out DIR (default data/research/signal_audit/<input-stem>/): signals.csv, summary.json, summary.md, unparsed.txt"""
import argparse, csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.signal_audit import audit as A, parse as P, tg_export as X

ap = argparse.ArgumentParser(); ap.add_argument("input"); ap.add_argument("--format", default="json", choices=["json", "paste"]); ap.add_argument("--policy", default="T1", choices=["T1", "LAST"])
ap.add_argument("--db"); ap.add_argument("--out"); a = ap.parse_args()
msgs, meta = (X.load_export(a.input) if a.format == "json" else X.load_paste(a.input))
sigs, upds, unparsed = [], [], []
for m in msgs:
    s = P.parse_signal(m["id"], m["t"], m["date_ist"], m["text"], edited=bool(m["edited_t"]), reply_to=m["reply_to"])
    if s: sigs.append(s); continue
    u = P.parse_update(m["id"], m["t"], m["text"], m["reply_to"])
    if u: upds.append(u); continue
    if re.search(r"\b(CE|PE|CALL|PUT)\b", P.norm(m["text"])) and re.search(r"\d{3,}", m["text"]): unparsed.append(m)
links = P.link_updates(sigs, upds)
st = A.Store(a.db)
rows = [A.audit_signal(st, s, a.policy) for s in sigs]
summ = A.summarize(rows, links, {"signals_parsed": len(sigs), "updates_parsed": len(upds), "possible_signals_unparsed": len(unparsed)}, meta)
out = a.out or os.path.join("data/research/signal_audit", os.path.splitext(os.path.basename(a.input))[0]); os.makedirs(out, exist_ok=True)
cols = ["msg_id", "date", "underlying", "strike", "type", "entry_mode", "entry", "sl", "targets", "edited", "expiry", "status", "fill", "exit", "exit_reason", "gross_pts", "cost_pts", "net_pts", "net_pct",
        "mfe", "mae", "lead_min", "posted_after_t1", "pre_msg_max_high"]
with open(f"{out}/signals.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
json.dump(summ, open(f"{out}/summary.json", "w"), indent=1, default=str)
open(f"{out}/unparsed.txt", "w").write("\n\n".join(f"[{m['date_ist']}] id={m['id']}\n{m['text']}" for m in unparsed))
pf = summ.get("performance")
md = ["# Telegram signal audit", f"input: `{a.input}` · policy: {a.policy} (T1 = exit all at first target; LAST = at last target; SL full exit; EOD flat) · pessimistic fills, costs included", "",
      f"- messages {meta['messages']} · signals parsed {len(sigs)} · updates {len(upds)} · possible signals NOT parsed {len(unparsed)} (see unparsed.txt)",
      f"- id gaps (possible deleted/missing messages): {meta['id_gaps']} ({'n/a' if meta['id_gap_fraction'] is None else format(meta['id_gap_fraction'], '.1%')}) · edited: {meta['edited_messages']}",
      f"- status: {summ['by_status']} · no-data reasons: {summ['no_data_reasons']}", f"- channel's own outcome posts: {summ['channel_claims']}", ""]
if pf:
    md += ["## Result on PRICED signals (real option bars)", f"- n={pf['n']} · win rate (net>0) {pf['win_rate_net']:.1%} · mean gross {pf['mean_gross_pts']:+.2f} pts · mean NET {pf['mean_net_pts']:+.2f} pts (SE {pf['se']:.2f}, t {pf['t']:.2f})",
           f"- day-clustered: {pf['day_clustered']} · profit factor {pf['profit_factor']} · max DD (sum pts) {pf['max_drawdown_pts_sum']:.1f}",
           f"- loss share by COUNT {pf['loss_count_share']:.1%} vs by AMOUNT {pf['loss_amount_share']:.1%} · exits SL {pf['sl_exits']} / target {pf['target_exits']} / EOD {pf['eod_exits']}",
           f"- timing: posted after T1 already reached {summ['timing']['posted_after_t1']} · median lead to first target touch {summ['timing']['lead_min_median']} min · lead 5-15 min: {summ['timing']['lead_5_to_15']}",
           f"- minimum detectable effect (80% power) ~{summ['mde_80pct_pts']:.1f} pts: a smaller true edge cannot be seen with this n", ""]
else: md.append("**Too few priced signals for any statistic (UNVERIFIED).**")
md.append("\nNot a profitability claim. Survivorship caveat: deleted/unposted signals are invisible; an export only proves what is IN it.")
open(f"{out}/summary.md", "w").write("\n".join(md))
print("\n".join(md)); print("->", out)
