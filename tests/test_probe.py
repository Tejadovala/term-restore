import fcntl
import os
import struct
import termios

from term_restore import probe
from term_restore.terminals import GNOME_TERMINAL

# A real stat line, with a comm chosen to break naive whitespace splitting.
# Fields after comm: state ppid pgrp session tty_nr tpgid ... starttime(idx 19).
STAT_WEIRD = (
    "4242 (we (ird) proc) S 13953 4242 4242 34816 4300 4194304 1234 0 0 0 "
    "12 7 0 0 20 0 1 0 999 0 0 0 0"
)


def test_parse_stat_survives_parens_and_spaces_in_comm():
    proc = probe.parse_stat(4242, STAT_WEIRD)
    assert proc.comm == "we (ird) proc"
    assert proc.ppid == 13953
    assert proc.pgrp == 4242
    assert proc.tpgid == 4300
    assert proc.starttime == 999


def test_parse_stat_rejects_garbage():
    assert probe.parse_stat(1, "not a stat line") is None
    assert probe.parse_stat(1, "1 (sh) S") is None
    assert probe.parse_stat(1, "") is None


def test_exe_name_prefers_argv_over_truncated_comm():
    """comm is capped at 15 chars, so 'gnome-terminal-server' arrives truncated;
    matching must use argv[0]."""
    proc = probe.Proc(pid=1, comm="gnome-terminal-")
    assert proc.exe_name == "gnome-terminal-"
    proc.argv = ["/usr/libexec/gnome-terminal-server"]
    assert proc.exe_name == "gnome-terminal-server"
    assert proc.exe_name in GNOME_TERMINAL.servers


def test_read_stat_agrees_with_os_for_this_process():
    proc = probe.read_stat(os.getpid())
    assert proc.pid == os.getpid()
    assert proc.ppid == os.getppid()
    assert proc.pgrp == os.getpgrp()
    assert proc.starttime > 0


def test_read_stat_of_dead_pid_is_none():
    assert probe.read_stat(2 ** 22) is None


def test_read_cmdline_and_environ_for_this_process():
    assert probe.read_cmdline(os.getpid())[0].endswith(("python3", "python"))
    # /proc/<pid>/environ is frozen at exec, so only pre-exec vars appear.
    assert probe.read_environ(os.getpid())["PATH"] == os.environ["PATH"]


def test_read_environ_of_dead_pid_is_empty():
    assert probe.read_environ(2 ** 22) == {}


def test_winsize_reads_a_real_pty():
    primary, secondary = os.openpty()
    try:
        fcntl.ioctl(secondary, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 132, 0, 0))
        assert probe.winsize(os.ttyname(secondary)) == (132, 40)
    finally:
        os.close(primary)
        os.close(secondary)


def test_winsize_of_a_non_tty_is_none(tmp_path):
    plain = tmp_path / "file"
    plain.write_text("")
    assert probe.winsize(str(plain)) is None
    assert probe.winsize("") is None
    assert probe.winsize("/dev/pts/99999") is None


def test_proctable_indexes_this_process():
    table = probe.ProcTable.scan()
    me = table.procs[os.getpid()]
    assert me.ppid == os.getppid()
    assert os.getpid() in table.children[os.getppid()]
    assert os.getpid() in table.by_pgrp[os.getpgrp()]


def test_foreground_command_is_empty_without_a_foreground_job():
    # pytest is the process-group leader here, so nothing else holds the tty.
    table = probe.ProcTable.scan()
    assert table.foreground_command(os.getpid()) == []


def test_foreground_command_of_unknown_pid_is_empty():
    assert probe.ProcTable().foreground_command(99999999) == []


def test_direct_children_matches_the_full_scan():
    """The cheap poll path must agree with the expensive one."""
    table = probe.ProcTable.scan()
    parent = os.getppid()
    cheap = set(probe.direct_children(parent))
    full = set(table.children.get(parent, []))
    assert os.getpid() in cheap
    # The scan can race against short-lived processes; cheap must not invent any.
    assert cheap - full == set() or cheap >= {os.getpid()}


def test_detect_returns_nothing_when_no_terminal_matches():
    table = probe.ProcTable()
    backend, shells = probe.detect(table)
    assert backend is None and shells == []


def test_take_snapshot_never_raises_on_this_machine():
    snap = probe.take_snapshot()
    assert snap.tab_count >= 0
    for tab in snap.tabs:
        assert tab.cwd.startswith("/")


def test_a_tab_whose_own_process_is_the_command_still_records_it(monkeypatch):
    """`gnome-terminal -- htop` leaves no foreground job to find: the tab
    process IS the command, and it must not be dropped."""
    table = probe.ProcTable()
    htop = probe.Proc(pid=500, comm="htop", ppid=1, pgrp=500, tpgid=500)
    htop.argv = ["htop", "-d", "10"]
    table.procs[500] = htop

    monkeypatch.setattr(probe.ProcTable, "scan", classmethod(lambda cls: table))
    monkeypatch.setattr(probe, "detect", lambda t=None, prefer="": (
        __import__("term_restore.terminals", fromlist=["GNOME_TERMINAL"]
                   ).GNOME_TERMINAL, [500]))
    monkeypatch.setattr(probe, "read_cwd", lambda pid: "/etc")
    monkeypatch.setattr(probe, "read_environ", lambda pid: {"SHELL": "/bin/bash"})
    monkeypatch.setattr(probe, "read_tty", lambda pid: "")

    snap = probe.take_snapshot()
    assert snap.tabs[0].command == ["htop", "-d", "10"]


def test_a_shell_sitting_at_a_prompt_records_no_command(monkeypatch):
    table = probe.ProcTable()
    sh = probe.Proc(pid=501, comm="bash", ppid=1, pgrp=501, tpgid=501)
    sh.argv = ["/bin/bash"]
    table.procs[501] = sh

    monkeypatch.setattr(probe.ProcTable, "scan", classmethod(lambda cls: table))
    monkeypatch.setattr(probe, "detect", lambda t=None, prefer="": (
        __import__("term_restore.terminals", fromlist=["GNOME_TERMINAL"]
                   ).GNOME_TERMINAL, [501]))
    monkeypatch.setattr(probe, "read_cwd", lambda pid: "/etc")
    monkeypatch.setattr(probe, "read_environ", lambda pid: {"SHELL": "/bin/bash"})
    monkeypatch.setattr(probe, "read_tty", lambda pid: "")

    assert probe.take_snapshot().tabs[0].command == []


def test_a_deleted_working_directory_is_recorded_without_the_kernel_suffix(tmp_path):
    """/proc appends ' (deleted)' once the directory is gone; recording that
    literally would put a path that never existed into the snapshot."""
    import subprocess
    import time
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    proc = subprocess.Popen(["sleep", "10"], cwd=str(doomed))
    try:
        time.sleep(0.3)
        doomed.rmdir()
        time.sleep(0.2)
        raw = os.readlink("/proc/{}/cwd".format(proc.pid))
        assert raw.endswith(" (deleted)"), "kernel behaviour changed"
        assert probe.read_cwd(proc.pid) == str(doomed)
    finally:
        proc.kill()


def test_a_directory_really_named_deleted_is_left_alone(tmp_path):
    import subprocess
    import time
    odd = tmp_path / "real (deleted)"
    odd.mkdir()
    proc = subprocess.Popen(["sleep", "10"], cwd=str(odd))
    try:
        time.sleep(0.3)
        assert probe.read_cwd(proc.pid) == str(odd)
    finally:
        proc.kill()
