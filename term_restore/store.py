"""Persisting snapshots.

Two failure modes drive the rules here. Writing an empty snapshot while the
session tears down at shutdown would destroy exactly what the tool exists to
keep, so an empty capture never overwrites a non-empty one. And the daemon ticks
every few seconds, so an unchanged desktop must not rewrite the file or rotate
the archive -- otherwise the ten archived snapshots span two minutes instead of
an hour, and the disk is written to for nothing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import paths
from .model import Snapshot

HISTORY_KEEP = 10
ARCHIVE_MIN_INTERVAL = 300.0  # seconds between archived snapshots
SHRINK_GRACE = 60.0           # how long a drop in tab count must hold


class ShrinkGuard:
    """Defers persisting a drop in tab count.

    The empty-snapshot rule is not enough on its own: at shutdown the session
    can close tabs one at a time while the daemon is still ticking, so a good
    four-tab snapshot would be overwritten by a half-torn-down one-tab snapshot
    and the reboot would restore almost nothing. Closing a tab for real survives
    the grace period; a machine on its way down does not.
    """

    def __init__(self, grace: float = SHRINK_GRACE, clock=time.monotonic):
        self.grace = grace
        self.clock = clock
        self._pending = None
        self._since = 0.0

    def should_save(self, new: Snapshot, previous) -> bool:
        if previous is None or new.tab_count >= previous.tab_count:
            self._pending = None
            return True
        if self._pending != new.fingerprint:
            self._pending = new.fingerprint
            self._since = self.clock()
            return False
        return self.clock() - self._since >= self.grace


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".{}.tmp{}".format(path.name, os.getpid()))
    tmp.write_text(text)
    os.replace(str(tmp), str(path))


def load(path=None):
    path = path or paths.snapshot_path()
    try:
        return Snapshot.from_json(path.read_text())
    except (OSError, ValueError):
        return None


def save(snapshot: Snapshot, path=None) -> bool:
    """Write the snapshot if it says something new. Returns True if written."""
    path = path or paths.snapshot_path()
    existing = load(path)

    if snapshot.tab_count == 0 and existing is not None and existing.tab_count:
        return False
    if existing is not None and existing.fingerprint == snapshot.fingerprint:
        return False

    if existing is not None and existing.tab_count:
        archive(existing)
    write_atomic(path, snapshot.to_json())
    return True


def archive(snapshot: Snapshot, force: bool = False) -> bool:
    """Keep a rollback copy, at most one per ARCHIVE_MIN_INTERVAL."""
    hist = paths.history_dir()
    hist.mkdir(parents=True, exist_ok=True)
    if not force:
        newest = max((f.stat().st_mtime for f in hist.glob("*.json")), default=0.0)
        if time.time() - newest < ARCHIVE_MIN_INTERVAL:
            return False
    write_atomic(_free_name(hist, snapshot.saved_at), snapshot.to_json())
    for stale in sorted(hist.glob("*.json"))[:-HISTORY_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass
    return True


def _free_name(hist: Path, when: float) -> Path:
    """A timestamped filename that is not already taken.

    The timestamp comes from the snapshot, so two states archived inside the
    same second would otherwise collide and the older one would be lost.
    """
    stamp = "{}.{:03d}".format(
        time.strftime("%Y%m%d-%H%M%S", time.localtime(when)),
        int(when * 1000) % 1000,
    )
    candidate = hist / "{}.json".format(stamp)
    suffix = 1
    while candidate.exists():
        candidate = hist / "{}-{}.json".format(stamp, suffix)
        suffix += 1
    return candidate


def history() -> list:
    return sorted(paths.history_dir().glob("*.json"), reverse=True)


def prune_markers(keep: str) -> int:
    """Drop restore markers from previous boots; one per reboot would pile up."""
    removed = 0
    for marker in paths.state_dir().glob("restored-*"):
        if marker.name != "restored-{}".format(keep or "unknown"):
            try:
                marker.unlink()
                removed += 1
            except OSError:
                pass
    return removed
