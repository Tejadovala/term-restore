import shlex
import subprocess
from pathlib import Path

from term_restore import restore
from term_restore.model import Snapshot, Tab


def plan_for(tabs, **kw):
    plans, _ = restore.build_plan(
        Snapshot(terminal="gnome-terminal", cols=100, rows=24, tabs=tabs), **kw)
    return plans


def _boot_path(plan):
    """The bootstrap is no longer a bare argv element: recording wraps the
    shell in `script`, so it lives inside the -c string."""
    assert plan.bootstrap, "this tab has no bootstrap"
    return plan.bootstrap


def test_first_tab_opens_a_window_and_the_rest_are_tabs():
    plans = plan_for([Tab(cwd="/etc"), Tab(cwd="/usr"), Tab(cwd="/var")])
    assert "--window" in plans[0].argv and "--geometry=100x24" in plans[0].argv
    for plan in plans[1:]:
        assert "--tab" in plan.argv
        assert "--window" not in plan.argv
        assert not any(a.startswith("--geometry") for a in plan.argv)


def test_working_directory_is_passed_through():
    plan = plan_for([Tab(cwd="/etc")])[0]
    assert plan.argv[plan.argv.index("--working-directory") + 1] == "/etc"
    assert plan.relocated is False


def test_missing_directory_falls_back_to_home(tmp_path):
    plan = plan_for([Tab(cwd=str(tmp_path / "gone"))])[0]
    assert plan.relocated is True
    assert plan.argv[plan.argv.index("--working-directory") + 1] != str(tmp_path / "gone")
    assert "is gone" in _boot_text(plan)


def _boot_text(plan):
    return Path(_boot_path(plan)).read_text()


def _launch_text(plan):
    """What the tab actually runs. It lives in a script so the batch launcher
    can pass it as a single string without nested quoting."""
    assert plan.launch, "this tab has no launch command"
    return Path(plan.launch[1]).read_text()


def test_captured_command_is_never_executed(tmp_path):
    """The guarantee of the whole tool: sourcing the bootstrap must not run it."""
    marker = tmp_path / "ran"
    home = tmp_path / "home"
    home.mkdir()
    plan = plan_for([Tab(cwd="/etc", command=["touch", str(marker)])])[0]
    subprocess.run(["bash", _boot_path(plan)], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                   check=True, timeout=30)
    assert not marker.exists()


def test_quote_injection_in_a_command_cannot_escape(tmp_path):
    marker = tmp_path / "pwned"
    home = tmp_path / "home"
    home.mkdir()
    nasty = ["echo", f"'; touch {marker}; echo '"]
    plan = plan_for([Tab(cwd="/etc", command=nasty)])[0]
    subprocess.run(["bash", _boot_path(plan)], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                   check=True, timeout=30)
    assert not marker.exists()


def test_history_seed_is_present_by_default_and_omitted_with_no_history():
    tab = [Tab(cwd="/etc", command=["tail", "-f", "/var/log/syslog"])]
    assert "history -s" in _boot_text(plan_for(tab)[0])
    assert "history -s" not in _boot_text(plan_for(tab, prefill=False)[0])


def test_notice_is_printed_even_when_history_is_off():
    tab = [Tab(cwd="/etc", command=["pytest", "-k", "vad"])]
    text = _boot_text(plan_for(tab, prefill=False)[0])
    assert "was running" in text and "pytest -k vad" in text


def test_bootstrap_sources_bashrc_before_anything_else():
    text = _boot_text(plan_for([Tab(cwd="/etc", command=["htop"])])[0])
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    assert ".bashrc" in lines[0]


def test_every_bootstrap_is_valid_bash():
    plans = plan_for([
        Tab(cwd="/etc", command=["grep", "-r", "two words", "."]),
        Tab(cwd="/usr", command=[]),
        Tab(cwd="/var", command=["sh", "-c", "a | b && c"]),
    ])
    for plan in plans:
        subprocess.run(["bash", "-n", _boot_path(plan)], check=True, timeout=30)


