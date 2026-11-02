import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from laptopscheduler import LaptopScheduler


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out")


def test_actual_runtime_executes_sequentially(make_scheduler):
    s = make_scheduler()
    calls = []
    s.date(id="job")(lambda: calls.append(threading.current_thread().name))
    runner = threading.Thread(target=s.run)
    runner.start()
    try:
        wait_until(lambda: len(calls) == 1)
        s.run_job("job")
        wait_until(lambda: len(calls) == 2)
        assert all(name.startswith("LaptopScheduler-") for name in calls)
        assert s.history()[0].outcome == "succeeded"
    finally:
        s.shutdown()
        runner.join(5)
    assert not runner.is_alive()


def test_timeout_retains_owner_until_thread_exits(make_scheduler):
    s = make_scheduler()
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        release.wait(10)

    s.date(id="job")(blocked)
    runner = threading.Thread(target=s.run)
    runner.start()
    try:
        assert entered.wait(5)
        s.shutdown(timeout=0.01)
        with pytest.raises(RuntimeError, match="owns"):
            make_scheduler()
        assert s._worker.is_alive()
    finally:
        release.set()
        runner.join(5)
    replacement = make_scheduler()
    replacement.date(id="job")(lambda: None)
    assert replacement.history()[0].outcome == "succeeded"


def test_storage_failure_propagates_through_run(make_scheduler, monkeypatch):
    s = make_scheduler()
    monkeypatch.setattr(
        s,
        "_reconcile",
        lambda now: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O")),
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        s.run()
    replacement = make_scheduler()
    assert replacement.jobs() == ()


def test_worker_failure_propagates_through_run(make_scheduler, monkeypatch):
    s = make_scheduler()
    monkeypatch.setattr(
        s,
        "_execute_one",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O")),
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        s.run()


def test_ownership_for_same_app_different_database_and_same_database(
    make_scheduler, tmp_path
):
    s = make_scheduler()
    with pytest.raises(RuntimeError, match="owns"):
        LaptopScheduler(s.app_id, database=tmp_path / "other.sqlite3")
    with pytest.raises(RuntimeError, match="owns"):
        LaptopScheduler("other-" + uuid.uuid4().hex, database=s.database)


def test_forced_process_death_recovers_committed_claim(tmp_path):
    app = "crash-" + uuid.uuid4().hex
    database = tmp_path / "crash.sqlite3"
    marker = tmp_path / "entered"
    code = """
import sys,time
from pathlib import Path
from laptopscheduler import LaptopScheduler
s = LaptopScheduler(sys.argv[1], database=sys.argv[2])
@s.date(id='job', retries=(0,0))
def job():
    Path(sys.argv[3]).write_text('entered')
    time.sleep(60)
s.run()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, app, str(database), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        wait_until(marker.exists)
        with pytest.raises(RuntimeError, match="owns"):
            LaptopScheduler(app, database=database)
        process.kill()
        process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    s = LaptopScheduler(app, database=database)
    try:
        done = []
        s.date(id="job", retries=(0, 0))(lambda: done.append(1))
        assert s.history()[0].attempts == 1
        assert s._execute_one()
        assert done == [1]
        assert s.history()[0].attempts == 2
        assert s.history()[0].outcome == "succeeded"
    finally:
        s.shutdown()
    s = LaptopScheduler(app, database=database)
    try:
        s.date(id="job", retries=(0, 0))(lambda: pytest.fail("success replayed"))
        s._prepare(time.time())
        s._reconcile(time.time())
        assert not s._execute_one()
        assert len(s.history()) == 1
    finally:
        s.shutdown()


def test_database_contention_stops_without_claim(make_scheduler):
    s = make_scheduler()
    s.date(id="job")(lambda: pytest.fail("ran during lock failure"))
    s.run_job("job")
    s._store.db.execute("PRAGMA busy_timeout=10")
    blocker = sqlite3.connect(s.database, isolation_level=None)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            s._execute_one()
        assert s._stop.is_set()
        assert s._store.history(None, 100)[0].attempts == 0
    finally:
        blocker.close()


def test_priority_lower_first(make_scheduler):
    s = make_scheduler()
    calls = []
    s.date(id="low", priority=10)(lambda: calls.append("low"))
    s.date(id="high", priority=-10)(lambda: calls.append("high"))
    s.run_job("low")
    s.run_job("high")
    s._execute_one()
    s._execute_one()
    assert calls == ["high", "low"]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("LAPTOPSCHEDULER_NATIVE_TESTS") != "1",
    reason="Set LAPTOPSCHEDULER_NATIVE_TESTS=1 to create a temporary native task",
)
def test_native_task_registration_idempotency_conflict_unicode_and_removal(tmp_path):
    app = "native-test-" + uuid.uuid4().hex
    directory = tmp_path / "spaces 'quotes' $dollar & café 日本"
    directory.mkdir()
    (directory / "check_startup.py").write_text(
        """
import ctypes, json, sys
from pathlib import Path
import laptopscheduler
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetConsoleWindow.restype = ctypes.c_void_p
Path("launched.json").write_text(json.dumps({
    "console": kernel.GetConsoleWindow(), "prefix": sys.prefix,
    "python": sys.executable, "package": laptopscheduler.__file__,
}))
""",
        encoding="utf-8",
    )
    s = LaptopScheduler(app, database=tmp_path / "native.sqlite3")
    try:
        assert not s.startup_status().registered
        status = s.install_startup("check_startup", directory)
        assert status.registered and status.owned
        assert Path(status.python) == Path(sys.executable).with_name("pythonw.exe")
        assert Path(status.working_directory) == directory
        assert status.arguments == "-m check_startup"
        assert s.install_startup("check_startup", directory) == status
        with pytest.raises(RuntimeError, match="Conflicting"):
            s.install_startup("different.module", directory)
        from laptopscheduler.windows import startup

        with pytest.raises(RuntimeError, match="another owner"):
            startup("uninstall", app, "not-the-owner")
        assert s.startup_status().registered
        subprocess.run(
            [
                str(
                    Path(os.environ["SystemRoot"])
                    / "System32/WindowsPowerShell/v1.0/powershell.exe"
                ),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; $name=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
                "$service=New-Object -ComObject Schedule.Service; $service.Connect(); "
                "$null=$service.GetFolder('\\').GetTask($name).Run($null)",
            ],
            input=json.dumps(status.task_name),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        wait_until(lambda: (directory / "launched.json").exists())
        wait_until(lambda: s.startup_status().state == "ready")
        launched = json.loads((directory / "launched.json").read_text())
        assert launched["console"] is None
        assert Path(launched["prefix"]) == Path(sys.prefix)
        assert Path(launched["python"]) == Path(status.python)
    finally:
        try:
            s.uninstall_startup()
            assert not s.startup_status().registered
            assert s.database.exists()
        finally:
            s.shutdown()
