import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from laptopscheduler import LaptopScheduler, windows


def test_native_read_only_prerequisite_probes():
    assert type(windows.powershell({"operation": "network"})["ready"]) is bool
    native = windows.Windows()
    assert type(native.ac_ready()) is bool
    assert type(native.idle_ready(1)) is bool


def test_power_prerequisites_fail_closed(make_scheduler, monkeypatch):
    s = make_scheduler()
    s.date(id="job", requires_network=True, requires_ac=True, idle_seconds=60)(
        lambda: None
    )
    native = windows.Windows()
    monkeypatch.setattr(native, "ac_ready", lambda: False)
    monkeypatch.setattr(
        windows,
        "powershell",
        lambda payload: pytest.fail("network probed before failed AC"),
    )
    assert not native.ready(s._jobs["job"])
    monkeypatch.setattr(native, "ac_ready", lambda: True)
    monkeypatch.setattr(native, "idle_ready", lambda seconds: False)
    assert not native.ready(s._jobs["job"])
    monkeypatch.setattr(native, "idle_ready", lambda seconds: True)
    monkeypatch.setattr(
        windows,
        "powershell",
        lambda payload: (_ for _ in ()).throw(RuntimeError("unknown connectivity")),
    )
    assert not native.ready(s._jobs["job"])


def test_startup_input_is_json_not_shell_code(make_scheduler, tmp_path, monkeypatch):
    directory = tmp_path / "a 'quoted' $variable & café"
    directory.mkdir()
    captured = []

    def run(command, **kwargs):
        captured.append((command, kwargs))
        payload = json.loads(kwargs["input"])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"registered": True, "task_name": payload["name"]}).encode(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    s = make_scheduler()
    s.install_startup("example.module", directory)
    command, kwargs = captured[0]
    assert str(directory) not in command
    assert "example.module" not in command
    assert not kwargs.get("shell", False)
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    payload = json.loads(kwargs["input"])
    assert payload["working_directory"] == str(directory)
    assert payload["python"] == sys.executable
    with pytest.raises(ValueError, match="module"):
        s.install_startup("example; malicious", directory)


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("LAPTOPSCHEDULER_NATIVE_TESTS") != "1",
    reason="Native task mutation is opt-in",
)
def test_native_registration_verification_failure_rolls_back(tmp_path, monkeypatch):
    app = "rollback-test-" + uuid.uuid4().hex
    s = LaptopScheduler(app, database=tmp_path / "rollback.sqlite3")
    original_path = windows.__file__
    script = Path(original_path).with_name("startup.ps1").read_text(encoding="utf-8")
    script = script.replace(
        "function Test-Configuration($task) {",
        "function Test-Configuration($task) {\n    return $false",
    )
    (tmp_path / "startup.ps1").write_text(script, encoding="utf-8")
    try:
        monkeypatch.setattr(windows, "__file__", str(tmp_path / "windows.py"))
        with pytest.raises(RuntimeError, match="verification failed"):
            s.install_startup("example.module", tmp_path)
        monkeypatch.setattr(windows, "__file__", original_path)
        assert not s.startup_status().registered
    finally:
        monkeypatch.setattr(windows, "__file__", original_path)
        try:
            s.uninstall_startup()
        finally:
            s.shutdown()
