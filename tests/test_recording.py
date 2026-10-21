import os
import stat

from term_restore import recording


def test_allocate_reuses_an_existing_log():
    log = recording.allocate()
    assert recording.allocate(str(log)) == log


def test_allocate_rejects_a_path_outside_the_log_directory(tmp_path):
    outside = tmp_path / "elsewhere.log"
    assert recording.allocate(str(outside)) != outside
    assert recording.allocate(str(outside)).parent == recording.logs_dir()


def test_a_fresh_tab_has_nothing_to_replay():
    log = recording.allocate()
    replay, lines = recording.prepare_replay(log)
    assert lines == 0 and not replay.exists()


def test_previous_output_is_carried_into_the_replay():
    log = recording.allocate()
    log.write_bytes(b"line one\nline two\n")
    replay, lines = recording.prepare_replay(log)
    assert lines == 2
    assert replay.read_bytes() == b"line one\nline two\n"
    assert log.read_bytes() == b"", "the log must start this boot clean"


def test_scrollback_accumulates_across_reboots():
    log = recording.allocate()
    log.write_bytes(b"first boot\n")
    recording.prepare_replay(log)
    log.write_bytes(b"second boot\n")
    replay, lines = recording.prepare_replay(log)
    assert replay.read_bytes() == b"first boot\nsecond boot\n"
    assert lines == 2


def test_script_banners_are_not_replayed():
    """Otherwise one pair stacks up per reboot."""
    log = recording.allocate()
    log.write_bytes(b"Script started on 2026-01-01 [COMMAND=x]\nreal output\n"
                    b"Script done on 2026-01-01 [COMMAND_EXIT_CODE=0]\n")
    replay, lines = recording.prepare_replay(log)
    assert replay.read_bytes() == b"real output\n"
    assert lines == 1


def test_the_replay_is_capped():
    log = recording.allocate()
    log.write_bytes(b"x" * 10 + b"\n" + b"y" * 10_000)
    replay, _ = recording.prepare_replay(log, cap=512)
    assert len(replay.read_bytes()) == 512
    assert replay.read_bytes().endswith(b"y")


def test_an_emptied_log_clears_a_stale_replay():
    log = recording.allocate()
    log.write_bytes(b"old\n")
    replay, _ = recording.prepare_replay(log)
    assert replay.exists()
    recording.prepare_replay(log)          # log now empty, replay still has old
    assert replay.exists(), "content should carry forward, not vanish"
    replay.unlink()
    log.write_bytes(b"")
    replay2, lines = recording.prepare_replay(log)
    assert lines == 0 and not replay2.exists()


def test_prune_drops_logs_nothing_refers_to():
    keep = recording.allocate()
    keep.write_bytes(b"keep\n")
    drop = recording.allocate()
    drop.write_bytes(b"drop\n")
    assert recording.prune([str(keep)]) >= 1
    assert keep.exists() and not drop.exists()


def test_logs_are_not_world_readable():
    """These files hold everything the terminal ever displayed."""
    log = recording.allocate()
    log.write_bytes(b"secret\n")
    recording.prepare_replay(log)
    assert stat.S_IMODE(os.stat(str(log)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(str(recording.logs_dir())).st_mode) == 0o700


def test_total_size_counts_the_logs():
    log = recording.allocate()
    log.write_bytes(b"12345")
    assert recording.total_size() >= 5


def test_peek_does_not_touch_anything():
    """`restore --dry-run` builds a plan; an earlier version truncated the logs
    of live tabs just by being asked what it would do."""
    log = recording.allocate()
    log.write_bytes(b"live output\n")
    before = log.read_bytes()
    assert recording.peek_replay(log) == 1
    assert log.read_bytes() == before
    assert not log.with_suffix(".replay").exists()


def test_peek_and_prepare_agree():
    log = recording.allocate()
    log.write_bytes(b"Script started on x\na\nb\n")
    peeked = recording.peek_replay(log)
    _, prepared = recording.prepare_replay(log)
    assert peeked == prepared == 2
