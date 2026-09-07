"""
Standalone runner:  python -m app.l2capture

Runs the L2 SnapQuote capture worker in its own process/event-loop -- for
capturing without the full app (tmux / a dedicated systemd unit). Honours the
same env vars; L2_CAPTURE_ENABLED is forced on for this entrypoint since running
it IS the intent. Ctrl-C stops cleanly.
"""
from __future__ import annotations

import asyncio
import os
import signal

os.environ.setdefault("L2_CAPTURE_ENABLED", "1")

from .worker import L2CaptureWorker  # noqa: E402


async def _main():
    w = L2CaptureWorker()
    w.enabled = True
    w.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except NotImplementedError:  # pragma: no cover
            pass
    print(f"[l2capture] started owner={w._owner} symbols={os.environ.get('L2_CAPTURE_SYMBOLS', 'NIFTY,CRUDEOIL,NATURALGAS')}")
    try:
        while not stop.is_set():
            await asyncio.wait([asyncio.create_task(stop.wait())], timeout=30)
            st = w.status()
            print(f"[l2capture] leader={st['is_leader']} connected={st['connected']} "
                  f"frames={st['frames_this_run']} ticks={st['ticks_this_run']} "
                  f"err={st['last_error']}")
    finally:
        await w.stop()
        print("[l2capture] stopped")


if __name__ == "__main__":
    asyncio.run(_main())
