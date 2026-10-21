"""Terminal emulator backends.

Tabs are found the same way everywhere -- the shells a terminal forks are its
direct children holding a pty -- so a backend only has to name the process that
owns those shells and describe how to build a command line that opens a window
or a tab.

Ubuntu's default terminal is gnome-terminal from 20.04 through 25.04 and Ptyxis
from 25.10; the flavours ship konsole, xfce4-terminal or mate-terminal. Only the
backend marked `verified` has been exercised against a running terminal on this
machine -- see `verified` in `doctor` output.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Backend:
    name: str
    servers: tuple = ()          # argv[0] basenames that own the tab shells
    launcher: str = ""
    new_window: tuple = ()
    new_tab: tuple = ()
    cwd_opt: str = "--working-directory"
    cwd_joined: bool = False     # --opt=VALUE instead of --opt VALUE
    geometry_opt: str = ""       # empty when the terminal cannot be sized
    exec_flag: tuple = ("--",)
    exec_joined: bool = False    # command as one quoted string after the flag
    # Flag that takes the command as ONE string, so several tabs fit in a single
    # invocation. Without it every tab needs its own call, and a bare new-tab
    # flag attaches to whatever window has focus -- which at login is not
    # reliably the window just created. Empty means batching is unavailable.
    batch_flag: tuple = ()
    verified: bool = False

    @property
    def can_batch(self) -> bool:
        return bool(self.batch_flag)

    def cwd_args(self, cwd: str) -> list:
        if self.cwd_joined:
            return ["{}={}".format(self.cwd_opt, cwd)]
        return [self.cwd_opt, cwd]

    def geometry_args(self, geometry: str) -> list:
        if not geometry or not self.geometry_opt:
            return []
        return ["{}={}".format(self.geometry_opt, geometry)]

    def exec_args(self, command: Sequence) -> list:
        if not command:
            return []
        if self.exec_joined:
            joined = shlex.join(list(command))
            if self.exec_flag[0].startswith("--"):
                return ["{}={}".format(self.exec_flag[0], joined)]
            return list(self.exec_flag) + [joined]
        return list(self.exec_flag) + list(command)

    def build_argv(self, cwd: str, first: bool, geometry: str,
                   command: Sequence) -> list:
        argv = [self.launcher]
        argv += list(self.new_window if first else self.new_tab)
        if first:
            argv += self.geometry_args(geometry)
        argv += self.cwd_args(cwd)
        argv += self.exec_args(command)
        return argv

    def build_batch_argv(self, entries, geometry: str) -> list:
        """One argv opening every tab in a single window.

        entries is [(cwd, command), ...]; the first opens the window and the
        rest become its tabs, with no dependency on which window has focus.
        """
        argv = [self.launcher]
        for i, (cwd, command) in enumerate(entries):
            argv += list(self.new_window if i == 0 else self.new_tab)
            if i == 0:
                argv += self.geometry_args(geometry)
            argv += self.cwd_args(cwd)
            if command:
                joined = " ".join(shlex.quote(a) for a in command)
                flag = self.batch_flag[0]
                argv += ["{}={}".format(flag, joined)] if flag.startswith("--") \
                    else [flag, joined]
        return argv


GNOME_TERMINAL = Backend(
    name="gnome-terminal",
    servers=("gnome-terminal-server",),
    launcher="gnome-terminal",
    new_window=("--window",),
    new_tab=("--tab",),
    geometry_opt="--geometry",
    exec_flag=("--",),
    verified=True,
    batch_flag=("-e",),
)

# GNOME's replacement for gnome-terminal; Ubuntu's default from 25.10.
PTYXIS = Backend(
    name="ptyxis",
    servers=("ptyxis-agent", "ptyxis"),
    launcher="ptyxis",
    new_window=("--new-window",),
    new_tab=("--tab",),
    cwd_joined=True,
    exec_flag=("-x",),
    exec_joined=True,
    batch_flag=("-x",),
)

MATE_TERMINAL = Backend(
    name="mate-terminal",
    servers=("mate-terminal-server", "mate-terminal"),
    launcher="mate-terminal",
    new_window=("--window",),
    new_tab=("--tab",),
    geometry_opt="--geometry",
    exec_flag=("--",),
    batch_flag=("-e",),
)

XFCE4_TERMINAL = Backend(
    name="xfce4-terminal",
    servers=("xfce4-terminal",),
    launcher="xfce4-terminal",
    new_window=("--window",),
    new_tab=("--tab",),
    cwd_joined=True,
    geometry_opt="--geometry",
    exec_flag=("--command",),
    exec_joined=True,
    batch_flag=("--command",),
)

KONSOLE = Backend(
    name="konsole",
    servers=("konsole",),
    launcher="konsole",
    new_window=(),
    new_tab=("--new-tab",),
    cwd_opt="--workdir",
    exec_flag=("-e",),
)

BACKENDS = (GNOME_TERMINAL, PTYXIS, MATE_TERMINAL, XFCE4_TERMINAL, KONSOLE)


def by_name(name: str) -> Backend:
    for backend in BACKENDS:
        if backend.name == name:
            return backend
    raise KeyError(name)


def names() -> list:
    return [b.name for b in BACKENDS]
