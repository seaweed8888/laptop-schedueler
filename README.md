# LaptopScheduler

Durable Python jobs for Windows laptops. Scheduled occurrences, attempts, and outcomes live in SQLite, so restarting Python does not erase unfinished work or replay recorded successes.

**1.0.0 is prepared for release, not yet published or hardware-accepted.** See [release verification](docs/release.md).

## Install and run

Requires Windows and Python 3.10 or newer. From this checkout:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install .
```

Put this in `my_scrapers.py`:

```python
from laptopscheduler import LaptopScheduler

scheduler = LaptopScheduler(
    app_id="my-scrapers",
    timezone="America/Los_Angeles",
)

@scheduler.cron(
    id="daily-scrape",
    hour=9,
    catch_up="latest",
    requires_network=True,
)
def scrape():
    # Fetch with explicit timeouts; write results idempotently.
    print("Scrape started")

if __name__ == "__main__":
    scheduler.run()
```

Run `.venv\Scripts\python -m my_scrapers`. `run()` blocks; Ctrl+C requests shutdown. One worker runs synchronous functions sequentially. Return values are discarded.

## Start after login

Run this once, using the same virtual environment, while the scheduler is stopped:

```python
from my_scrapers import scheduler

try:
    print(scheduler.install_startup(
        module="my_scrapers",
        working_directory=r"C:\Scrapers",
    ))
finally:
    scheduler.shutdown()
```

The explicit setup call registers `LaptopScheduler-my-scrapers` for the current user's next logon. It uses this interpreter's exact path, `-m my_scrapers`, and the supplied absolute working directory. Keep that environment and module installed at those paths. Registration is idempotent. Different paths/settings or another owner's task cause a conflict; uninstall an owned task before changing its configuration.

The task runs with normal privileges, ignores duplicate instances, allows battery operation, and has no execution limit. On process failure it retries three times, one minute apart. It has no wake timer or task-wide network/idle condition. `startup_status()` reports native task state and configured paths; native `running` does not establish worker health. `uninstall_startup()` removes only the owned task and retains SQLite data.

Windows must launch Python; Python cannot launch itself after reboot. This helper uses a [current-user logon trigger](https://learn.microsoft.com/en-us/windows/win32/taskschd/starting-an-executable-when-a-user-logs-on). It does not start before login or wake a powered-off laptop.

## API

`LaptopScheduler(app_id, timezone=None, database=None, *, keep_awake=True, event_retention=timedelta(days=90))`

`app_id` accepts 1–64 ASCII letters, digits, underscores, or hyphens and must start with a letter or digit. The default database is `%LOCALAPPDATA%\LaptopScheduler\<app_id>\ledger.sqlite3`; custom paths become absolute. Use a local disk, not a network share or a live cloud-synced database. Without an explicit timezone, the system's IANA timezone is resolved and saved on first initialization. Explicit configuration overrides that saved value.

Construction acquires application and database ownership, initializes the ledger, and recovers abandoned attempts. A second instance owning either identity is rejected. Call `shutdown()` even when using only setup or inspection methods. Each instance has one lifecycle; create a new instance after shutdown. Register the same job IDs on every launch, before `run()`.

`cron()`, `interval()`, and `date()` require `id=` and forward their remaining trigger arguments to the matching [APScheduler 3.x trigger](https://apscheduler.readthedocs.io/en/3.x/userguide.html). Trigger timezone overrides are supported. Decorators return the original function.

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
| `skip` | Discards pre-start missed occurrences. While running, ordinary dispatch can be up to 30 seconds late |
| `all` | Replays oldest eligible occurrences first, at most 100 outstanding per job, replenishing as work finishes |

An interval without `start_date` starts at registration and has no invented historical runs. Its anchor survives restarts. A date without `run_date` also retains its original registration time. Catch-up is reconciled when `run()` starts, using the registered schedules. Manual invocations have separate identities, no lateness expiry, and never advance schedule progress. They bypass pause/overlap suppression but still use prerequisites and retries.

Committed pending work and interrupted attempts survive independently of the catch-up policy. A delayed retry does not block other ready work. Queued work is checked for expiration before execution. `all` intentionally permits its catch-up batch even when that job already has pending work; arriving live occurrences still follow overlap suppression. Explicit manual runs are not limited to 100.

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

Schema version 1 initializes a new database transactionally. Unknown schema versions and unrelated/unversioned databases are rejected without migration. No Repost Bot database migration is provided, and no baseline repository is modified.

## Delivery guarantee and operational limits

Eligible work can be attempted again after interruption, within its retry budget and lateness limit. External side effects can therefore repeat: a crash after a website write but before the success commit cannot be distinguished from an unfinished call. Use idempotent writes, unique destination keys, or destination-side deduplication. This is not exactly-once execution, nor an unlimited eventual-delivery guarantee.

Give scraper HTTP requests, browser navigation, and subprocesses explicit timeouts. A hung Python function cannot be safely killed by this worker. A shutdown timeout leaves the call alive and keeps ownership until its thread exits; Python's non-daemon worker can keep the process alive. If the process is forcibly terminated, the committed running occurrence is recoverable on the next launch. A worker that finishes after the timeout still records its outcome.

Keep-awake runs on the worker thread and restores its previous state after outcome persistence, including failures. It [prevents idle sleep](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate), not explicit sleep or lid-close actions, and does not keep the display on. Wake timers and forced return to sleep are deferred.

Use Windows Credential Manager or your application's secret provider for credentials. Registered arguments and error text are stored unencrypted in the local ledger; avoid embedding credentials or tokens in either. Protect the data directory with normal per-user Windows permissions. User code chooses its own logging; the library does not configure global logging.

Deferred from v1: a CLI, async jobs, concurrent workers, hard execution timeouts, dashboards, and non-Windows runtime support.

## Development

```powershell
.venv\Scripts\python -m pip install -e '.[dev]'
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m ruff format --check src tests
.venv\Scripts\python -m build
.venv\Scripts\python -m twine check dist/*
```

Native task tests create uniquely named temporary tasks and remove them in `finally`. Opt in with `$env:LAPTOPSCHEDULER_NATIVE_TESTS='1'`. Windows CI tests Python 3.10–3.14, builds the sdist/wheel, installs the wheel, and runs the suite against the installed package. Hardware acceptance and publishing instructions are in [docs/release.md](docs/release.md).
