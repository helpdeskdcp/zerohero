#!/usr/bin/env python3
"""Generate app/smart_chart/capabilities_upstream.json from a PineTS checkout's docs/api-coverage/*.md.

usage: gen_pine_capabilities.py <path-to-PineTS-checkout>
Run only against a checkout of the PINNED PineTS version (see app/smart_chart/package.json). The output records the
upstream commit and version so the registry can never silently claim more than the pinned runtime documents.
Status icons: ✅ implemented+tested upstream, ✔️ implemented but needs testing, ❌/blank = not listed as implemented.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
cov = root / "docs" / "api-coverage"
row = re.compile(r"^\|\s*`([^`|]+)`\s*\|\s*([^|]*?)\s*\|")
fns: dict[str, str] = {}
for md in sorted(cov.glob("*.md")):
    for line in md.read_text(encoding="utf-8").splitlines():
        m = row.match(line)
        if not m:
            continue
        name = re.sub(r"\(\)\s*$", "", m.group(1).strip())
        icon = m.group(2).strip()
        status = "implemented_tested" if icon.startswith("✅") else "implemented_untested" if icon.startswith("✔") else "not_listed"
        if name not in fns or status != "not_listed":
            fns[name] = status
pkg = json.loads((root / "package.json").read_text())
commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
out = {"pinets_version": pkg["version"], "pinets_commit": commit, "functions": dict(sorted(fns.items()))}
dest = Path(__file__).resolve().parents[1] / "app" / "smart_chart" / "capabilities_upstream.json"
dest.write_text(json.dumps(out, indent=1) + "\n")
print(f"{len(fns)} entries ({sum(v == 'implemented_tested' for v in fns.values())} tested, {sum(v == 'implemented_untested' for v in fns.values())} untested, "
      f"{sum(v == 'not_listed' for v in fns.values())} not listed) -> {dest}")
