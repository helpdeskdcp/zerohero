"""Long-running read-only channel reader (see app/arena/tg_reader.py). Foreground: venv/bin/python scripts/arena_tg_reader.py   [--seconds N for a bounded test run]"""
import asyncio, logging, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if "=" in line and not line.startswith("#"): k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger("telethon").setLevel(logging.WARNING)
from app.arena import tg_reader
async def main():
    if "--seconds" in sys.argv:
        try: await asyncio.wait_for(tg_reader.run(), float(sys.argv[sys.argv.index("--seconds") + 1]))
        except asyncio.TimeoutError: print("bounded run finished")
    else: await tg_reader.run()
asyncio.run(main())
