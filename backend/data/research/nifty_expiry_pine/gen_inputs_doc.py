"""Generates NXI_INPUTS.md from the Pine source so the input documentation can never drift from the code."""
import re
src = open("nifty_expiry_intelligence.pine", encoding="utf-8").read()
groups = dict(re.findall(r'^(G\d+)\s*=\s*"([^"]+)"', src, re.M))
def split_args(body):
    out, buf, d, q = [], "", 0, None
    for ch in body:
        if q:
            buf += ch
            if ch == q: q = None
        elif ch in "\"'": q = ch; buf += ch
        elif ch in "([{": d += 1; buf += ch
        elif ch in ")]}": d -= 1; buf += ch
        elif ch == "," and d == 0: out.append(buf.strip()); buf = ""
        else: buf += ch
    out.append(buf.strip()); return out

rows = []
for m in re.finditer(r'^(\w+)\s*=\s*input\.(\w+)\((.*?)\)\s*(?=\n\S|\n\n|\Z)', src, re.M | re.S):
    name, typ, body = m.groups(); args = split_args(body)
    pos = [a for a in args if not re.match(r"^\w+\s*=(?!=)", a)]; named = {k: v for k, v in (re.match(r"^(\w+)\s*=\s*(.*)$", a, re.S).groups() for a in args if re.match(r"^\w+\s*=(?!=)", a))}
    default = pos[0] if pos else named.get("defval", ""); title = (pos[1] if len(pos) > 1 else named.get("title", "")).strip('"')
    g = named.get("group", ""); tip = named.get("tooltip", "").strip().strip('"'); opts = named.get("options", "").strip("[]").replace('"', "")
    rows.append((g, name, typ, default.replace("|", "/"), title, opts, tip))
out = ["# NXI input reference (generated from the source - do not edit by hand)", "", f"{len(rows)} inputs in {len(groups)} groups. Inputs are grouped exactly as in the TradingView settings dialog.", ""]
cur = None
for g, name, typ, default, title, opts, tip in rows:
    if g != cur:
        cur = g; out += ["", f"## {groups.get(g, g)}", "", "| input | type | default | options | notes |", "|---|---|---|---|---|"]
    out.append(f"| {title} (`{name}`) | {typ} | `{default}` | {opts} | {tip.replace('|','/')} |")
open("NXI_INPUTS.md", "w", encoding="utf-8").write("\n".join(out) + "\n")
print(len(rows), "inputs documented")
