#!/usr/bin/env python3
"""Reboot test for term-restore.

    ./verify-reboot.py arm    before rebooting: record what is open now
    ./verify-reboot.py        after rebooting: check what came back

Delete this file once the test is done; it is a harness, not part of the tool.
"""

import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SELF = str(Path(__file__).resolve())
sys.path.insert(0, str(ROOT))

from term_restore import paths, probe, store  # noqa: E402
from term_restore.model import Snapshot  # noqa: E402

EXPECT = paths.state_dir() / "reboot-test.json"
GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def mark(ok):
    return "{}PASS{}".format(GREEN, RESET) if ok else "{}FAIL{}".format(RED, RESET)


def arm():
    paths.ensure_dirs()
    snapshot = probe.take_snapshot()
    if snapshot.tab_count == 0:
        sys.exit("no tabs found; nothing to arm")
    store.save(snapshot)
    EXPECT.write_text(snapshot.to_json())
    print("armed with {} tab(s), boot_id {}".format(
        snapshot.tab_count, snapshot.boot_id[:8]))
    for i, tab in enumerate(snapshot.tabs, 1):
        extra = ("{}   was running: {}{}".format(DIM, tab.command_str, RESET)
                 if tab.command else "")
        print("  {}. {}{}".format(i, tab.cwd, extra))
    print("\nNow reboot. Afterwards run:  {}".format(SELF))


def _terminal_windows() -> int:
    """Count gnome-terminal windows via xprop. The tool itself never does this
    -- it is deliberately stdlib-only -- but a test harness may."""
    try:
        roots = subprocess.run(["xprop", "-root", "_NET_CLIENT_LIST"],
                               capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return -1
    ids = roots.partition("#")[2].replace(" ", "").split(",")
    count = 0
    for wid in [i for i in ids if i.startswith("0x")]:
        cls = subprocess.run(["xprop", "-id", wid, "WM_CLASS"],
                             capture_output=True, text=True).stdout
        if "gnome-terminal" in cls:
            count += 1
    return count


def journal_result():
    """What the autostart entry reported this boot.

    This is the authoritative check: it survives you closing tabs afterwards,
    which a live tab count does not.
    """
    out = subprocess.run(
        ["journalctl", "--user", "-b", "--no-pager", "-o", "cat"],
        capture_output=True, text=True, errors="replace").stdout
    hits = [l for l in out.splitlines() if "restored" in l and "tab(s)" in l]
    return hits[-1] if hits else ""


def check():
    if not EXPECT.exists():
        sys.exit("not armed; run `{} arm` before rebooting".format(SELF))
    expected = Snapshot.from_json(EXPECT.read_text())
    boot = paths.boot_id()

    print("term-restore reboot test\n")
    print("  the checks that matter:")

    rebooted = boot != expected.boot_id
    print("    {}  rebooted          armed under {}, now {}".format(
        mark(rebooted), expected.boot_id[:8], boot[:8]))
    if not rebooted:
        print("\n  Same boot as when armed -- you have not rebooted yet.")
        return 1

    marker = paths.restore_marker(boot)
    print("    {}  autostart fired   {}".format(
        mark(marker.exists()),
        marker.name if marker.exists()
        else "no marker: the .desktop entry never ran"))

    line = journal_result()
    want = expected.tab_count
    opened = 0
    if line:
        try:
            opened = int(line.split("restored")[1].split("/")[0])
        except (IndexError, ValueError):
            pass
    full = opened >= want
    print("    {}  tabs reopened     {}".format(
        mark(full), line.strip() if line else "nothing in the journal"))

    # The journal line is the authority on grouping: the tool itself cannot see
    # which window a tab is in, so it reports which launch path it took.
    grouped = "one window" in line
    print("    {}  one window        {}".format(
        mark(grouped),
        "launched as a single invocation" if grouped
        else "opened one tab at a time -- they are in separate windows"))
    print("    {}note{}  gnome-terminal windows open now: {}".format(
        DIM, RESET, _terminal_windows()))

    # Informational only -- tabs you closed since login are not failures.
    live = probe.take_snapshot()
    got = Counter(t.cwd for t in live.tabs)
    print("\n  {}still open now (informational -- closing tabs is not a failure):{}"
          .format(DIM, RESET))
    for cwd, n in sorted(Counter(t.cwd for t in expected.tabs).items()):
        here = got.get(cwd, 0)
        print("    {}  {}{}".format("yes " if here >= n else "no  ", cwd,
                                    "" if here >= n else "   (closed since)"))

    print()
    subprocess.run([str(ROOT / "bin" / "term-restore"), "doctor"])
    passed = rebooted and marker.exists() and full and grouped
    print("\n  {}".format("ALL CHECKS PASSED" if passed else "SOME CHECKS FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "arm":
        arm()
    else:
        sys.exit(check())