def test_stale_bootstraps_are_cleared_between_restores():
    from term_restore import paths
    plan_for([Tab(cwd="/a"), Tab(cwd="/b"), Tab(cwd="/c")])
    plan_for([Tab(cwd="/a")])
    assert sorted(p.name for p in paths.boot_dir().glob("tab-*.sh")) == ["tab-0.sh"]


def test_non_bash_shell_restores_directory_only():
    """--rcfile is bash-only, so another shell gets its directory back and
    nothing else."""
    plan = plan_for([Tab(cwd="/etc", shell="/bin/dash", command=["top"])])[0]
    assert "--rcfile" not in plan.argv
    assert plan.argv[plan.argv.index("--working-directory") + 1] == "/etc"


def test_unknown_shell_path_falls_back_to_bash():
    plan = plan_for([Tab(cwd="/etc", shell="/nope/zsh", command=["top"])])[0]
    assert "/bin/bash --rcfile" in _launch_text(plan)


def test_max_tabs_caps_a_runaway_snapshot():
    snap = Snapshot(terminal="gnome-terminal", cols=80, rows=24,
                    tabs=[Tab(cwd="/etc") for _ in range(12)])
    plans, warnings = restore.build_plan(snap, max_tabs=5)
    assert len(plans) == 5
    assert any("12 tabs" in w for w in warnings)


def test_no_warning_when_under_the_cap():
    snap = Snapshot(terminal="gnome-terminal", tabs=[Tab(cwd="/etc")])
    plans, warnings = restore.build_plan(snap, max_tabs=5)
    assert len(plans) == 1 and warnings == []


def test_backend_comes_from_the_snapshot():
    snap = Snapshot(terminal="konsole", tabs=[Tab(cwd="/etc")])
    assert restore.resolve_backend(snap).name == "konsole"


def test_explicit_backend_overrides_the_snapshot():
    snap = Snapshot(terminal="konsole", tabs=[Tab(cwd="/etc")])
    assert restore.resolve_backend(snap, "ptyxis").name == "ptyxis"


def test_unknown_terminal_in_an_old_snapshot_falls_back():
    snap = Snapshot(terminal="rxvt", tabs=[Tab(cwd="/etc")])
    assert restore.resolve_backend(snap) is not None


def test_shell_line_is_safe_to_paste(tmp_path):
    weird = tmp_path / "two words"
    weird.mkdir()
    plans = plan_for([Tab(cwd=str(weird))])
    assert "'" in plans[0].shell_line()
    assert shlex.split(plans[0].shell_line()) == plans[0].argv


def test_a_tab_with_no_command_still_says_it_was_restored():
    """Silence in a restored tab is indistinguishable from a failed restore."""
    text = _boot_text(plan_for([Tab(cwd="/var/log")])[0])
    assert "restored this tab" in text


def test_quiet_suppresses_only_the_no_command_line():
    plans = plan_for([Tab(cwd="/var/log"),
                      Tab(cwd="/etc", command=["htop"])], announce=False)
    assert "restored this tab" not in _boot_text(plans[0])
    assert "was running" in _boot_text(plans[1])


def test_the_notice_is_not_faint():
    """\\033[2m renders close to invisible on several themes."""
    for tab in (Tab(cwd="/etc", command=["htop"]), Tab(cwd="/var/log")):
        text = _boot_text(plan_for([tab])[0])
        assert "\\033[2m" not in text
        assert "\\033[36m[term-restore]" in text


def test_a_relocated_tab_does_not_print_two_notices():
    text = _boot_text(plan_for([Tab(cwd="/definitely/not/here")])[0])
    assert text.count("term-restore]") == 1
    assert "is gone" in text


