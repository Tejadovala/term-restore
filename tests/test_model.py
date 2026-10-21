from term_restore.model import Snapshot, Tab


def test_round_trip_preserves_tabs():
    snap = Snapshot(boot_id="b", cols=100, rows=24, tabs=[
        Tab(cwd="/etc", command=["tail", "-f", "/var/log/syslog"], pid=7),
        Tab(cwd="/home/teja"),
    ])
    back = Snapshot.from_json(snap.to_json())
    assert back.boot_id == "b"
    assert (back.cols, back.rows) == (100, 24)
    assert [t.cwd for t in back.tabs] == ["/etc", "/home/teja"]
    assert back.tabs[0].command == ["tail", "-f", "/var/log/syslog"]


def test_geometry_needs_both_dimensions():
    assert Snapshot(cols=80, rows=24).geometry == "80x24"
    assert Snapshot(cols=80).geometry is None
    assert Snapshot().geometry is None


def test_command_str_quotes_spaces():
    assert Tab(cwd="/", command=["grep", "two words", "f"]).command_str == \
        "grep 'two words' f"
    assert Tab(cwd="/").command_str == ""


def test_future_schema_is_rejected():
    import json
    import pytest
    with pytest.raises(ValueError, match="newer than this build"):
        Snapshot.from_json(json.dumps({"version": 99, "tabs": []}))


def test_missing_fields_default():
    snap = Snapshot.from_json('{"version": 1}')
    assert snap.tabs == [] and snap.tab_count == 0


def test_terminal_name_survives_a_round_trip():
    back = Snapshot.from_json(Snapshot(terminal="ptyxis", tabs=[]).to_json())
    assert back.terminal == "ptyxis"


def test_snapshot_without_a_terminal_field_still_loads():
    """Snapshots written before backends existed must not break an upgrade."""
    snap = Snapshot.from_json('{"version": 1, "tabs": [{"cwd": "/etc"}]}')
    assert snap.terminal == "" and snap.tabs[0].cwd == "/etc"


def test_fingerprint_changes_with_the_terminal():
    a = Snapshot(terminal="konsole", tabs=[Tab(cwd="/a")])
    b = Snapshot(terminal="ptyxis", tabs=[Tab(cwd="/a")])
    assert a.fingerprint != b.fingerprint
