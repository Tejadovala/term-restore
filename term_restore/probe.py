"""Discover the live terminal tabs by reading /proc.

Standard library only: no xprop, xdotool, wmctrl, gdbus or tmux. That rules out
window geometry beyond the pty size and rules out knowing which window a tab
sits in, because no terminal publishes it -- gnome-terminal's D-Bus lists
/screen/<uuid> and /window/N objects but never links them, and VTE does not set
WINDOWID.

/proc is scanned once per snapshot and every lookup works off that table; the
previous version re-walked /proc for each tab, which is quadratic in tab count.
"""

from __future__ import annotations

import fcntl
import os
import struct
import termios
from dataclasses import dataclass, field
from pathlib import Path

from . import paths
from .model import Snapshot, Tab
from .terminals import BACKENDS, Backend, by_name

PROC = Path("/proc")

# A tab normally runs a shell, and the command is whatever holds the shell's
# foreground process group. But a terminal can be launched with the command as
# the tab process itself (`gnome-terminal -- htop`, or a profile custom
# command), and then there is no foreground job to find.
SHELLS = frozenset({"bash", "zsh", "sh", "dash", "fish", "ksh", "mksh",
                    "tcsh", "csh", "elvish", "nu", "xonsh"})

# A tab that term-restore reopened runs under `script`, which records the
# session so the next restore can replay the scrollback. The recorder is then
# the tab process and the shell is one level down -- and it is the shell that
# holds the real cwd and foreground job, since script never leaves the
# directory it was launched in.
RECORDERS = frozenset({"script"})


