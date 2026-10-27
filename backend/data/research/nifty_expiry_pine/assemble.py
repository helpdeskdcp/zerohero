"""Assembles part*.pine into nifty_expiry_intelligence.pine and rewrites 'a := 1, b := 2' style lines into one statement per line (Pine allows only one per line)."""
import glob, re, sys

def split_top(line):
    ind = re.match(r"\s*", line).group(0); s = line.strip(); out, buf, d, q = [], "", 0, None
    i = 0
    while i < len(s):
        ch = s[i]
        if q:
            buf += ch
            if ch == "\\": buf += s[i + 1]; i += 1
            elif ch == q: q = None
        elif ch in "\"'": q = ch; buf += ch
        elif ch == "/" and s[i:i + 2] == "//": buf += s[i:]; break
        elif ch in "([{": d += 1; buf += ch
        elif ch in ")]}": d -= 1; buf += ch
        elif ch == "," and d == 0: out.append(buf.strip()); buf = ""
        else: buf += ch
        i += 1
    out.append(buf.strip())
    return [ind + o for o in out if o]

def fix(text):
    res = []
    for line in text.split("\n"):
        st = line.strip()
        if not st or st.startswith("//") or st.startswith("[") or re.match(r"^(if|else|for|while|switch|f_|\w+\s*\()", st) and ":=" not in st and "= " not in st and not st.startswith("var"):
            res.append(line); continue
        parts = split_top(line)
        # only split when every piece looks like a statement (assignment / declaration / call)
        if len(parts) > 1 and all(re.match(r"^\s*(var\s+|varip\s+)?([A-Za-z_][\w<>]*\s+)?[A-Za-z_][\w\.]*\s*(:=|=|\+=|-=)|^\s*[A-Za-z_][\w\.]*\(", p) for p in parts):
            res.extend(parts)
        else:
            res.append(line)
    return "\n".join(res)

if __name__ == "__main__":
    files = sorted(glob.glob("part*.pine")); src = "\n".join(open(f, encoding="utf-8").read() for f in files)
    open("nifty_expiry_intelligence.pine", "w", encoding="utf-8").write(fix(src))
    print("assembled", files)
