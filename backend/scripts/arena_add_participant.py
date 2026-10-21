"""Add an arena participant at runtime (no restart): own token, own signals, own standings. The token is stored only as a sha256 hash in data/arena/participants.json; the plain token goes to
data/arena/ACCESS_<NAME>.txt (chmod 600, gitignored) for you to hand over.   python scripts/arena_add_participant.py NAME "Display Name" """
import hashlib, json, os, re, secrets, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
name, display = (sys.argv[1] if len(sys.argv) > 1 else "").upper(), (sys.argv[2] if len(sys.argv) > 2 else "")
if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,40}", name) or not display: sys.exit("usage: arena_add_participant.py NAME \"Display Name\"")
if name in ("ZEROHERO_AI", "ZEROHERO_AI_ACTIVE", "ZEROHERO_AI_EVENT"): sys.exit("reserved name")
P = os.path.join(HERE, "data", "arena", "participants.json"); d = json.load(open(P)) if os.path.exists(P) else {}
if d.get(name, {}).get("token_sha256"): sys.exit(f"{name} already has a token (delete its entry first to re-issue)")
tok = secrets.token_urlsafe(32); d[name] = {**d.get(name, {}), "display": display, "token_sha256": hashlib.sha256(tok.encode()).hexdigest()}
json.dump(d, open(P, "w"), indent=1); os.chmod(P, 0o600)
A = os.path.join(HERE, "data", "arena", f"ACCESS_{name}.txt")
open(A, "w").write(f"ZeroHero Arena - participant {display} ({name})\nSeparate profile: your signals and results are never mixed with another participant's.\nPage: /static/arena.html   Leaderboard: /static/leaderboard.html\nToken (send as header X-Arena-Token / enter on the page): {tok}\n"); os.chmod(A, 0o600)
print(f"added {name} ({display}); access file: {A}")
