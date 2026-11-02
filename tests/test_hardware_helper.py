import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "tools" / "hardware_check.py"
spec = importlib.util.spec_from_file_location("hardware_check", HELPER)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_hardware_report_detects_changed_or_missing_old_success():
    old = {"id": "old", "state": "succeeded", "attempts": 1}
    pending = {"id": "pending", "state": "pending", "attempts": 0}
    assert helper.unchanged_successes(
        [old, pending], [old, {**pending, "state": "running"}]
    )
    assert not helper.unchanged_successes([old], [])
    assert not helper.unchanged_successes([old], [{**old, "attempts": 2}])


def test_hardware_worker_logs_failure_without_console(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["hardware_check.py"])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(
        helper, "run", lambda: (_ for _ in ()).throw(RuntimeError("test failure"))
    )
    with pytest.raises(RuntimeError, match="test failure"):
        helper.main()
    assert "RuntimeError: test failure" in (tmp_path / "worker.log").read_text()


def test_hardware_read_survives_windows_delete_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    original = Path.read_text
    calls = []

    def read_text(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("Windows delete pending")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert helper.read("request.json", {}) == {}
    assert len(calls) == 2


def test_hardware_report_does_not_pass_empty_or_old_checkpoint(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    helper.write(
        "status.json", {"time": 100, "pid": 1, "session": "s", "gaps": [], "runs": []}
    )
    helper.write(
        "checkpoint.json", {"time": 101, "session": "s", "label": "test", "runs": []}
    )
    helper.report()
    output = capsys.readouterr().out
    assert "WAIT: checkpoint needs" in output
    assert "PASS" not in output
    assert "STALE" in output
    report = json.loads(next(tmp_path.glob("report-*.json")).read_text())
    assert report["hardware_cause_verified"] is False


@pytest.mark.integration
@pytest.mark.parametrize("stop_first", [True, False])
@pytest.mark.skipif(
    os.environ.get("LAPTOPSCHEDULER_NATIVE_TESTS") != "1",
    reason="Creates a temporary task and deliberately crashes its disposable worker",
)
def test_hardware_helper_setup_crash_hold_and_cleanup(tmp_path, stop_first):
    directory = tmp_path / "helper space café"
    directory.mkdir()
    script = directory / "chosen_name.py"
    shutil.copyfile(HELPER, script)
    environment = {**os.environ, "LOCALAPPDATA": str(tmp_path)}

    def command(*args):
        result = subprocess.run(
            [sys.executable, str(script), *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=65,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def wait_for(predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = json.loads((evidence / "status.json").read_text())
            if predicate(status):
                return status
            time.sleep(0.2)
        pytest.fail("Helper did not reach expected state")

    try:
        command("setup")
        evidence = next((tmp_path / "LaptopSchedulerChecks").iterdir())
        wait_for(lambda s: any(r["state"] == "succeeded" for r in s["runs"]))
        assert "PASS: interrupted run" in command("crash")
        assert "checkpoint successes unchanged" in command("report")
        command("hold", "1")
        wait_for(
            lambda s: any(
                r["job"] == "hold" and r["state"] == "succeeded" for r in s["runs"]
            )
        )
        command("resume")
        command("probe", "idle")
        wait_for(lambda s: any(r["job"] == "idle" for r in s["runs"]))
        if stop_first:
            command("stop")
    finally:
        command("uninstall")
