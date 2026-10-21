"""Filesystem locations used by term-restore.

Honours XDG_STATE_HOME, plus a TERM_RESTORE_STATE_DIR override so tests can run
against a throwaway tree.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "term-restore"


def _base(env: str, xdg: str, default: str) -> Path:
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser()
    root = os.environ.get(xdg) or str(Path.home() / default)
    return Path(root).expanduser() / APP


def state_dir() -> Path:
    return _base("TERM_RESTORE_STATE_DIR", "XDG_STATE_HOME", ".local/state")


def snapshot_path() -> Path:
    return state_dir() / "snapshot.json"


def history_dir() -> Path:
    return state_dir() / "history"


def boot_dir() -> Path:
    return state_dir() / "boot"


def restore_marker(boot_id: str) -> Path:
    return state_dir() / f"restored-{boot_id or 'unknown'}"


def ensure_dirs() -> None:
    for d in (state_dir(), history_dir(), boot_dir()):
        d.mkdir(parents=True, exist_ok=True)


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""
