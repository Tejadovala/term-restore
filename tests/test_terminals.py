import shlex

import pytest

from term_restore import terminals
from term_restore.terminals import (GNOME_TERMINAL, KONSOLE, PTYXIS,
                                    XFCE4_TERMINAL)

BASH = ["/bin/bash", "--rcfile", "/tmp/boot.sh"]


def test_gnome_terminal_first_tab_opens_a_sized_window():
    argv = GNOME_TERMINAL.build_argv("/etc", first=True, geometry="100x24",
                                     command=BASH)
    assert argv == ["gnome-terminal", "--window", "--geometry=100x24",
                    "--working-directory", "/etc", "--"] + BASH


def test_gnome_terminal_later_tabs_carry_no_geometry():
    argv = GNOME_TERMINAL.build_argv("/etc", first=False, geometry="100x24",
                                     command=BASH)
    assert argv[:2] == ["gnome-terminal", "--tab"]
    assert not any(a.startswith("--geometry") for a in argv)


def test_joined_cwd_style_is_used_where_the_terminal_needs_it():
    argv = XFCE4_TERMINAL.build_argv("/var/log", first=True, geometry="",
                                     command=BASH)
    assert "--working-directory=/var/log" in argv


def test_joined_exec_style_quotes_the_command_into_one_argument():
    argv = XFCE4_TERMINAL.build_argv("/etc", first=True, geometry="",
                                     command=BASH)
    command_args = [a for a in argv if a.startswith("--command=")]
    assert len(command_args) == 1
    # It has to survive being re-split by a shell.
    assert shlex.split(command_args[0][len("--command="):]) == BASH


def test_ptyxis_uses_its_own_flag_names():
    argv = PTYXIS.build_argv("/etc", first=True, geometry="100x24", command=BASH)
    assert argv[:2] == ["ptyxis", "--new-window"]
    assert "--working-directory=/etc" in argv
    # Ptyxis has no geometry option; asking for one must not emit a bad flag.
    assert not any("100x24" in a for a in argv)


def test_konsole_opens_a_window_with_no_subcommand_flag():
    argv = KONSOLE.build_argv("/etc", first=True, geometry="", command=BASH)
    assert argv[0] == "konsole"
    assert argv[1] != "--new-tab"
    assert argv[argv.index("--workdir") + 1] == "/etc"
    assert argv[argv.index("-e") + 1:] == BASH


def test_empty_command_restores_directory_only():
    for backend in terminals.BACKENDS:
        argv = backend.build_argv("/etc", first=True, geometry="", command=[])
        assert "/bin/bash" not in argv
        assert any("/etc" in a for a in argv)


def test_every_backend_names_a_launcher_and_a_server():
    for backend in terminals.BACKENDS:
        assert backend.launcher and backend.servers
        assert backend.new_tab, "a backend must be able to open a tab"


def test_by_name_round_trips_and_rejects_junk():
    for name in terminals.names():
        assert terminals.by_name(name).name == name
    with pytest.raises(KeyError):
        terminals.by_name("xterm")


def test_only_gnome_terminal_is_marked_verified():
    """Nothing else has been exercised against a running terminal; doctor and
    --dry-run surface that, so the flag must stay honest."""
    verified = [b.name for b in terminals.BACKENDS if b.verified]
    assert verified == ["gnome-terminal"]
