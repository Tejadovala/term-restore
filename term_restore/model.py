"""Snapshot data model.

Tabs are a flat list: nothing available to a stdlib-only process can say which
window a given tab belongs to, so a restore rebuilds one window holding every
tab. The on-disk format is written by hand rather than via dataclasses.asdict
so an old snapshot stays readable after the dataclass gains fields.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class Tab:
    cwd: str
    screen: str | None = None
    command: list[str] = field(default_factory=list)
    shell: str = "/bin/bash"
    pid: int | None = None
    log: str = ""          # session recording, replayed as scrollback

    @property
    def command_str(self) -> str:
        return shlex.join(self.command) if self.command else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "screen": self.screen,
            "command": list(self.command),
            "shell": self.shell,
            "pid": self.pid,
            "log": self.log,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tab":
        return cls(
            cwd=d.get("cwd", ""),
            screen=d.get("screen"),
            command=list(d.get("command") or []),
            shell=d.get("shell") or "/bin/bash",
            pid=d.get("pid"),
            log=d.get("log", ""),
        )


@dataclass
class Snapshot:
    boot_id: str = ""
    saved_at: float = field(default_factory=time.time)
    terminal: str = ""
    cols: int | None = None
    rows: int | None = None
    tabs: list[Tab] = field(default_factory=list)

    @property
    def tab_count(self) -> int:
        return len(self.tabs)

    @property
    def fingerprint(self) -> tuple:
        """What makes two snapshots the same session state.

        Deliberately excludes saved_at and pids, so an idle desktop does not
        rewrite the file every tick.
        """
        return (
            self.terminal,
            self.cols,
            self.rows,
            tuple((t.cwd, tuple(t.command)) for t in self.tabs),
        )

    @property
    def geometry(self) -> str | None:
        """A --geometry argument, or None when size was never captured.

        Size only -- a position would need to be read off the X server.
        """
        if not self.cols or not self.rows:
            return None
        return f"{self.cols}x{self.rows}"

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": SCHEMA_VERSION,
                "boot_id": self.boot_id,
                "saved_at": self.saved_at,
                "terminal": self.terminal,
                "cols": self.cols,
                "rows": self.rows,
                "tabs": [t.to_dict() for t in self.tabs],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "Snapshot":
        d = json.loads(text)
        version = d.get("version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"snapshot schema v{version} is newer than this build (v{SCHEMA_VERSION})"
            )
        return cls(
            boot_id=d.get("boot_id", ""),
            saved_at=d.get("saved_at", 0.0),
            terminal=d.get("terminal", ""),
            cols=d.get("cols"),
            rows=d.get("rows"),
            tabs=[Tab.from_dict(t) for t in d.get("tabs") or []],
        )
