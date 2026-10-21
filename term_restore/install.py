"""Wiring term-restore into the GNOME session.

Two pieces, both plain files: a systemd --user service that snapshots while you
work, and an autostart entry that replays the snapshot once the desktop is up.
Nothing is appended to ~/.bashrc and no system packages are touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths

SERVICE = "term-restore.service"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "bin" / "term-restore"


def config_root() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def wanted_by() -> str:
    """graphical-session.target where the desktop reaches it (GNOME, KDE),
    default.target elsewhere (XFCE and friends never start it, so a unit
    wanted by it would simply never run)."""
    code, out = _systemctl("show", "-P", "LoadState", "graphical-session.target")
    if code == 0 and out.strip() == "loaded":
        return "graphical-session.target"
    return "default.target"


def service_unit(interval: int, target: str = "graphical-session.target") -> str:
    return f"""[Unit]
Description=term-restore: snapshot terminal tabs
PartOf={target}
After={target}

[Service]
Type=simple
ExecStart={LAUNCHER} daemon --interval {interval}
Restart=on-failure
RestartSec=5

[Install]
WantedBy={target}
"""


# Reserved in an Exec value per the Desktop Entry Specification. Note that a
# shell-style shlex.quote() is NOT valid here: it quotes with single quotes,
# which the spec itself reserves, and desktop-file-validate rejects the result.
_EXEC_RESERVED = " \t\n\"'\\><~|&;$*?#()`"


def desktop_exec_arg(arg: str) -> str:
    """Quote one Exec argument the way the Desktop Entry Specification wants.

    Quoting is with double quotes, escaping backslash, double quote, backtick
    and dollar with a preceding backslash.
    """
    if not any(c in _EXEC_RESERVED for c in arg):
        return arg
    escaped = arg
    for char in ("\\", '"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return '"' + escaped + '"'


def autostart_entry(delay: int) -> str:
    # The delay is a CLI flag rather than `sh -c "sleep N; ..."` so that Exec
    # carries no shell metacharacters, and rather than X-GNOME-Autostart-Delay,
    # which only GNOME honours -- this entry has to work on KDE, XFCE and MATE.
    exec_line = "{} restore --boot-guard --delay {}".format(
        desktop_exec_arg(str(LAUNCHER)), delay)
    return f"""[Desktop Entry]
Type=Application
Name=term-restore
Comment=Reopen the terminal tabs that were open before reboot
Exec={exec_line}
X-GNOME-Autostart-enabled=true
NoDisplay=true
Terminal=false
"""


def _systemctl(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def install(interval: int = 15, delay: int = 5) -> list[str]:
    paths.ensure_dirs()
    log = []

    target = wanted_by()
    unit = config_root() / "systemd" / "user" / SERVICE
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(service_unit(interval, target))
    log.append(f"service    {unit}  (wanted by {target})")

    entry = config_root() / "autostart" / "term-restore.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(autostart_entry(delay))
    log.append(f"autostart  {entry}")

    _systemctl("daemon-reload")
    code, err = _systemctl("enable", "--now", SERVICE)
    log.append(f"systemd    {'enabled + started' if code == 0 else 'FAILED: ' + err}")
    return log


def uninstall() -> list[str]:
    log = []
    code, err = _systemctl("disable", "--now", SERVICE)
    log.append(f"systemd    {'stopped + disabled' if code == 0 else err}")
    for path in (config_root() / "systemd" / "user" / SERVICE,
                 config_root() / "autostart" / "term-restore.desktop"):
        if path.exists():
            path.unlink()
            log.append(f"removed    {path}")
    _systemctl("daemon-reload")
    log.append(f"kept       {paths.state_dir()}")
    return log
