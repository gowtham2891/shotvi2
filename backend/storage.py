"""Where customer files live, addressed by a KEY like ``episodes/<episode id>/c03_crop.mp4`` (what the renders table
stores). Today: a folder on our server (KLIPVI_STORAGE_DIR, default <repo>/storage, not in git). Later: Cloudflare
R2 behind the same three functions, so nothing else changes."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .db import ROOT


def root() -> Path:
    return Path(os.environ.get("KLIPVI_STORAGE_DIR") or (ROOT / "storage"))


def path(key: str) -> Path:
    p = (root() / key).resolve()
    if root().resolve() not in p.parents:
        raise ValueError(f"bad storage key {key!r}")
    return p


def exists(key: str) -> bool:
    try:
        return path(key).is_file()
    except ValueError:
        return False


def put(src: Path, key: str, move: bool = True) -> str:
    dst = path(key)
    dst.parent.mkdir(parents=True, exist_ok=True)
    (shutil.move if move else shutil.copy2)(str(src), str(dst))
    return key


def size_mb(key: str) -> float:
    return round(path(key).stat().st_size / 1e6, 2)
