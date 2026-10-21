import time

from term_restore import paths, store
from term_restore.model import Snapshot, Tab


def snap(n, terminal="gnome-terminal", **kw):
    return Snapshot(terminal=terminal,
                    tabs=[Tab(cwd="/dir{}".format(i)) for i in range(n)], **kw)


def test_empty_snapshot_never_clobbers_a_good_one():
    assert store.save(snap(3))
    assert store.save(snap(0)) is False
    assert store.load().tab_count == 3


def test_empty_snapshot_is_written_when_nothing_exists():
    assert store.save(snap(0))
    assert store.load().tab_count == 0


def test_an_unchanged_snapshot_is_not_rewritten():
    """The daemon ticks every 15s; an idle desktop must not touch the disk."""
    assert store.save(snap(2))
    before = paths.snapshot_path().stat().st_mtime_ns
    assert store.save(snap(2)) is False
    assert paths.snapshot_path().stat().st_mtime_ns == before


def test_a_changed_directory_counts_as_a_change():
    store.save(Snapshot(tabs=[Tab(cwd="/a")]))
    assert store.save(Snapshot(tabs=[Tab(cwd="/b")]))
    assert store.load().tabs[0].cwd == "/b"


def test_a_changed_command_counts_as_a_change():
    store.save(Snapshot(tabs=[Tab(cwd="/a")]))
    assert store.save(Snapshot(tabs=[Tab(cwd="/a", command=["htop"])]))


def test_fingerprint_ignores_timestamp_and_pids():
    a = Snapshot(saved_at=1.0, tabs=[Tab(cwd="/a", pid=1)])
    b = Snapshot(saved_at=999.0, tabs=[Tab(cwd="/a", pid=2)])
    assert a.fingerprint == b.fingerprint


def test_archiving_is_throttled(monkeypatch):
    """Without a floor, ten archives would span two minutes of a 15s daemon."""
    store.save(snap(1))
    store.save(snap(2))
    assert len(store.history()) == 1
    store.save(snap(3))
    assert len(store.history()) == 1, "second archive should be throttled"


def test_archiving_resumes_after_the_interval(monkeypatch):
    monkeypatch.setattr(store, "ARCHIVE_MIN_INTERVAL", 0.0)
    store.save(snap(1))
    store.save(snap(2))
    store.save(snap(3))
    assert len(store.history()) == 2


def test_history_is_capped(monkeypatch):
    monkeypatch.setattr(store, "ARCHIVE_MIN_INTERVAL", 0.0)
    for i in range(store.HISTORY_KEEP + 5):
        store.save(snap(1, saved_at=float(i)))
        store.save(snap(2, saved_at=float(i) + 0.5))
    assert len(store.history()) <= store.HISTORY_KEEP


def test_load_of_corrupt_file_returns_none():
    paths.ensure_dirs()
    paths.snapshot_path().write_text("{not json")
    assert store.load() is None


def test_prune_markers_keeps_only_the_current_boot():
    paths.ensure_dirs()
    for boot in ("aaa", "bbb", "ccc"):
        paths.restore_marker(boot).write_text("x")
    assert store.prune_markers("bbb") == 2
    assert [p.name for p in paths.state_dir().glob("restored-*")] == ["restored-bbb"]


def test_prune_markers_with_no_markers_is_a_no_op():
    paths.ensure_dirs()
    assert store.prune_markers("aaa") == 0


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_growth_is_saved_immediately():
    guard = store.ShrinkGuard(grace=60, clock=FakeClock())
    assert guard.should_save(snap(3), snap(2))


def test_same_size_is_saved_immediately():
    guard = store.ShrinkGuard(grace=60, clock=FakeClock())
    assert guard.should_save(snap(2), snap(2))


def test_first_snapshot_is_saved_immediately():
    guard = store.ShrinkGuard(grace=60, clock=FakeClock())
    assert guard.should_save(snap(2), None)


def test_a_shrink_is_not_persisted_straight_away():
    """A reboot closing tabs under the daemon must not wipe the snapshot."""
    guard = store.ShrinkGuard(grace=60, clock=FakeClock())
    assert guard.should_save(snap(1), snap(4)) is False


def test_a_shrink_that_holds_is_eventually_persisted():
    clock = FakeClock()
    guard = store.ShrinkGuard(grace=60, clock=clock)
    assert guard.should_save(snap(1), snap(4)) is False
    clock.now = 59
    assert guard.should_save(snap(1), snap(4)) is False
    clock.now = 61
    assert guard.should_save(snap(1), snap(4)) is True


def test_a_further_shrink_restarts_the_clock():
    clock = FakeClock()
    guard = store.ShrinkGuard(grace=60, clock=clock)
    guard.should_save(snap(3), snap(4))
    clock.now = 59
    assert guard.should_save(snap(2), snap(4)) is False
    clock.now = 100
    assert guard.should_save(snap(2), snap(4)) is False, "clock restarted at 59"
    clock.now = 120
    assert guard.should_save(snap(2), snap(4)) is True


def test_recovering_to_the_old_size_clears_the_pending_shrink():
    clock = FakeClock()
    guard = store.ShrinkGuard(grace=60, clock=clock)
    guard.should_save(snap(1), snap(4))
    assert guard.should_save(snap(4), snap(4)) is True
    clock.now = 999
    assert guard.should_save(snap(1), snap(4)) is False, "must start over"
