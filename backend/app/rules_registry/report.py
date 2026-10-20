"""Markdown report from scan() — what is enforced, what is merely shadow/dark, what lacks evidence, where parameters disagree."""
from __future__ import annotations

from collections import Counter

from .scan import ENFORCED, scan


def render(s: dict | None = None) -> str:
    s = s or scan(); R = s["rules"]
    c_mode, c_layer, c_ev = Counter(r["mode"] for r in R), Counter(r["layer"] for r in R), Counter(r["evidence"]["kind"] for r in R)
    L = ["# ZeroHero rules inventory", "", f"registry {s['registry_version']} · {len(R)} rules · drift problems: **{len(s['problems'])}** · unclassified env flags: **{len(s['unclassified_env'])}**", "",
         f"- by mode: {dict(c_mode)}", f"- by evidence: {dict(c_ev)}", f"- by layer: {dict(sorted(c_layer.items()))}", ""]
    enf = [r for r in R if r["mode"] in ENFORCED]
    unval = [r for r in enf if r["evidence"]["kind"] not in ("SAFETY_RAIL", "VALIDATED_OOS")]
    L += ["## 1. Enforced on the default paper path but NOT validated as alpha (evidence != safety rail)", "",
          "These rules change which trades are taken; none has independent out-of-sample, net-of-cost evidence. Safety rails are excluded.", "",
          "| rule | layer | mode | evidence | note |", "|---|---|---|---|---|"]
    for r in unval: L.append(f"| {r['id']} | {r['layer']} | {r['mode']} | {r['evidence']['kind']} | {(r['evidence'].get('note') or '')[:110]} |")
    L += ["", "## 2. Parameter conflicts (same concept, different values)", ""]
    if not s["conflicts"]: L.append("none")
    else:
        L += ["| concept | severity | values (rule=value unit mode) |", "|---|---|---|"]
        for c in s["conflicts"]:
            L.append(f"| {c['concept']} | {c['severity']} | " + "; ".join(f"{i['rule']}={i['value']}{(' ' + i['unit']) if i['unit'] else ''} [{i['mode']}]" for i in c["items"]) + " |")
    L += ["", "## 3. Shadow / dark / offline (computed or present, but not blocking trades)", ""]
    for r in R:
        if r["mode"] not in ENFORCED and r["mode"] != "OFF_BY_DEFAULT": L.append(f"- **{r['id']}** [{r['mode']}] {r['name']} — {(r['evidence'].get('note') or '')[:140]}")
    L += ["", "## 4. Off by default (code exists, switch off)", ""]
    for r in R:
        if r["mode"] == "OFF_BY_DEFAULT": L.append(f"- **{r['id']}** {r['name']} — switch `{(r.get('switch') or {}).get('name')}` — evidence {r['evidence']['kind']}")
    L += ["", "## 5. Safety rails (enforced, tested, no alpha claim)", ""]
    for r in R:
        if r["evidence"]["kind"] == "SAFETY_RAIL" and r["mode"] in ENFORCED: L.append(f"- {r['id']}: {r['name']}")
    L += ["", "## 6. Coverage gaps (NOT inventoried yet)", ""] + [f"- `{g['path']}` — {g['why']}" for g in s["not_yet_inventoried"]]
    L += ["", "## 7. Environment flags (read under app/)", "", "| name | category | rule | default(s) | first use |", "|---|---|---|---|---|"]
    for e in s["env"]:
        if e["category"] in ("FEATURE_FLAG", "SAFETY_SWITCH"): L.append(f"| {e['name']} | {e['category']} | {e['rule'] or ''} | {e['defaults']} | {e['where'][0]} |")
    L += ["", f"(+ {sum(1 for e in s['env'] if e['category'] not in ('FEATURE_FLAG', 'SAFETY_SWITCH'))} secret/path/tuning/provider variables, classified by pattern)", "",
          "## 8. Drift problems", ""] + ([f"- {p}" for p in s["problems"]] or ["none"])
    return "\n".join(L)
