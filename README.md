# LaptopScheduler

Download here (https://pypi.org/project/laptopscheduler/)

Run recurring Python tasks on the Windows laptop you already own, without paying for a hosted server. LaptopScheduler is useful for backups, folder syncing, reports, data imports, file cleanup, and other tasks that can wait until your laptop is available.

Your laptop does not need to stay on around the clock. LaptopScheduler saves each scheduled run's progress so that, when your program starts again, it can handle missed work according to your chosen catch-up policy and retry interrupted jobs within their retry limits.

You write the task functions; the library handles scheduling, retries, and execution history. Import it into your Python application and schedule jobs with APScheduler-style cron, interval, or date triggers. Jobs run one at a time, with idle sleep prevented during execution by default.

**Your laptop must be running for jobs to execute.** An optional Windows Task Scheduler helper starts your Python module after you log in. LaptopScheduler does not power on the laptop or wake it from sleep.

## Install and run

Requires **Windows and Python 3.10+**. Install in a virtual environment:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install laptopscheduler
```

To install from a source checkout instead, use `.venv\Scripts\python -m pip install .` from the repository directory.

Add this to your application's entry point, using any filename or package structure you prefer:

```python
from laptopscheduler import LaptopScheduler

scheduler = LaptopScheduler(
    app_id="my-application",
    timezone="America/Los_Angeles",
)

@scheduler.cron(
    id="daily-task",
    hour=9,
    catch_up="latest",
)
def daily_task():
    # Put your task logic here, or call a function from your application.
    print("Running the daily task")

if __name__ == "__main__":
    scheduler.run()
```

Run your application with the Python interpreter where you installed LaptopScheduler. You can execute a script by its path or use `python -m` with your application's module name. There is no required script name or directory layout. Choose your own stable `app_id` and job IDs; they identify the application's saved state and its jobs.

`run()` keeps the scheduler running; Ctrl+C requests shutdown. The example runs daily at 9 a.m. in Los Angeles. With `catch_up="latest"`, it can also run the most recent missed occurrence immediately on startup, including the first launch. Use `catch_up="skip"` if you only want future scheduled times.

Schedules can also use `@scheduler.interval(id="sync", minutes=30)` for recurring intervals, or `@scheduler.date(id="one-off", run_date="2027-03-01 09:00:00")` for a specific date and time. Declare all jobs before calling `run()`.

## Start after login

Startup registration is optional. Choose either the Python helper below or [manual Windows Task Scheduler setup](#manual-windows-task-scheduler-setup). You can also start your application yourself. Neither the startup helper nor the hardware test script is required to use the library; your scheduled functions still run in Python.

### Optional Python startup helper

Call `install_startup()` once from your own setup code, using the same Python environment as the application and while its scheduler is stopped. No separate setup script or specific filename is required.

For example, if your application's entry point is the importable module `your_package.worker`:

```python
from laptopscheduler import LaptopScheduler

scheduler = LaptopScheduler(app_id="my-application")  # Same app_id as your application.
try:
    print(scheduler.install_startup(
        module="your_package.worker",  # Replace with your actual module name.
        working_directory=r"C:\path\to\your\application",  # Replace with its directory.
    ))
finally:
    scheduler.shutdown()
```

The names and paths above are placeholders. `module` must be the dotted import name that works with `python -m`, without a `.py` extension. For a standalone script, use its filename without `.py` and its containing directory. For a package, use its module path and a working directory from which it can be imported. That module must register your jobs and call `run()` when executed. If you use a custom `database` path, supply the same path in setup code.

This registers `LaptopScheduler-<app_id>` for your next Windows login. It uses the `pythonw.exe` beside your current Python executable, `-m <module>`, and your chosen working directory. This keeps the same virtual environment and starts without a terminal window. Registration fails if that environment has no `pythonw.exe`. Keep that environment and module installed at those paths. Running setup again with the same configuration is safe. Different paths/settings or another owner's task cause a conflict; uninstall an owned task before changing its configuration, including upgrading an older task that used `python.exe`.

Startup jobs have no interactive console. Configure file logging in your application for diagnostics; console output and interactive input are unavailable. The hardware test helper writes its worker errors to `worker.log` in its evidence directory.

The task runs with normal privileges, ignores duplicate instances, allows battery operation, and has no execution limit. On process failure it retries three times, one minute apart. It has no wake timer or task-wide network/idle condition. `startup_status()` reports native task state and configured paths; native `running` does not establish worker health. `uninstall_startup()` removes only the owned task and retains SQLite data.

Windows must launch Python; Python cannot launch itself after reboot. This helper uses a [current-user logon trigger](https://learn.microsoft.com/en-us/windows/win32/taskschd/starting-an-executable-when-a-user-logs-on). It does not start before login or wake a powered-off laptop.

### Manual Windows Task Scheduler setup

This performs the same startup role without calling `install_startup()`. Your application must still declare its jobs and call `scheduler.run()`. Windows launches that application once at login; LaptopScheduler handles the individual job schedules, retries, and catch-up.

1. Open **Task Scheduler** from the Windows Start menu and select **Create Task**.
2. On **General**, choose any descriptive name, select your current Windows user, choose **Run only when user is logged on**, and leave **Run with highest privileges** unchecked.
3. On **Triggers**, select **New → At log on → Specific user**, choose your account, and leave the trigger enabled. Do not add a repeating trigger for each Python job.
4. On **Actions**, select **New → Start a program** and fill in these fields using your actual paths:

   | Field | Example | Purpose |
   |---|---|---|
   | Program/script | `C:\path\to\application\.venv\Scripts\pythonw.exe` | Starts the Python environment containing LaptopScheduler without a terminal window |
   | Add arguments | `-m your_package.worker` | Runs your application's importable module |
   | Start in | `C:\path\to\application` | Sets the directory from which the module and relative files resolve |

   The paths and module name are placeholders, not required names. Use **Browse** to choose the interpreter. Enter the **Start in** directory without surrounding quotes. For a standalone script, you can instead set **Add arguments** to its full quoted path, such as `"C:\My Projects\nightly tasks.py"`; no particular script filename is required. Use the same stable `app_id` and database location as your normal application.

   Choose `python.exe` instead of `pythonw.exe` if you want a visible console for debugging. With `pythonw.exe`, configure application logging to a file because there is no interactive console. Keep the environment and application at the registered paths.

5. On **Conditions**, clear the idle and network requirements, **Start the task only if the computer is on AC power**, **Stop if the computer switches to battery power**, and **Wake the computer to run this task**. Clear the battery-stop option before disabling its parent AC option if Windows greys it out. Use LaptopScheduler's per-job prerequisites when only particular jobs need network, AC, or idle time.
6. On **Settings**, enable **Allow task to be run on demand**. Set **If the task fails, restart every** to **1 minute**, with **3** restart attempts. Disable **Stop the task if it runs longer than** and automatic deletion of the task. For **If the task is already running**, choose **Do not start a new instance**.
7. Save the task. If you want to check it immediately, first stop any manually running copy of the same application, then right-click the task and choose **Run**. Check your application's logs or job history; Task Scheduler's **Running** state alone does not prove jobs are succeeding.

These settings correspond to Microsoft's [logon trigger](https://learn.microsoft.com/en-us/windows/win32/taskschd/starting-an-executable-when-a-user-logs-on) and [task settings](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings). The task starts after login, not while the laptop is powered off or signed out.

Use one startup registration per application. If switching from the Python helper, stop the application and call `uninstall_startup()` with the same `app_id` and database before creating the manual task. This retains your ledger. Manually created tasks are managed in Task Scheduler; `startup_status()` and `uninstall_startup()` do not adopt them. To remove manual startup, disable or delete your chosen task there. Removing a registration does not by itself stop an already running Python process.

## API

`LaptopScheduler(app_id, timezone=None, database=None, *, keep_awake=True, event_retention=timedelta(days=90))`

`app_id` accepts 1–64 ASCII letters, digits, underscores, or hyphens and must start with a letter or digit. The default database is `%LOCALAPPDATA%\LaptopScheduler\<app_id>\ledger.sqlite3`; custom paths become absolute. Use a local disk, not a network share or a live cloud-synced database. Without an explicit timezone, the system's IANA timezone is resolved and saved on first initialization. Explicit configuration overrides that saved value.

Construction acquires application and database ownership, initializes the ledger, and recovers abandoned attempts. A second instance owning either identity is rejected. Call `shutdown()` even when using only setup or inspection methods. Each instance has one lifecycle; create a new instance after shutdown. Register the same job IDs on every launch, before `run()`.

`cron()`, `interval()`, and `date()` require `id=` and forward their remaining trigger arguments to the matching [APScheduler 3.x trigger](https://apscheduler.readthedocs.io/en/3.x/userguide.html). Trigger timezone overrides are supported. Decorators return the original function.

The library has its own public API; it is not a drop-in replacement for APScheduler. Use the decorators and methods documented here rather than APScheduler's `add_job()` or `start()` methods.

Shared job options:

| Option | Default | Meaning |
|---|---|---|
| `catch_up` | `"latest"` | `latest`, `skip`, or `all` |
| `max_lateness` | seven days | Maximum age at execution; seconds or `timedelta` |
| `retries` | `(60, 300)` | Delays after failed attempts; seconds or timedeltas. `()` disables retries |
| `overlap` | `"skip"` | Only supported overlap policy; skip arriving live occurrences while that job has pending/running work |
| `priority` | `0` | Lower integer values run first; then oldest scheduled time |
| `requires_network` | `False` | Require Windows to report Internet connectivity |
| `requires_ac` | `False` | Require confirmed AC power |
| `idle_seconds` | `0` | Minimum time since this session's last user input |
| `args`, `kwargs` | `()`, `{}` | JSON-compatible registered arguments, copied when registered and queued |

Async callables, duplicate IDs, invalid policies, non-JSON arguments, and trigger jitter are rejected. Numeric durations must be finite and nonnegative; `max_lateness` must be positive. Three default attempts means the initial attempt plus two retries. Prerequisites that fail or cannot be determined defer work without consuming an attempt. Internet connectivity does not guarantee a particular website will respond; those failures use normal retries.

| Method | Result / behavior |
|---|---|
| `run()` | Blocks; reconciles and runs work. Ordinary job exceptions remain in history; infrastructure/SQLite failures stop claims and propagate |
| `shutdown(wait=True, timeout=30)` | Stops new claims, waits up to the timeout for active work |
| `run_job(id)` | Durably queues a manual invocation, returns its unique run ID; execution requires `run()` |
| `pause_job(id)` / `resume_job(id)` | Holds scheduled work and suppresses the paused period; manual runs still work |
| `jobs()` | Tuple of frozen `JobSnapshot` objects, including undeclared jobs marked disabled |
| `history(job_id=None, limit=100)` | Tuple of frozen `RunSnapshot` objects, newest creation first |
| `install_startup(module, working_directory)` | Installs/verifies a current-user logon task; returns `StartupStatus` |
| `uninstall_startup()` / `startup_status()` | Removes the owned task / reads task state; returns `StartupStatus` |

Use control and inspection methods from your application, including another thread while `run()` blocks. A separate process cannot open a second scheduler for a running app. Unknown IDs raise `KeyError` in control methods. `history()` for an unknown ID returns an empty tuple.

## Catch-up and schedule changes

Catch-up applies on first registration, within `max_lateness`, as well as after downtime:

| Policy | Behavior |
|---|---|
| `latest` | Queues the newest eligible missed occurrence. Intermediate cron scan results are never published |
| `skip` | Discards pre-start missed occurrences. While running, dispatch can be up to 30 seconds late, bounded by `max_lateness` |
| `all` | Replays oldest eligible occurrences first, at most 100 outstanding per job, replenishing as work finishes |

For example, if an hourly job misses 9, 10, and 11 a.m. while the laptop is off and starts again at 11:15: `latest` queues the 11 a.m. run, `all` queues all three in order, and `skip` waits for noon. Missed runs outside `max_lateness` are not replayed.

An interval without `start_date` starts at registration and has no invented historical runs. Its anchor survives restarts. A date without `run_date` also retains its original registration time. Catch-up is reconciled when `run()` starts, using the registered schedules. Manual invocations have separate identities, no lateness expiry, and never advance schedule progress. They bypass pause/overlap suppression but still use prerequisites and retries.

Committed pending work and interrupted attempts survive independently of the catch-up policy. A delayed retry does not block other ready work. Queued work is checked for expiration before execution. With `all`, multiple accumulated due occurrences form a replay batch even after a short gap between scheduler checks. Replay batches may queue while that job already has pending work; a single live arrival still follows overlap suppression. Explicit manual runs are not limited to 100.

Pausing holds existing queued scheduled work, without interrupting an active call. Resuming keeps that work but does not backfill the paused period. Expiration still applies. Schedule changes take effect prospectively and preserve queued occurrences/history. Queued arguments, priority, retry delays, and expiry remain those captured when queued; execution uses the current registered function and prerequisites. Removing a declaration disables that job without deleting its history or pending work.

Times use UTC identities, including distinct instances of a DST fold. Calendar behavior follows APScheduler and the selected timezone; a DST gap can shift a wall-clock trigger. Test critical local-time schedules. The dependency floor includes [APScheduler's DST fixes](https://apscheduler.readthedocs.io/en/3.x/versionhistory.html).

## Snapshots and persistence

Snapshot timestamps are timezone-aware UTC `datetime` values. Optional timestamps are `None` when not applicable. All snapshot classes are exported from `laptopscheduler`.

| Snapshot | Fields |
|---|---|
| `JobSnapshot` | `id`, `trigger` (human-readable string), `timezone`, `catch_up`, `paused`, `enabled`, `priority`, `interval_anchor`, `next_scheduled_time`, `reconciliation_target`, `outstanding` |
| `RunSnapshot` | `run_id`, `job_id`, `scheduled_time`, `manual`, `outcome`, `attempts`, `created_at`, `started_at`, `finished_at`, `next_attempt_at`, `expires_at`, `error`, `events` |
| `EventSnapshot` | `time`, `kind`, `attempt`, `error` |
| `StartupStatus` | `registered`, `task_name`, `owned`, `python`, `arguments`, `working_directory`, `state` |

`next_scheduled_time` is the next unscanned occurrence, possibly in the past during catch-up. `reconciliation_target` is the persisted endpoint of an unfinished scan/batch. `outstanding` counts pending/running occurrences, including manual runs. Run outcomes are `pending`, `running`, `succeeded`, `failed`, `expired`, or `skipped`. Event kinds also include `queued`, `retry`, and `interrupted`. `started_at` is the latest attempt's start; `finished_at` is set only for terminal outcomes. `next_attempt_at` is meaningful only for pending work. Errors contain exception tracebacks or an interruption explanation.

SQLite commits an occurrence and schedule progress together, and an outcome and its event together. Claims increment attempts before invoking user code. Recovery occurs only after exclusive Windows ownership is acquired; there is no lease that could mistake a slow or sleeping live worker for a dead process. Recorded successes stay recorded. Events for terminal runs expire after 90 days by default; pending/running events and compact occurrence rows remain, preserving deduplication and history. The ledger therefore grows with occurrences. Back it up while the scheduler is shut down, or use SQLite's backup API.

Schema version 1 initializes a new database transactionally. Unknown schema versions and unrelated/unversioned databases are rejected without migration. Use a dedicated database for each application.

## Delivery guarantee and operational limits

Eligible work can be attempted again after interruption, within its retry budget and lateness limit. External side effects can therefore repeat: a crash after writing a result but before recording success cannot be distinguished from an unfinished call. Use idempotent writes, unique destination keys, or destination-side deduplication. This is not exactly-once execution, nor an unlimited eventual-delivery guarantee.

Give HTTP requests, browser navigation, and subprocesses explicit timeouts. A hung Python function cannot be safely killed by this worker. A shutdown timeout leaves the call alive and keeps ownership until its thread exits; Python's non-daemon worker can keep the process alive. If the process is forcibly terminated, the committed running occurrence is recoverable on the next launch. A worker that finishes after the timeout still records its outcome.

Keep-awake runs on the worker thread and restores its previous state after outcome persistence, including failures. It [prevents idle sleep](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate), not explicit sleep or lid-close actions, and does not keep the display on. Wake timers and forced return to sleep are deferred.

Use Windows Credential Manager or your application's secret provider for credentials. Registered arguments and error text are stored unencrypted in the local ledger; avoid embedding credentials or tokens in either. Protect the data directory with normal per-user Windows permissions. User code chooses its own logging; the library does not configure global logging.

Deferred from v1: a CLI, async jobs, concurrent workers, hard execution timeouts, dashboards, and non-Windows runtime support.

## Testing status

Version 1.0.0 passed 73 automated tests on each of Python 3.10–3.14 on Windows, using installed wheels. Tests include actual process-termination recovery and native Task Scheduler registration, conflict handling, and rollback.

The current tree passed 82 tests on Python 3.11. The Windows/runtime tests also passed on Python 3.10 and 3.14, including launching a registered task with no console window. Saved manual reports confirm crash recovery, unchanged earlier successes, and the three catch-up policies across a short restart gap.

Manual checks involving signing out and back in, shutting down and powering on, and sleeping/resuming a physical laptop have not yet been performed. These check the full interaction with Windows and the laptop's power settings. See the [verification record](https://github.com/seaweed8888/laptop-schedueler/blob/main/docs/release.md) for procedures and results.

The [manual hardware test helper](https://github.com/seaweed8888/laptop-schedueler/blob/main/docs/hardware-testing.md) creates an isolated test app, saves evidence across restarts, and checks that old successes remain unchanged. It also exercises crash recovery, prerequisites, and a bounded keep-awake job. Run it from a source checkout; it is not part of the library's public API.

## Development

```powershell
.venv\Scripts\python -m pip install -e '.[dev]'
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests tools
.venv\Scripts\python -m ruff format --check src tests tools
.venv\Scripts\python -m build
.venv\Scripts\python -m twine check dist/*
```

Run development commands from a source checkout. Native task tests create uniquely named temporary tasks and remove them in `finally`. Opt in with `$env:LAPTOPSCHEDULER_NATIVE_TESTS='1'`. Windows CI is configured to test Python 3.10–3.14, build the sdist/wheel, and run the suite against the installed package.
