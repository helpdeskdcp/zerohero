"""ZeroHero multi-tenant platform foundation (G1, isolated).

Nothing here is imported by the existing application, touches chanakya.db, calls a
broker, or places an order. Importing this package has no side effects (no files,
no network). Later gates add market-data, candle and signal layers on top.
"""
import sys as _sys

if __name__ == "platform":
    # Shadow guard: if backend/app/ ever lands on sys.path (e.g. `python app/some_script.py`),
    # `import platform` would resolve HERE instead of the standard library and break numpy,
    # uvicorn, requests, ... Load the real stdlib module in our place so the name stays safe.
    import importlib.util as _ilu
    import os as _os
    import sysconfig as _sc

    _spec = _ilu.spec_from_file_location("platform", _os.path.join(_sc.get_paths()["stdlib"], "platform.py"))
    _real = _ilu.module_from_spec(_spec)
    _sys.modules["platform"] = _real
    _spec.loader.exec_module(_real)