@dataclass
class Proc:
    pid: int
    comm: str = ""
    ppid: int = 0
    pgrp: int = 0
    tpgid: int = 0
    starttime: int = 0
    argv: list = field(default_factory=list)

    @property
    def exe_name(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else self.comm


def parse_stat(pid: int, raw: str):
    """Parse the contents of /proc/<pid>/stat.

    comm can contain spaces and parentheses, so split on the last ')' instead
    of on whitespace.
    """
    head, _, tail = raw.rpartition(")")
    if not tail:
        return None
    fields = tail.split()
    try:
        # fields[0] is state; then ppid, pgrp, session, tty_nr, tpgid.
        return Proc(
            pid=pid,
            comm=head.partition("(")[2],
            ppid=int(fields[1]),
            pgrp=int(fields[2]),
            tpgid=int(fields[5]),
            # field 22 overall (index 19 here): start time in clock ticks.
            starttime=int(fields[19]),
        )
    except (IndexError, ValueError):
        return None


def read_cmdline(pid: int) -> list:
    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [a for a in raw.decode("utf-8", "replace").split("\0") if a]


def read_environ(pid: int) -> dict:
    try:
        raw = (PROC / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    env = {}
    for item in raw.decode("utf-8", "replace").split("\0"):
        key, sep, value = item.partition("=")
        if sep:
            env[key] = value
    return env


def read_stat(pid: int):
    try:
        return parse_stat(pid, (PROC / str(pid) / "stat").read_text())
    except OSError:
        return None


DELETED_SUFFIX = " (deleted)"


def read_cwd(pid: int):
    try:
        target = os.readlink(str(PROC / str(pid) / "cwd"))
    except OSError:
        return None
    # The kernel appends " (deleted)" once the directory is removed. Strip it
    # only when the literal path is not itself a directory, so a real directory
    # whose name happens to end that way survives.
    if target.endswith(DELETED_SUFFIX) and not os.path.isdir(target):
        return target[:-len(DELETED_SUFFIX)]
    return target


def read_tty(pid: int):
    for fd in ("0", "1", "2"):
        try:
            target = os.readlink(str(PROC / str(pid) / "fd" / fd))
        except OSError:
            continue
        if target.startswith("/dev/pts/") or target.startswith("/dev/tty"):
            return target
    return None


def winsize(tty: str):
    """(cols, rows) of a pty, or None if it cannot be read."""
    if not tty:
        return None
    try:
        fd = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
    except OSError:
        return None
    try:
        rows, cols, _, _ = struct.unpack(
            "HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        )
    except OSError:
        return None
    finally:
        os.close(fd)
    return (cols, rows) if cols and rows else None


def direct_children(pid: int) -> list:
    """Children of a process, via /proc/<pid>/task/<tid>/children.

    Far cheaper than walking all of /proc, which matters while polling during a
    restore. Every thread is checked because a multithreaded terminal can fork a
    tab from any of them.
    """
    task = PROC / str(pid) / "task"
    try:
        tids = os.listdir(str(task))
    except OSError:
        return []
    kids = []
    for tid in tids:
        try:
            raw = (task / tid / "children").read_text()
        except OSError:
            continue
        kids.extend(int(x) for x in raw.split())
    return kids


def pty_children(server_pids) -> set:
    """Tab shells under the given terminal processes, without a full scan."""
    return {pid for server in server_pids
            for pid in direct_children(server) if read_tty(pid)}


# --------------------------------------------------------------------------
# one pass over /proc
# --------------------------------------------------------------------------

@dataclass
class ProcTable:
    procs: dict = field(default_factory=dict)
    children: dict = field(default_factory=dict)
    by_pgrp: dict = field(default_factory=dict)

    @classmethod
    def scan(cls) -> "ProcTable":
        table = cls()
        try:
            entries = [e for e in os.listdir(str(PROC)) if e.isdigit()]
        except OSError:
            return table
        for entry in entries:
            pid = int(entry)
            proc = read_stat(pid)
            if proc is None:
                continue  # exited between listdir and read
            proc.argv = read_cmdline(pid)
            table.procs[pid] = proc
            table.children.setdefault(proc.ppid, []).append(pid)
            table.by_pgrp.setdefault(proc.pgrp, []).append(pid)
        return table

    def server_pids(self, backend: Backend) -> list:
        return sorted(p.pid for p in self.procs.values()
                      if p.exe_name in backend.servers)

    def tab_shells(self, backend: Backend) -> list:
        """Direct children of the terminal process that hold a pty.

        The pty filter matters for konsole and xfce4-terminal, whose process is
        also the parent of helpers that are not tabs.
        """
        shells = []
        for server in self.server_pids(backend):
            for pid in self.children.get(server, []):
                if read_tty(pid):
                    shells.append(pid)
        return sorted(shells, key=lambda p: (self.procs[p].starttime, p))

    def foreground_command(self, shell_pid: int) -> list:
        """argv of whatever holds the tab's foreground process group.

        Empty when the shell itself holds it, i.e. the tab sits at a prompt.
        Only the process-group leader is visible, so a pipeline is recorded as
        its first command.
        """
        proc = self.procs.get(shell_pid)
        if proc is None or proc.tpgid <= 0 or proc.tpgid == proc.pgrp:
            return []
        members = self.by_pgrp.get(proc.tpgid)
        if not members:
            return []
        # Lowest pid in the group is the job leader: the command the user typed.
        return read_cmdline(min(members))


def detect(table=None, prefer: str = ""):
    """(backend, tab_pids) for the terminal with the most tabs open.

    `prefer` pins a backend by name; it is used even if nothing is running, so
    a restore can target the same terminal the snapshot came from.
    """
    table = table or ProcTable.scan()
    if prefer:
        backend = by_name(prefer)
        return backend, table.tab_shells(backend)
    best = (None, [])
    for backend in BACKENDS:
        shells = table.tab_shells(backend)
        if len(shells) > len(best[1]):
            best = (backend, shells)
    return best


def log_from_argv(argv) -> str:
    """The log path out of `script [options] LOGFILE`."""
    plain = []
    skip = False
    for arg in argv[1:]:
        if skip:
            skip = False
        elif arg in ("-c", "--command", "-t", "--timing", "-B", "--log-in",
                     "-I", "--log-io", "-O", "--log-out"):
            skip = True
        elif not arg.startswith("-"):
            plain.append(arg)
    return plain[-1] if plain else ""


def resolve_shell(table: "ProcTable", tab_pid: int):
    """(shell_pid, log_path) for a tab, seeing through a `script` recorder."""
    proc = table.procs.get(tab_pid)
    if proc is None or proc.exe_name not in RECORDERS:
        return tab_pid, ""
    log = log_from_argv(proc.argv)
    for child in table.children.get(tab_pid, []):
        if read_tty(child):
            return child, log
    return tab_pid, log


def take_snapshot(prefer: str = "") -> Snapshot:
    table = ProcTable.scan()
    backend, shells = detect(table, prefer)
    if backend is None:
        return Snapshot(boot_id=paths.boot_id())

    tabs = []
    size = None
    for tab_pid in shells:
        pid, log = resolve_shell(table, tab_pid)
        cwd = read_cwd(pid)
        if cwd is None:
            continue  # shell exited mid-scan
        env = read_environ(pid)
        proc = table.procs.get(pid)
        command = table.foreground_command(pid)
        tab_is_a_shell = proc is None or proc.exe_name in SHELLS
        if not command and not tab_is_a_shell:
            command = list(proc.argv)
        tabs.append(
            Tab(
                cwd=cwd,
                screen=env.get("GNOME_TERMINAL_SCREEN"),
                command=command,
                shell=(env.get("SHELL") or "/bin/bash") if tab_is_a_shell
                      else "/bin/bash",
                pid=pid,
                log=log,
            )
        )
        if size is None:
            size = winsize(read_tty(tab_pid) or "")

    return Snapshot(
        boot_id=paths.boot_id(),
        terminal=backend.name,
        cols=size[0] if size else None,
        rows=size[1] if size else None,
        tabs=tabs,
    )
