"""Command line for term-restore."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import install as installer
from . import paths, probe, recording, restore, store, terminals

DIM, RESET = "\033[2m", "\033[0m"


def _tilde(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def cmd_save(args) -> int:
    paths.ensure_dirs()
    snapshot = probe.take_snapshot(prefer=args.terminal)
    if snapshot.tab_count == 0:
        print("no terminal tabs found"
              + (" for " + args.terminal if args.terminal else ""))
        return 1
    if not store.save(snapshot):
        print("unchanged since the last snapshot; nothing written")
        return 0
    print("saved {} tab(s) from {} -> {}".format(
        snapshot.tab_count, snapshot.terminal, paths.snapshot_path()))
    return 0


def cmd_daemon(args) -> int:
    paths.ensure_dirs()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    guard = store.ShrinkGuard()

    def tick():
        snapshot = probe.take_snapshot(prefer=args.terminal)
        if guard.should_save(snapshot, store.load()):
            store.save(snapshot)

    while not stop.is_set():
        tick()
        stop.wait(args.interval)
    # Goes through the guard too: a stop during session teardown must not be
    # the thing that wipes the snapshot.
    tick()
    return 0


def cmd_list(args) -> int:
    if args.history:
        files = store.history()
        if not files:
            print("no archived snapshots")
            return 0
        for f in files:
            snap = store.load(f)
            print("{}  {} tab(s)".format(f, snap.tab_count if snap else "?"))
        return 0

    snapshot = store.load(Path(args.source) if args.source else None)
    if snapshot is None:
        print("no snapshot yet; run `term-restore save`", file=sys.stderr)
        return 1

    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snapshot.saved_at))
    print("{}   {} tab(s)   {}   {}".format(
        when, snapshot.tab_count, snapshot.terminal or "terminal unknown",
        snapshot.geometry or "size unknown"))
    for i, tab in enumerate(snapshot.tabs, 1):
        print("  {}. {}".format(i, _tilde(tab.cwd)))
        if tab.command:
            print("       {}was running: {}{}".format(DIM, tab.command_str, RESET))
    return 0


def cmd_restore(args) -> int:
    paths.ensure_dirs()
    if args.delay:
        # Autostart fires before the desktop has settled; opening a window too
        # early can land it on the wrong workspace or miss the session entirely.
        time.sleep(args.delay)
    boot = paths.boot_id()
    marker = paths.restore_marker(boot)
    if args.boot_guard and marker.exists():
        print("already restored since this boot; use --force to repeat")
        return 0

    snapshot = store.load(Path(args.source) if args.source else None)
    if snapshot is None or snapshot.tab_count == 0:
        print("nothing to restore", file=sys.stderr)
        return 1
    if args.boot_guard and snapshot.boot_id == boot and not args.force:
        print("snapshot is from the current boot, not a previous session; "
              "skipping (use --force to restore anyway)")
        return 0

    backend = restore.resolve_backend(snapshot, args.terminal)

    # Restoring always opens a new window; it never merges into an existing
    # one. Run it while tabs are open and you get a second copy of every tab
    # alongside the originals -- and the daemon then records the doubled state.
    # At login (--boot-guard) nothing is open yet, so the check only applies to
    # a restore invoked by hand.
    if not args.boot_guard and not args.force:
        live = probe.detect(prefer=backend.name)[1]
        if live:
            print("{} tab(s) are already open; restoring would open a second "
                  "copy of every saved tab in a new window.\n"
                  "Close them first, or pass --force to do it anyway."
                  .format(len(live)), file=sys.stderr)
            return 1

    plans, warnings = restore.build_plan(
        snapshot, prefill=not args.no_history, backend=backend,
        max_tabs=args.max_tabs, announce=not args.quiet,
        record=not args.no_record)

    if args.dry_run:
        print("terminal: {}{}".format(
            backend.name, "" if backend.verified else "  (untested backend)"))
        for plan in plans:
            note = "  (cwd missing -> $HOME)" if plan.relocated else ""
            print("tab {}  {}{}".format(plan.index + 1, _tilde(plan.cwd), note))
            if plan.command:
                print("  {}prints, does not run: {}{}".format(
                    DIM, plan.command, RESET))
            if plan.replayed:
                print("  {}replays {} lines of scrollback{}".format(
                    DIM, plan.replayed, RESET))
            print("  " + plan.shell_line())
        for warning in warnings:
            print("  warning: " + warning, file=sys.stderr)
        return 0

    opened, errors, mode = restore.execute(plans, backend)
    warnings += errors
    if opened:
        marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        store.prune_markers(boot)
    print("restored {}/{} tab(s), {} [{}]".format(
        opened, len(plans), mode, backend.name))
    for warning in warnings:
        print("  warning: " + warning, file=sys.stderr)
    return 0 if opened and not warnings else 1


def cmd_install(args) -> int:
    for line in installer.install(interval=args.interval, delay=args.delay):
        print(line)
    return 0


def cmd_uninstall(args) -> int:
    for line in installer.uninstall():
        print(line)
    return 0


def _os_name() -> str:
    try:
        fields = dict(
            line.split("=", 1) for line in
            Path("/etc/os-release").read_text().splitlines() if "=" in line)
        return fields.get("PRETTY_NAME", "?").strip('"')
    except OSError:
        return "unknown"


def _last_restore() -> str:
    proc = subprocess.run(
        ["journalctl", "--user", "-b", "--no-pager", "-o", "cat"],
        capture_output=True, text=True, errors="replace")
    hits = [l for l in proc.stdout.splitlines() if "tab(s)" in l and "restored" in l]
    return hits[-1].strip() if hits else "no restore recorded this boot"


def cmd_doctor(args) -> int:
    import os
    import shutil
    ok = True

    def check(label, good, detail):
        nonlocal ok
        ok = ok and good
        print("  [{}] {:<16} {}".format("ok" if good else "--", label, detail))

    def note(label, detail):
        print("  [--] {:<16} {}".format(label, detail))

    print("term-restore doctor")
    note("os", _os_name())
    note("desktop", "{} / {}".format(
        os.environ.get("XDG_CURRENT_DESKTOP", "?"),
        os.environ.get("XDG_SESSION_TYPE", "?")))
    check("python", sys.version_info >= (3, 8),
          "{}.{}.{}".format(*sys.version_info[:3]))
    check("script", shutil.which("script") is not None,
          shutil.which("script") or "missing -- scrollback cannot be recorded")

    table = probe.ProcTable.scan()
    running = [(b, table.tab_shells(b)) for b in terminals.BACKENDS
               if table.server_pids(b)]
    if running:
        for backend, shells in running:
            check("terminal", True, "{} -- {} tab(s){}{}".format(
                backend.name, len(shells),
                "" if backend.verified else "  BACKEND UNTESTED",
                "" if backend.can_batch else "  (cannot group tabs in one window)"))
    else:
        check("terminal", False,
              "no supported terminal running. Looked for: {}. If this machine "
              "uses a different one, term-restore cannot see its tabs."
              .format(", ".join(terminals.names())))

    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        note("wayland", "window size is not restored under Wayland")

    unit = installer.config_root() / "systemd" / "user" / installer.SERVICE
    state = subprocess.run(["systemctl", "--user", "is-active", installer.SERVICE],
                           capture_output=True, text=True).stdout.strip()
    enabled = subprocess.run(["systemctl", "--user", "is-enabled", installer.SERVICE],
                             capture_output=True, text=True).stdout.strip()
    check("snapshot daemon", state == "active",
          "{}, {} ({})".format(state or "unknown", enabled or "not enabled",
                               "unit present" if unit.exists() else "NO UNIT FILE"))
    note("wanted by", installer.wanted_by())

    autostart = installer.config_root() / "autostart" / "term-restore.desktop"
    check("autostart", autostart.exists(),
          str(autostart) if autostart.exists() else "not installed -- run `install`")

    snapshot = store.load()
    if snapshot:
        check("snapshot", snapshot.tab_count > 0, "{} tab(s) from {}, {}s old"
              .format(snapshot.tab_count, snapshot.terminal or "?",
                      int(time.time() - snapshot.saved_at)))
    else:
        check("snapshot", False, "none yet")
    note("last restore", _last_restore())
    note("archives", "{} in {}".format(len(store.history()), paths.history_dir()))
    recorded = sum(1 for t in (snapshot.tabs if snapshot else []) if t.log)
    note("scrollback", "{} tab(s) recording, {:.1f} MB".format(
        recorded, recording.total_size() / 1e6))
    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="term-restore",
        description="Reopen the terminal tabs you had before a reboot. "
                    "Restores directories only; captured commands are printed "
                    "and put on the shell history, never executed.",
    )
    parser.add_argument("--terminal", default="", choices=[""] + terminals.names(),
                        metavar="NAME",
                        help="force a terminal backend instead of detecting one ({})"
                             .format(", ".join(terminals.names())))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("save", help="snapshot the open tabs now").set_defaults(
        func=cmd_save)

    daemon = sub.add_parser("daemon", help="snapshot on a loop (systemd service)")
    daemon.add_argument("--interval", type=float, default=15.0,
                        help="seconds between snapshots (default: 15)")
    daemon.set_defaults(func=cmd_daemon)

    listing = sub.add_parser("list", help="show what the snapshot holds")
    listing.add_argument("--source", help="read this snapshot file instead")
    listing.add_argument("--history", action="store_true",
                         help="list archived snapshots")
    listing.set_defaults(func=cmd_list)

    rest = sub.add_parser("restore", help="reopen the saved tabs")
    rest.add_argument("--dry-run", action="store_true",
                      help="print the launch plan, open nothing")
    rest.add_argument("--force", action="store_true",
                      help="restore even from a same-boot snapshot, or while "
                           "tabs are already open (this duplicates them)")
    rest.add_argument("--boot-guard", action="store_true",
                      help="no-op if already restored since boot (autostart uses this)")
    rest.add_argument("--no-history", action="store_true",
                      help="print the old command but keep it off the shell history")
    rest.add_argument("--max-tabs", type=int, default=restore.MAX_TABS,
                      help="refuse to open more than this many tabs (default: %(default)s)")
    rest.add_argument("--no-record", action="store_true",
                      help="do not start restored tabs under `script`; "
                           "their scrollback will not survive the next reboot")
    rest.add_argument("--quiet", action="store_true",
                      help="skip the 'restored this tab' line in tabs that "
                           "had no command running")
    rest.add_argument("--delay", type=float, default=0.0,
                      help="wait this many seconds before restoring "
                           "(autostart uses it to let the desktop settle)")
    rest.add_argument("--source", help="restore this snapshot file instead")
    rest.set_defaults(func=cmd_restore)

    inst = sub.add_parser("install", help="enable the snapshot service and autostart")
    inst.add_argument("--interval", type=int, default=15)
    inst.add_argument("--delay", type=int, default=5,
                      help="seconds to wait after login before restoring")
    inst.set_defaults(func=cmd_install)

    sub.add_parser("uninstall", help="undo install, keep saved snapshots"
                   ).set_defaults(func=cmd_uninstall)
    sub.add_parser("doctor", help="check that everything is wired up"
                   ).set_defaults(func=cmd_doctor)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
