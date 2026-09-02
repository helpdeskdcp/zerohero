"""`python -m app.histcap [--once]` — run one capture cycle or the loop."""
import asyncio
import pprint
import sys

from .worker import CaptureWorker


def main() -> None:
    w = CaptureWorker()
    if "--once" in sys.argv or "--run-once" in sys.argv:
        pprint.pprint(w.run_once("POLL_ONCE", do_candles=True))
        return

    async def _run():
        w.start()
        print("histcap worker running; Ctrl-C to stop")
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            await w.stop()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
