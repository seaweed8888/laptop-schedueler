"""Disposable Windows acceptance helper; see docs/hardware-testing.md."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from laptopscheduler import LaptopScheduler

SCRIPT = Path(__file__).resolve()
KEY = hashlib.sha256(str(SCRIPT).casefold().encode()).hexdigest()[:12]
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LaptopSchedulerChecks" / KEY
APP = "hardware-check-" + KEY
POLICIES = ("all", "latest", "skip")


def file_io(action):
    # Windows briefly denies access while another process replaces/deletes a file.
    for attempt in range(10):
        try:
            return action()
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05)


def read(name, default=None):
    try:
        return json.loads(file_io(lambda: (ROOT / name).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return default


def write(name, data):
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / name
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    file_io(lambda: os.replace(temporary, path))


def scheduler():
    return LaptopScheduler(APP, database=ROOT / "ledger.sqlite3")


def summarize(s):
    return [
        {
            "id": r.run_id,
            "job": r.job_id,
            "scheduled": r.scheduled_time.timestamp(),
            "state": r.outcome,
            "attempts": r.attempts,
            "manual": r.manual,
            "finished": r.finished_at.timestamp() if r.finished_at else None,
        }
        for r in s.history(limit=100_000)
    ]


def unchanged_successes(before, after):
    current = {r["id"]: r for r in after}
    return all(current.get(r["id"]) == r for r in before if r["state"] == "succeeded")


def request(operation, value=None, timeout=15):
    if (ROOT / "request.json").exists():
        raise RuntimeError(
            "A command is already pending. Start the worker or wait for it."
        )
    identifier = uuid.uuid4().hex
    write("request.json", {"id": identifier, "operation": operation, "value": value})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reply = read("reply.json", {})
        if reply.get("id") == identifier:
            if "error" in reply:
                raise RuntimeError(reply["error"])
            return reply
        time.sleep(0.2)
    raise RuntimeError(
        "Worker did not answer. See worker.log; the command remains pending."
    )


def launch():
    with (ROOT / "worker.log").open("a", encoding="utf-8") as log:
        return subprocess.Popen(
            [sys.executable, str(SCRIPT)],
            cwd=SCRIPT.parent,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def run():
    config = read("config.json")
    if config is None:
        raise RuntimeError("Run setup first.")
    s = scheduler()
    session = uuid.uuid4().hex
    previous = read("status.json", {})
    gaps = previous.get("gaps", [])
    now = time.time()
    if previous:
        gaps.append({"kind": "restart", "before": previous["time"], "after": now})
    crash_ready = threading.Event()
    halt = threading.Event()
    errors = []

    def harmless():
        pass

    for policy in POLICIES:
        s.interval(
            id=policy,
            seconds=2,
            catch_up=policy,
            start_date=datetime.fromtimestamp(config["anchor"], timezone.utc),
        )(harmless)
    for probe, options in (
        ("network", {"requires_network": True}),
        ("ac", {"requires_ac": True}),
        ("idle", {"idle_seconds": 60}),
    ):
        s.date(id=probe, run_date="2099-01-01", **options)(harmless)

    @s.date(id="hold", run_date="2099-01-01")
    def hold():
        # Persist the duration before enqueueing so a restart can retry this job.
        time.sleep(read("hold.json")["seconds"])

    @s.date(id="crash", run_date="2099-01-01", retries=(0, 0))
    def crash():
        if not (ROOT / "crash-entered.json").exists():
            write("crash-entered.json", {"time": time.time()})
            if not crash_ready.wait(15):
                raise RuntimeError("Crash command acknowledgement timed out")
            os._exit(23)  # Deliberately kill only this disposable test process.

    def snapshot():
        write(
            "status.json",
            {
                "time": time.time(),
                "session": session,
                "pid": os.getpid(),
                "gaps": gaps,
                "runs": summarize(s),
            },
        )

    def monitor():
        last = time.time()
        try:
            while not halt.is_set():
                now = time.time()
                if now - last > 10:
                    gaps.append(
                        {"kind": "live-process gap", "before": last, "after": now}
                    )
                last = now
                command = read("request.json")
                stop = False
                if command:
                    reply = {"id": command["id"]}
                    try:
                        op = command["operation"]
                        if op == "checkpoint":
                            write(
                                "checkpoint.json",
                                {
                                    "label": command["value"],
                                    "time": now,
                                    "session": session,
                                    "runs": summarize(s),
                                },
                            )
                        elif op == "queue":
                            job = command["value"]
                            if job == "crash":
                                (ROOT / "crash-entered.json").unlink(missing_ok=True)
                                crash_ready.clear()
                            reply["run_id"] = s.run_job(job)
                        elif op == "hold":
                            # Keep the short interval jobs from renewing wake requests afterwards.
                            for policy in POLICIES:
                                s.pause_job(policy)
                            write("hold.json", {"seconds": command["value"]})
                            reply["run_id"] = s.run_job("hold")
                        elif op == "resume":
                            for policy in POLICIES:
                                s.resume_job(policy)
                        elif op == "stop":
                            stop = True
                        else:
                            raise ValueError("Unknown helper command")
                    except Exception as error:
                        reply["error"] = str(error)
                    snapshot()
                    file_io(lambda: (ROOT / "request.json").unlink())
                    write("reply.json", reply)
                    if command["operation"] == "queue" and command["value"] == "crash":
                        crash_ready.set()
                snapshot()
                if stop:
                    s.shutdown(timeout=0)
                    return
                halt.wait(1)
        except Exception as error:
            errors.append(error)
            s.shutdown(timeout=0)

    watcher = threading.Thread(target=monitor, name="hardware-check-monitor")
    try:
        snapshot()
        watcher.start()
        s.run()
    finally:
        halt.set()
        if watcher.ident is not None:
            watcher.join(5)
        s.shutdown()
    if errors:
        raise errors[0]


def report():
    status = read("status.json")
    if not status:
        raise RuntimeError("No worker report yet. Run setup first.")
    age = time.time() - status["time"]
    print(f"Evidence: {ROOT}\nSnapshot age: {age:.1f}s; process: {status['pid']}")
    if age > 10:
        print(
            "STALE: worker may be stopped or suspended; this is not a live health check."
        )
    baseline = read("checkpoint.json")
    if baseline:
        successes = sum(r["state"] == "succeeded" for r in baseline["runs"])
        if status["time"] < baseline["time"] or not successes:
            print(
                "WAIT: checkpoint needs a newer snapshot and at least one old success."
            )
        else:
            same = unchanged_successes(baseline["runs"], status["runs"])
            print(
                f"{'PASS' if same else 'FAIL'}: {successes} checkpoint successes unchanged."
            )
        print(
            f"Scenario: {baseline['label']}; new process: {baseline['session'] != status['session']}"
        )
        gaps = [g for g in status["gaps"] if g["after"] >= baseline["time"]]
        for gap in gaps:
            duration = gap["after"] - gap["before"]
            print(
                f"Observed {gap['kind']}: {duration:.1f}s (not proof of its physical cause)"
            )
            # Exclude edges: heartbeat and dispatcher need not run at the same instant.
            for policy in POLICIES:
                rows = [
                    r
                    for r in status["runs"]
                    if r["job"] == policy
                    and gap["before"] + 4 < r["scheduled"] < gap["after"] - 4
                ]
                completed = sum(r["state"] == "succeeded" for r in rows)
                print(
                    f"  {policy}: {completed} successes strictly inside the downtime window"
                )
        if not gaps:
            print(
                "WAIT: no restart or >10s heartbeat gap recorded after this checkpoint."
            )
    for job in (*POLICIES, "network", "ac", "idle", "hold", "crash"):
        rows = [r for r in status["runs"] if r["job"] == job]
        if rows:
            latest = max(rows, key=lambda r: r["scheduled"])
            print(
                f"{job}: {len(rows)} rows; newest {latest['state']}, "
                f"attempts={latest['attempts']}, run_id={latest['id']}"
            )
    print("Hardware observations remain manual. See docs/hardware-testing.md.")
    report_name = f"report-{time.time_ns()}.json"
    write(
        report_name,
        {
            "environment": read("config.json"),
            "checkpoint": baseline,
            "snapshot": status,
            "hardware_cause_verified": False,
        },
    )
    print(f"Saved report: {ROOT / report_name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=(
            "setup",
            "run",
            "checkpoint",
            "report",
            "probe",
            "hold",
            "resume",
            "crash",
            "stop",
            "uninstall",
        ),
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Run this helper on Windows.")
    ROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "setup":
        if not SCRIPT.stem.isidentifier() or not SCRIPT.stem.isascii():
            parser.error("The script filename must be an ASCII Python module name.")
        if read("config.json") is None:
            write(
                "config.json",
                {
                    "anchor": time.time(),
                    "python": sys.executable,
                    "windows": platform.platform(),
                    "package": version("laptopscheduler"),
                    "script": str(SCRIPT),
                },
            )
        s = scheduler()
        try:
            print(s.install_startup(SCRIPT.stem, SCRIPT.parent))
        finally:
            s.shutdown()
        launch()
        request("checkpoint", "initial setup")
        print(f"Worker started. Task: LaptopScheduler-{APP}\nEvidence: {ROOT}")
    elif args.command == "run":
        with (ROOT / "worker.log").open("a", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                try:
                    run()
                except BaseException:
                    traceback.print_exc()
                    raise
    elif args.command == "report":
        report()
    elif args.command == "checkpoint":
        request("checkpoint", args.value or "manual test")
        print("Checkpoint saved. Now perform the Windows action, then run report.")
    elif args.command == "probe":
        if args.value not in ("network", "ac", "idle"):
            parser.error("probe requires network, ac, or idle")
        print(request("queue", args.value))
    elif args.command == "hold":
        try:
            seconds = int(args.value or "180")
        except ValueError:
            parser.error("hold requires seconds (1 to 3600)")
        if not 1 <= seconds <= 3600:
            parser.error("hold requires seconds (1 to 3600)")
        print(request("hold", seconds))
        print("Interval jobs paused. Run resume after testing normal idle sleep.")
    elif args.command == "crash":
        request("checkpoint", "forced termination")
        reply = request("queue", "crash")
        time.sleep(2)
        child = launch()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = read("status.json", {})
            row = next(
                (r for r in status.get("runs", []) if r["id"] == reply["run_id"]), None
            )
            if row and row["state"] == "succeeded" and row["attempts"] == 2:
                print(f"PASS: interrupted run {row['id']} recovered on attempt 2.")
                return
            if child.poll() is not None:
                break
            time.sleep(0.2)
        raise RuntimeError("Recovery not confirmed. See report and worker.log.")
    elif args.command in ("resume", "stop"):
        print(request(args.command))
    elif args.command == "uninstall":
        deadline = time.monotonic() + 35
        stop_requested = False
        while True:
            try:
                s = scheduler()
                break
            except RuntimeError:
                if not stop_requested:
                    stop_requested = True
                    try:
                        request("stop", timeout=5)
                    except RuntimeError:
                        pass  # Ownership is authoritative if the worker just exited.
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Worker still owns the database. Let any hold job finish, then retry."
                    ) from None
                time.sleep(0.2)
        try:
            print(s.uninstall_startup())
            (ROOT / "request.json").unlink(missing_ok=True)
        finally:
            s.shutdown()
        print(f"Startup removed; evidence retained at {ROOT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
