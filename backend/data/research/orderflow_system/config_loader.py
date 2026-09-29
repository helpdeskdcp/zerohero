"""Deep-merges a versioned config (e.g. config/v1.yaml) over config/base.yaml
-- base.yaml is the full, exhaustive parameter set; a version file overrides
only what it explicitly changes. Every value used anywhere in this project
must trace back to one of these two files -- nothing hard-coded downstream."""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(version_path: str | Path) -> dict:
    base = yaml.safe_load((HERE / "config" / "base.yaml").read_text())
    version_path = Path(version_path)
    if not version_path.is_absolute():
        version_path = HERE / version_path
    override = yaml.safe_load(version_path.read_text()) or {}
    return _deep_merge(base, override)


def resolve_db_path(cfg: dict, key: str) -> str:
    """config paths are relative to config_loader.py's own directory
    (HERE), not the caller's cwd -- so `python main.py` works the same
    whether invoked from this directory or anywhere else."""
    return str((HERE / cfg["data"][key]).resolve())
