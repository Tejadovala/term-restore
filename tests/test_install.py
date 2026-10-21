import configparser
import shutil
import subprocess

import pytest

from term_restore import install


def test_service_unit_points_at_the_launcher():
    unit = install.service_unit(interval=30)
    cfg = configparser.ConfigParser()
    cfg.read_string(unit)
    exec_start = cfg["Service"]["ExecStart"]
    assert exec_start.endswith("daemon --interval 30")
    assert install.LAUNCHER.exists(), "ExecStart would point at a missing file"
    assert exec_start.startswith(str(install.LAUNCHER))
    assert cfg["Install"]["WantedBy"] == "graphical-session.target"


def test_service_unit_target_is_configurable():
    """XFCE and friends never reach graphical-session.target, so a unit wanted
    by it would never start."""
    cfg = configparser.ConfigParser()
    cfg.read_string(install.service_unit(interval=15, target="default.target"))
    assert cfg["Install"]["WantedBy"] == "default.target"
    assert cfg["Unit"]["PartOf"] == "default.target"


def test_wanted_by_picks_a_real_target():
    assert install.wanted_by() in ("graphical-session.target", "default.target")


def test_autostart_entry_is_a_valid_desktop_file():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_string(install.autostart_entry(delay=7))
    entry = cfg["Desktop Entry"]
    assert entry["Type"] == "Application"
    # --boot-guard is what stops a second restore piling tabs on the first.
    assert entry["Exec"].endswith("restore --boot-guard --delay 7")
    # The delay must not rely on X-GNOME-Autostart-Delay, which only GNOME reads.
    assert "X-GNOME-Autostart-Delay" not in install.autostart_entry(7)


def test_exec_key_carries_no_shell_metacharacters():
    """A .desktop Exec is not a shell command line. An earlier version used
    `sh -c 'sleep N; ...'`, which desktop-file-validate rejects: the spec
    reserves both ';' and the single quote."""
    exec_line = install.autostart_entry(5).split("Exec=")[1].splitlines()[0]
    assert ";" not in exec_line
    assert "'" not in exec_line
    assert "sh -c" not in exec_line


def test_desktop_exec_arg_leaves_ordinary_paths_bare():
    assert install.desktop_exec_arg("/home/teja/bin/x") == "/home/teja/bin/x"


def test_desktop_exec_arg_quotes_reserved_characters_with_double_quotes():
    for path, expected in [
        ("/home/a b/x", '"/home/a b/x"'),
        ("/home/$USER/x", '"/home/\\$USER/x"'),
        ('/home/we"ird/x', '"/home/we\\"ird/x"'),
        ("/home/ba`ck/x", '"/home/ba\\`ck/x"'),
    ]:
        assert install.desktop_exec_arg(path) == expected


def test_desktop_entry_passes_the_real_spec_validator(tmp_path):
    """desktop-file-validate is the authority; skip where it is absent."""
    if shutil.which("desktop-file-validate") is None:
        pytest.skip("desktop-file-validate not installed")
    for path in ("/home/teja/bin/term-restore", "/home/a b/term restore/x",
                 "/home/$USER/x", '/home/we"ird/x', "/home/ba`ck/x"):
        entry = install.autostart_entry(5).replace(
            install.desktop_exec_arg(str(install.LAUNCHER)),
            install.desktop_exec_arg(path))
        f = tmp_path / "e.desktop"
        f.write_text(entry)
        result = subprocess.run(["desktop-file-validate", str(f)],
                                capture_output=True, text=True)
        assert result.returncode == 0 and not result.stdout, \
            "{} -> {}".format(path, result.stdout)


def test_launcher_is_executable():
    import os
    assert os.access(install.LAUNCHER, os.X_OK)