def test_restored_tabs_are_wrapped_in_script_for_recording():
    plan = plan_for([Tab(cwd="/etc")])[0]
    assert "exec script -q -f -a -c" in _launch_text(plan)
    assert plan.log.endswith(".log")


def test_the_replay_is_cat_before_script_so_it_is_not_re_recorded():
    """Otherwise the log doubles in size on every reboot."""
    text = _launch_text(plan_for([Tab(cwd="/etc")])[0])
    assert text.index("cat ") < text.index("exec script")


def test_no_record_leaves_the_shell_unwrapped():
    plan = plan_for([Tab(cwd="/etc")], record=False)[0]
    text = _launch_text(plan)
    assert "script" not in text
    assert plan.log == ""
    assert "--rcfile" in text


def test_missing_script_binary_degrades_with_a_warning(monkeypatch):
    monkeypatch.setattr(restore.shutil, "which", lambda name: None)
    plans, warnings = restore.build_plan(
        Snapshot(terminal="gnome-terminal", tabs=[Tab(cwd="/etc")]))
    assert any("script" in w for w in warnings)
    text = Path(plans[0].launch[1]).read_text()
    assert "exec script" not in text
    assert "--rcfile" in text, "should fall back to a bare shell"
    assert plans[0].log == ""


def test_the_scrollback_notice_reports_the_line_count():
    from term_restore import recording
    log = recording.allocate()
    log.write_bytes(b"a\nb\nc\n")
    plan = plan_for([Tab(cwd="/etc", log=str(log))])[0]
    assert plan.replayed == 3
    assert "lines of scrollback" in Path(plan.bootstrap).read_text()


def test_building_a_plan_never_truncates_a_live_log():
    from term_restore import recording
    log = recording.allocate()
    log.write_bytes(b"work in progress\n")
    plan_for([Tab(cwd="/etc", log=str(log))])
    assert log.read_bytes() == b"work in progress\n"


def test_pruning_keeps_the_logs_of_tabs_that_are_still_recording(monkeypatch):
    """Pruning to the restore's own plan would delete the logs of live tabs
    that were not part of it."""
    from term_restore import recording
    from term_restore.model import Snapshot as S
    planned = recording.allocate()
    planned.write_bytes(b"a\n")
    live = recording.allocate()
    live.write_bytes(b"b\n")
    unrelated = recording.allocate()
    unrelated.write_bytes(b"c\n")

    monkeypatch.setattr(restore.probe, "take_snapshot",
                        lambda *a, **k: S(tabs=[Tab(cwd="/x", log=str(live))]))
    restore._prune_logs([restore.Plan(index=0, cwd="/x", command="",
                                      argv=[], log=str(planned))])
    assert planned.exists() and live.exists()
    assert not unrelated.exists()


def test_all_tabs_open_in_one_window_via_a_single_invocation():
    """One invocation per tab does not work at login: a bare --tab attaches to
    whatever window has focus, and the new window is not focused yet, so every
    tab ends up in a window of its own."""
    from term_restore.terminals import GNOME_TERMINAL
    plans = plan_for([Tab(cwd="/etc"), Tab(cwd="/var/log"), Tab(cwd="/usr")])
    argv = GNOME_TERMINAL.build_batch_argv(
        [(p.cwd, p.launch) for p in plans], plans[0].geometry)
    assert argv.count("--window") == 1
    assert argv.count("--tab") == 2
    assert [a for a in argv if a.startswith("--geometry")] == ["--geometry=100x24"]
    for cwd in ("/etc", "/var/log", "/usr"):
        assert cwd in argv


def test_a_backend_that_cannot_batch_still_plans_per_tab():
    from term_restore.terminals import KONSOLE
    assert KONSOLE.can_batch is False
    plans = plan_for([Tab(cwd="/etc"), Tab(cwd="/var/log")], backend=KONSOLE)
    assert plans[0].argv[0] == "konsole"
    assert "--new-tab" in plans[1].argv
