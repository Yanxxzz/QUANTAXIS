from panda_alpha.team import runtime_io


def setup_control(tmp_path, monkeypatch, *, run=True, deadline=2000):
    monkeypatch.setattr(runtime_io.time, "time", lambda: 1000)
    runtime_io.write(tmp_path / "control.json", {"run": run, "heartbeat": 900})
    runtime_io.write(tmp_path / "supervisor.json", {"status": "running", "process": {"pid": 42, "created": 7}})
    return {"deadline_epoch": deadline, "workers": {"orphan_timeout": 30}}


def test_delayed_heartbeat_with_same_live_supervisor_is_not_an_orphan(tmp_path, monkeypatch):
    manifest = setup_control(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_io, "owned", lambda receipt: receipt == {"pid": 42, "created": 7})
    assert runtime_io.active(tmp_path, manifest)


def test_real_orphan_and_reused_pid_still_stop(tmp_path, monkeypatch):
    manifest = setup_control(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime_io, "owned", lambda receipt: None)
    assert not runtime_io.active(tmp_path, manifest)


def test_live_owner_cannot_override_stop_or_deadline(tmp_path, monkeypatch):
    manifest = setup_control(tmp_path, monkeypatch, run=False)
    monkeypatch.setattr(runtime_io, "owned", lambda receipt: True)
    assert not runtime_io.active(tmp_path, manifest)
    manifest = setup_control(tmp_path, monkeypatch, deadline=1000)
    assert not runtime_io.active(tmp_path, manifest)


def test_paused_owner_does_not_revive_a_stale_run_flag(tmp_path, monkeypatch):
    manifest = setup_control(tmp_path, monkeypatch)
    runtime_io.write(tmp_path / "supervisor.json", {"status": "paused", "process": {"pid": 42, "created": 7}})
    monkeypatch.setattr(runtime_io, "owned", lambda receipt: True)
    assert not runtime_io.active(tmp_path, manifest)
