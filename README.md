# term-restore

Reopens the terminal tabs you had open before a reboot, on Ubuntu.

A background service snapshots your open tabs every few seconds. After you log
back in, an autostart entry reopens them in their old working directories.

**Nothing you were running is re-executed.** Each restored tab prints the
command that had been running there and puts it on the shell history, so one
press of Up re-runs it — your keystroke, not the tool's. A snapshot catches
commands mid-flight, and replaying a half-finished `rm -rf` or `git push` is not
something you can undo.

**Scrollback is restored for tabs term-restore opened.** It starts those tabs
under `script`, which records the session; the next restore replays the tail
back into the tab. A tab you opened yourself is not recorded, so its scrollback
is gone after a reboot — it only has history from the first time term-restore
reopened it. See [Scrollback](#scrollback) for the privacy cost.

Standard library only: no `xdotool`, `wmctrl`, `xprop`, `tmux`, or pip packages,
and nothing is appended to your `~/.bashrc`.

## Supported

| | |
| --- | --- |
| Ubuntu | 20.04 → 25.10 (Python 3.8+, tested on 3.8 / 3.10 / 3.12) |
| Terminals | gnome-terminal, ptyxis, mate-terminal, xfce4-terminal, konsole |
| Session | X11 and Wayland (window *size* is not restored under Wayland) |
| Desktops | GNOME, KDE, XFCE, MATE — autostart and the systemd target adapt |

The terminal is detected at runtime, recorded in the snapshot, and reused on
restore; `--terminal NAME` forces one. Only the **gnome-terminal** backend has
been exercised against a running terminal — `doctor` and `restore --dry-run`
label the others as untested, since their flags come from documentation rather
than from a machine I could try them on.

## Setup

Nothing to install — it runs from the checkout:

```bash
./bin/term-restore doctor
```

To enable it permanently (writes a systemd user unit and an autostart entry):

```bash
./bin/term-restore install
```

That starts a snapshot daemon now and restores tabs on every future login.
`./bin/term-restore uninstall` removes both and keeps your snapshots.

## Run

```bash
./bin/term-restore save               # snapshot the open tabs right now
./bin/term-restore list               # show what the snapshot holds
./bin/term-restore restore --dry-run  # print the launch plan, open nothing
./bin/term-restore restore            # reopen the tabs
./bin/term-restore doctor             # check the wiring
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `restore --no-history` | print the old command but keep it off the shell history |
| `restore --force` | restore even from a snapshot taken during this boot |
| `restore --source FILE` | restore a specific snapshot file |
| `list --history` | list the last 10 archived snapshots |
| `restore --max-tabs N` | refuse to open more than N tabs (default 50) |
| `--terminal NAME` | force a backend instead of detecting one |
| `install --interval 30` | snapshot every 30s instead of 15 |
| `install --delay 10` | wait 10s after login before restoring |

## Test

```bash
uv venv && uv pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Key files

| Path | Role |
| --- | --- |
| `bin/term-restore` | launcher; runs the package straight from the checkout |
| `term_restore/terminals.py` | per-terminal process names and command lines |
| `term_restore/probe.py` | finds live tabs by reading `/proc` |
| `term_restore/model.py` | snapshot dataclasses and the on-disk JSON format |
| `term_restore/store.py` | atomic writes, the empty-snapshot guard, archiving |
| `term_restore/restore.py` | builds the `gnome-terminal` command lines and bootstraps |
| `term_restore/install.py` | systemd unit and autostart entry |
| `term_restore/cli.py` | subcommands |

State lives in `~/.local/state/term-restore/`: `snapshot.json`, `history/`
(last 10), and `boot/` (the per-tab bootstrap scripts, rewritten each restore).

## How it works

GNOME Terminal has no session-save API, so tabs are found indirectly:

- Every tab's shell is a direct child of the terminal's process
  (`gnome-terminal-server`, `ptyxis-agent`, `konsole`, …) and holds a pty. One
  pass over `/proc` enumerates them; `/proc/<pid>/cwd` gives each tab's
  directory.
- The command running in a tab is found from `tpgid` in `/proc/<pid>/stat` —
  the foreground process group of the tab's pty. When it equals the shell's own
  process group, the tab is sitting at a prompt.
- Window size comes from a `TIOCGWINSZ` ioctl on the tab's pty.
- Restore opens every tab in **one** invocation, so they land in a single
  window. Each tab's command lives in a generated `launch-N.sh`, which lets the
  whole thing be passed as one string per tab. Backends that cannot batch
  (konsole) fall back to one invocation per tab, as does a batch that fails.
  While waiting for tabs to appear it polls `/proc/<pid>/task/<tid>/children`
  rather than rescanning `/proc`.

Each tab is started as `bash --rcfile <bootstrap>`. The bootstrap sources your
`~/.bashrc`, prints the old command, and seeds it into the history list.

## Limitations

- **All tabs come back in one window.** Nothing available to a stdlib-only
  process maps a tab to its window: gnome-terminal's D-Bus exposes
  `/screen/<uuid>` and `/window/N` objects but never links them, and VTE does
  not set `WINDOWID`. Reading that association needs an X query tool, which this
  build deliberately avoids.
- **Window position is not restored**, for the same reason. Size (`COLSxROWS`)
  is, from the pty — except under Wayland, and on ptyxis and konsole, which have
  no geometry option.
- **Backends other than gnome-terminal are untested.** Their flags come from
  documentation. `doctor` says which backend is in use and whether it is
  verified.
- **Scrollback only exists for recorded tabs.** It cannot be recovered after
  the fact: scrollback lives inside the terminal emulator's own memory, with no
  API to read it, so a tab that was not recorded from the start has nothing to
  replay. Tabs you open yourself are never recorded.
- **Pipelines are recorded as their first command.** Only the process-group
  leader is visible in `/proc`, so `a | b` is captured as `a`.
- **X11 only in practice.** `gnome-terminal --geometry` is ignored under
  Wayland; everything else still works.
- Tab *order* is inferred from pid order, which matches creation order but not
  a tab you dragged to a new position.
- Non-bash shells get their directory back and nothing else — `--rcfile` is a
  bash flag. zsh would need a generated `ZDOTDIR`; not implemented rather than
  shipped untested.
- At most 50 tabs are reopened by default, so a bad snapshot cannot carpet the
  desktop. Raise with `--max-tabs`.

## Scrollback

Restored tabs run as:

```
bash -c 'cat <replay>; exec script -q -f -a -c "bash --rcfile <bootstrap>" <log>'
```

The replay is `cat` before `script` is exec'd, so replayed text is not written
back into the new log — otherwise the file would double on every reboot. At each
restore the previous replay and log are folded together, capped at 256 KB, and
the log is truncated so it holds only the current boot.

**These logs contain everything the terminal displayed**, including anything
secret ever printed there. They live in `~/.local/state/term-restore/logs/`,
created `0700` with files `0600`, and are pruned when no snapshot refers to them.
`term-restore doctor` reports how many tabs are recording and the total size.
`restore --no-record` turns it off.

## Gotchas

- **`history -s` must not run during startup.** Calling the `history` builtin
  while a startup file is still executing makes bash skip loading
  `~/.bash_history` entirely — you would lose your shell history on every
  restore. The bootstrap defers the seed to the first prompt via
  `PROMPT_COMMAND`, which it then removes. See `_SEED_TEMPLATE` in
  `restore.py`.
- **`gnome-terminal --` swallows the rest of the argv**, so a single command
  line can only ever start its first tab with `--`. Batching therefore uses the
  deprecated `-e`, which takes the command as one string and leaves the parser
  able to see the next `--tab`.
- **One invocation per tab does not group them at login.** A bare `--tab`
  attaches to the terminal's *most recently focused* window, and at login the
  window just created is not focused yet, so every tab opens in a window of its
  own. Waiting for the tab's shell process is not enough -- the process exists
  before the window is mapped. This is why all tabs go in one invocation.
- **An empty snapshot never overwrites a good one.** At shutdown the tabs may
  close before the daemon stops; without that guard a reboot would wipe the
  snapshot it was meant to preserve.
- `restore --boot-guard` (what autostart uses) is a no-op if a restore already
  happened since this boot, and refuses a snapshot taken during the current
  boot. Both stop duplicate tabs piling up. The marker is written only if a tab
  actually opened, so a failed restore can be retried.
- **The daemon only writes when something changed.** Snapshots are compared on a
  fingerprint of (terminal, size, each tab's directory and command) that
  excludes timestamps and pids. Archiving is throttled to one copy per five
  minutes, so the ten kept archives span an hour rather than two minutes.
- **`graphical-session.target` is not universal.** GNOME and KDE reach it; XFCE
  does not, so `install` falls back to `default.target` there. The autostart
  delay uses `sh -c 'sleep N; …'` rather than `X-GNOME-Autostart-Delay`, which
  only GNOME reads.
