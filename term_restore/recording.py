"""Session recording, so a restored tab can show its previous scrollback.

Scrollback cannot be recovered after the fact -- it lives inside the terminal
emulator's own memory, with no API to read it -- so the only way to have it
after a reboot is to record output while it happens. term-restore starts the
tabs it reopens under `script`, which records to a log here.

On the next restore the tail of that log is replayed into the tab *before*
`script` starts recording again, so replayed text never re-enters the log and
each boot's log holds only that boot's output.

These logs contain everything the terminal displayed, including anything secret
that was ever printed. The directory is created 0700 and the files 0600.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import paths

REPLAY_CAP = 256 * 1024  # bytes of scrollback carried across a reboot

# `script` writes its own banner into the log even under -q. Replaying those
# would stack one pair of "Script started/done" lines per reboot.
_BANNERS = (b"Script started on ", b"Script done on ")


def _strip_banners(data: bytes) -> bytes:
    kept = [line for line in data.splitlines(True)
            if not line.startswith(_BANNERS)]
    return b"".join(kept)


def logs_dir() -> Path:
    d = paths.state_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(str(d), 0o700)
    return d


def allocate(existing: str = "") -> Path:
    """Reuse the tab's log across reboots, or start one."""
    if existing:
        candidate = Path(existing)
        if candidate.parent == logs_dir():
            return candidate
    return logs_dir() / "{}.log".format(uuid.uuid4().hex)


def _carried(log: Path, cap: int) -> bytes:
    chunks = []
    for part in (log.with_suffix(".replay"), log):   # order matters: older first
        try:
            chunks.append(part.read_bytes())
        except OSError:
            pass
    return _strip_banners(b"".join(chunks))[-cap:]


def peek_replay(log: Path, cap: int = REPLAY_CAP) -> int:
    """How many lines a restore would replay. Touches nothing.

    Planning must not mutate: `restore --dry-run` builds the same plan, and an
    earlier version truncated the logs of live tabs just by being asked what it
    would do.
    """
    return _carried(log, cap).count(b"\n")


def prepare_replay(log: Path, cap: int = REPLAY_CAP):
    """(replay_path, line_count). Folds the last session into the replay file
    and truncates the log so this boot starts clean."""
    replay = log.with_suffix(".replay")
    data = _carried(log, cap)
    if data:
        replay.write_bytes(data)
        os.chmod(str(replay), 0o600)
    elif replay.exists():
        replay.unlink()
    try:
        log.write_bytes(b"")
        os.chmod(str(log), 0o600)
    except OSError:
        pass
    return replay, data.count(b"\n")


def prune(keep) -> int:
    """Drop logs no snapshot refers to any more."""
    keep = {str(Path(k)) for k in keep if k}
    removed = 0
    for f in logs_dir().iterdir():
        stem = f.with_suffix(".log")
        if str(stem) in keep:
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def total_size() -> int:
    return sum(f.stat().st_size for f in logs_dir().iterdir() if f.is_file())
