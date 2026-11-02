"""Declarations, bounded reconciliation, and a single durable worker."""

import inspect
import json
import math
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta, tzinfo
from datetime import timezone as utc_timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tzlocal import get_localzone_name

from .models import JobSnapshot, RunSnapshot, StartupStatus
from .storage import Store, as_datetime
from .windows import Ownership, Windows, startup

UTC = utc_timezone.utc
SCAN_LIMIT = 1000
BATCH_SIZE = 100


@dataclass
class Job:
    id: str
    function: object
    trigger: object
    catch_up: str
    max_lateness: float
    retries: tuple[float, ...]
    priority: int
    requires_network: bool
    requires_ac: bool
    idle_seconds: float
    args_json: str
    kwargs_json: str


def _seconds(value, name, *, positive=False):
    if isinstance(value, timedelta):
        value = value.total_seconds()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be seconds or timedelta")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"Invalid {name}")
    return float(value)


def _json(value):
    def validate(item):
        if type(item) in (str, int, float, bool, type(None)):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError("Registered arguments must be JSON-compatible")

    validate(value)
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _schedule_value(value):
    if isinstance(value, (datetime, date_type)):
        return value.isoformat()
    if isinstance(value, tzinfo):
        return str(value)
    raise TypeError(f"Unsupported trigger argument: {value!r}")


class LaptopScheduler:
    """Own one app/database until shutdown. Declare jobs before calling run()."""

    def __init__(
        self,
        app_id: str,
        timezone: str | None = None,
        database: str | Path | None = None,
        *,
        keep_awake: bool = True,
        event_retention: timedelta = timedelta(days=90),
    ):
        if not isinstance(app_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", app_id
        ):
            raise ValueError(
                "app_id must be 1–64 ASCII letters, digits, underscores or hyphens"
            )
        if type(keep_awake) is not bool:
            raise ValueError("keep_awake must be boolean")
        self.event_retention = _seconds(
            event_retention, "event_retention", positive=True
        )
        timezone_name = timezone or get_localzone_name()
        ZoneInfo(timezone_name)
        self.app_id = app_id
        if database is None:
            database = (
                Path(os.environ["LOCALAPPDATA"])
                / "LaptopScheduler"
                / app_id
                / "ledger.sqlite3"
            )
        self.database = Path(database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._ownership = Ownership(app_id, self.database)
        self._store = None
        try:
            self._store = Store(self.database, app_id, timezone_name)
            with self._store.transaction() as db:
                if timezone is not None:
                    db.execute(
                        "UPDATE metadata SET value=? WHERE key='timezone'", (timezone,)
                    )
                db.execute(
                    "INSERT OR IGNORE INTO metadata VALUES ('startup_token',?)",
                    (uuid.uuid4().hex,),
                )
            self.timezone = ZoneInfo(self._store.meta("timezone"))
            self._windows = Windows()
            self._store.recover(time.time())
        except BaseException:
            if self._store is not None:
                self._store.close()
            self._ownership.close()
            raise
        self.keep_awake = keep_awake
        self._jobs: dict[str, Job] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._work = threading.Event()
        self._lifecycle = threading.RLock()
        self._worker = None
        self._aps = None
        self._running = False
        self._closed = False
        self._fatal = None
        self._store.on_error = self._fail

    def cron(self, *, id: str, **options):
        return self._decorator("cron", id, **options)

    def interval(self, *, id: str, **options):
        return self._decorator("interval", id, **options)

    def date(self, *, id: str, **options):
        return self._decorator("date", id, **options)

    def _decorator(
        self,
        kind,
        id,
        *,
        catch_up="latest",
        max_lateness=timedelta(days=7),
        retries=(60, 300),
        overlap="skip",
        priority=0,
        requires_network=False,
        requires_ac=False,
        idle_seconds=0,
        args=(),
        kwargs=None,
        **trigger_args,
    ):
        if not isinstance(id, str) or not id or len(id) > 200:
            raise ValueError(
                "Job id must be a nonempty string of at most 200 characters"
            )
        if catch_up not in ("latest", "skip", "all") or overlap != "skip":
            raise ValueError(
                "Invalid catch_up or overlap policy (only overlap='skip' is supported)"
            )
        if "jitter" in trigger_args:
            raise ValueError("Trigger jitter is not supported")
        if type(priority) is not int:
            raise ValueError("priority must be an integer")
        if type(requires_network) is not bool or type(requires_ac) is not bool:
            raise ValueError("Prerequisite flags must be boolean")
        lateness = _seconds(max_lateness, "max_lateness", positive=True)
        retry_seconds = tuple(_seconds(delay, "retry delay") for delay in retries)
        idle = _seconds(idle_seconds, "idle_seconds")
        if not isinstance(args, (list, tuple)) or (
            kwargs is not None and type(kwargs) is not dict
        ):
            raise ValueError("args must be a list/tuple; kwargs must be a JSON object")
        args_json, kwargs_json = (
            _json(list(args)),
            _json({} if kwargs is None else kwargs),
        )

        def register(function):
            with self._lifecycle:
                self._check_open()
                if self._running:
                    raise RuntimeError("Declare jobs before run()")
                if id in self._jobs:
                    raise ValueError(f"Duplicate job id: {id}")
                if not callable(function) or any(
                    predicate(target)
                    for predicate in (
                        inspect.iscoroutinefunction,
                        inspect.isasyncgenfunction,
                    )
                    for target in (function, getattr(function, "__call__", None))
                ):
                    raise ValueError("Jobs must be synchronous callables")
                inspect.signature(function).bind(
                    *json.loads(args_json), **json.loads(kwargs_json)
                )
                now = time.time()
                options = dict(trigger_args)
                options.setdefault("timezone", self.timezone)
                fingerprint = json.dumps(
                    [kind, options], default=_schedule_value, sort_keys=True
                )
                with self._store.transaction() as db:
                    old = db.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone()
                    changed = old is not None and old["fingerprint"] != fingerprint
                    anchor = old["anchor"] if old is not None and not changed else now
                    if kind == "interval" and options.get("start_date") is None:
                        options["start_date"] = as_datetime(anchor)
                    if kind == "date" and options.get("run_date") is None:
                        options["run_date"] = as_datetime(anchor)
                    trigger = {
                        "cron": CronTrigger,
                        "interval": IntervalTrigger,
                        "date": DateTrigger,
                    }[kind](**options)
                    if kind == "interval":
                        anchor = trigger.start_date.timestamp()
                    cursor = self._next(
                        trigger, now + 0.000001 if changed else now - lateness
                    )
                    if old is None:
                        db.execute(
                            """INSERT INTO jobs(id,fingerprint,trigger,timezone,anchor,cursor,catch_up,priority)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (
                                id,
                                fingerprint,
                                str(trigger),
                                str(options["timezone"]),
                                anchor if kind in ("interval", "date") else None,
                                cursor,
                                catch_up,
                                priority,
                            ),
                        )
                    elif changed:
                        db.execute(
                            """UPDATE jobs SET fingerprint=?,trigger=?,timezone=?,anchor=?,cursor=?,
                            scan_target=NULL,candidate=NULL,enabled=1,catch_up=?,priority=? WHERE id=?""",
                            (
                                fingerprint,
                                str(trigger),
                                str(options["timezone"]),
                                anchor if kind in ("interval", "date") else None,
                                cursor,
                                catch_up,
                                priority,
                                id,
                            ),
                        )
                    else:
                        db.execute(
                            "UPDATE jobs SET enabled=1,catch_up=?,priority=? WHERE id=?",
                            (catch_up, priority, id),
                        )
                        if old["catch_up"] != catch_up:
                            # Revisit unfinished scan, so changing policy cannot discard its candidate.
                            if old["candidate"] is not None:
                                db.execute(
                                    "UPDATE jobs SET cursor=? WHERE id=?",
                                    (old["candidate"], id),
                                )
                            db.execute(
                                "UPDATE jobs SET candidate=NULL,scan_target=NULL WHERE id=?",
                                (id,),
                            )
                self._jobs[id] = Job(
                    id,
                    function,
                    trigger,
                    catch_up,
                    lateness,
                    retry_seconds,
                    priority,
                    requires_network,
                    requires_ac,
                    idle,
                    args_json,
                    kwargs_json,
                )
                return function

        return register

    @staticmethod
    def _next(trigger, lower):
        result = trigger.get_next_fire_time(None, as_datetime(lower))
        if result is None:
            return None
        stamp = result.timestamp()
        # DateTrigger ignores 'now'; a consumed date must not reopen.
        return stamp if stamp >= lower else None

    @staticmethod
    def _after(trigger, stamp):
        result = trigger.get_next_fire_time(
            as_datetime(stamp), as_datetime(stamp + 0.000001)
        )
        if result is None:
            return None
        if result.timestamp() <= stamp:
            raise RuntimeError("Trigger did not advance in UTC")
        return result.timestamp()

    def _check_open(self):
        if self._closed or self._stop.is_set():
            raise RuntimeError("Scheduler is shut down; create a new instance")

    def _advance(self, db, job, row, now):
        if row["cursor"] is not None:
            cursor = self._next(job.trigger, max(row["cursor"], now + 0.000001))
            db.execute(
                "UPDATE jobs SET cursor=?,scan_target=NULL,candidate=NULL WHERE id=?",
                (cursor, job.id),
            )

    def _prepare(self, now):
        with self._store.transaction() as db:
            for job in self._jobs.values():
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
                if job.catch_up == "skip" or row["paused"]:
                    self._advance(db, job, row, now)
                elif (
                    job.catch_up == "all"
                    and row["cursor"] is not None
                    and row["cursor"] <= now
                ):
                    db.execute(
                        "UPDATE jobs SET scan_target=coalesce(scan_target,?) WHERE id=?",
                        (now, job.id),
                    )
        self._store.prune(now - self.event_retention)

    def _reconcile(self, now):
        """One bounded pass. Progress and queued occurrences share a transaction."""
        unfinished = False
        with self._store.transaction() as db:
            for job in self._jobs.values():
                row = db.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
                if row["paused"]:
                    self._advance(db, job, row, now)
                    continue
                cursor = row["cursor"]
                if cursor is None or cursor > now:
                    continue
                lower = now - (
                    min(30, job.max_lateness)
                    if job.catch_up == "skip"
                    else job.max_lateness
                )
                if cursor < lower:
                    cursor = self._next(job.trigger, lower)
                target = row["scan_target"] if row["scan_target"] is not None else now
                replay_batch = job.catch_up == "all" and (
                    row["scan_target"] is not None
                    or cursor is not None
                    and cursor < now - 30
                )
                if job.catch_up == "all" and not replay_batch and cursor is not None:
                    following = self._after(job.trigger, cursor)
                    # Multiple due occurrences are backlog, even after a short gap.
                    replay_batch = following is not None and following <= now
                candidate = row["candidate"]
                outstanding = self._store.outstanding(job.id)
                count = 0
                if (
                    job.catch_up == "latest"
                    and isinstance(job.trigger, IntervalTrigger)
                    and cursor is not None
                    and cursor <= target
                ):
                    step = job.trigger.interval.total_seconds()
                    candidate = cursor + math.floor((target - cursor) / step) * step
                    if job.trigger.end_date is not None:
                        end = job.trigger.end_date.timestamp()
                        candidate = (
                            cursor
                            + math.floor((min(target, end) - cursor) / step) * step
                        )
                    cursor = self._after(job.trigger, candidate)
                else:
                    while (
                        cursor is not None and cursor <= target and count < SCAN_LIMIT
                    ):
                        if replay_batch and outstanding >= BATCH_SIZE:
                            break
                        if job.catch_up == "latest":
                            candidate = cursor
                        else:
                            skipped = outstanding > 0 and not replay_batch
                            self._store.enqueue(job, cursor, now, skipped=skipped)
                            if not skipped:
                                outstanding += 1
                        cursor = self._after(job.trigger, cursor)
                        count += 1
                complete = cursor is None or cursor > target
                if job.catch_up == "latest" and complete:
                    if candidate is not None and candidate >= now - job.max_lateness:
                        self._store.enqueue(
                            job, candidate, now, skipped=outstanding > 0
                        )
                    candidate = None
                scan_target = (
                    target
                    if not complete and (job.catch_up == "latest" or replay_batch)
                    else None
                )
                db.execute(
                    "UPDATE jobs SET cursor=?,scan_target=?,candidate=? WHERE id=?",
                    (cursor, scan_target, candidate, job.id),
                )
                if not complete and (job.catch_up != "all" or outstanding < BATCH_SIZE):
                    unfinished = True
        self._work.set()
        return unfinished

    def _execute_one(self):
        with self._store.reading():
            candidates = self._store.db.execute(
                """SELECT r.* FROM runs r JOIN jobs j ON j.id=r.job_id
                WHERE r.state='pending' AND (r.due<=? OR r.expires<?) AND j.enabled=1 AND (j.paused=0 OR r.manual=1)
                ORDER BY r.priority,r.scheduled,r.rowid""",
                (time.time(), time.time()),
            ).fetchall()
        readiness = {}
        for candidate in candidates:
            if self._stop.is_set():
                return False
            job = self._jobs[candidate["job_id"]]
            now = time.time()
            if candidate["expires"] is not None and now > candidate["expires"]:
                with self._store.transaction() as db:
                    if db.execute(
                        "UPDATE runs SET state='expired',finished=? WHERE id=? AND state='pending'",
                        (now, candidate["id"]),
                    ).rowcount:
                        self._store.event(
                            candidate["id"], now, "expired", candidate["attempts"]
                        )
                self._wake.set()
                return True
            if job.id not in readiness:
                readiness[job.id] = self._windows.ready(job)
            if not readiness[job.id]:
                continue
            previous = self._windows.awake() if self.keep_awake else None
            try:
                with self._store.transaction() as db:
                    if self._stop.is_set():
                        return False
                    row = db.execute(
                        """SELECT r.* FROM runs r JOIN jobs j ON j.id=r.job_id
                        WHERE r.id=? AND r.state='pending' AND (j.paused=0 OR r.manual=1)""",
                        (candidate["id"],),
                    ).fetchone()
                    if row is None:
                        continue
                    now = time.time()
                    if row["due"] > now:
                        continue
                    if row["expires"] is not None and now > row["expires"]:
                        continue
                    db.execute(
                        "UPDATE runs SET state='running',attempts=attempts+1,started=? WHERE id=?",
                        (now, row["id"]),
                    )
                    self._store.event(row["id"], now, "running", row["attempts"] + 1)
                    row = db.execute(
                        "SELECT * FROM runs WHERE id=?", (row["id"],)
                    ).fetchone()
                error = None
                try:
                    result = job.function(
                        *json.loads(row["args"]), **json.loads(row["kwargs"])
                    )
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise TypeError(
                            "Job returned an awaitable; async execution is unsupported"
                        )
                except Exception:
                    error = traceback.format_exc()
                with self._store.transaction():
                    self._store.finish(row, time.time(), error)
            finally:
                if previous is not None:
                    self._windows.restore(previous)
            self._wake.set()
            return True
        return False

    def _worker_loop(self):
        try:
            while not self._stop.is_set():
                self._work.clear()
                if not self._execute_one():
                    now = time.time()
                    with self._store.reading() as db:
                        due = db.execute(
                            "SELECT min(due) FROM runs WHERE state='pending' AND due>?",
                            (now,),
                        ).fetchone()[0]
                    self._work.wait(
                        min(30, max(0, due - now)) if due is not None else 30
                    )
        except BaseException as error:
            self._fail(error)
        finally:
            with self._lifecycle:
                if not self._running:
                    self._close()

    def _fail(self, error):
        if self._fatal is None:
            self._fatal = error
        self._stop.set()
        self._wake.set()
        self._work.set()

    def run(self):
        """Block until shutdown; propagate storage/platform failures to the caller."""
        with self._lifecycle:
            self._check_open()
            if self._running:
                raise RuntimeError("Scheduler is already running")
            self._running = True
        try:
            self._prepare(time.time())
            self._aps = BackgroundScheduler(timezone=UTC)
            self._aps.start()
            with self._lifecycle:
                self._worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"LaptopScheduler-{self.app_id}",
                    daemon=False,
                )
                self._worker.start()
            pruned_at = time.monotonic()
            while not self._stop.is_set():
                self._wake.clear()
                now = time.time()
                if self._reconcile(now):
                    continue
                if time.monotonic() - pruned_at >= 86400:
                    self._store.prune(now - self.event_retention)
                    pruned_at = time.monotonic()
                with self._store.reading():
                    next_time = self._store.db.execute(
                        "SELECT min(cursor) FROM jobs WHERE enabled=1 AND paused=0 AND cursor>?",
                        (now,),
                    ).fetchone()[0]
                deadline = (
                    min(now + 30, next_time) if next_time is not None else now + 30
                )
                self._aps.add_job(
                    self._wake.set,
                    "date",
                    run_date=as_datetime(deadline),
                    id="notification",
                    replace_existing=True,
                    misfire_grace_time=None,
                )
                self._wake.wait(30)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            with self._lifecycle:
                self._running = False
            self.shutdown()
        if self._fatal is not None:
            raise self._fatal

    def shutdown(self, wait: bool = True, timeout: float = 30):
        """Stop claims. A timed-out worker retains ownership until it exits."""
        timeout = _seconds(timeout, "timeout")
        with self._lifecycle:
            with self._store.lock:
                self._stop.set()
            self._wake.set()
            self._work.set()
            aps = None
            if not self._running:
                aps, self._aps = self._aps, None
        if aps is not None:
            aps.shutdown(wait=False)
        worker = self._worker
        if wait and worker is not None and worker is not threading.current_thread():
            worker.join(timeout)
        with self._lifecycle:
            if not self._running and (worker is None or not worker.is_alive()):
                self._close()

    def _close(self):
        if not self._closed:
            self._store.close()
            self._ownership.close()
            self._closed = True

    def run_job(self, id: str) -> str:
        self._check_open()
        job = self._jobs[id]
        with self._store.transaction():
            now = time.time()
            run_id = self._store.enqueue(job, now, now, manual=True)
        self._work.set()
        self._wake.set()
        return run_id

    def _pause(self, id, paused):
        self._check_open()
        job = self._jobs[id]
        with self._store.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (id,)).fetchone()
            if bool(row["paused"]) != paused:
                self._advance(db, job, row, time.time())
                db.execute("UPDATE jobs SET paused=? WHERE id=?", (paused, id))
        self._wake.set()
        self._work.set()

    def pause_job(self, id: str):
        self._pause(id, True)

    def resume_job(self, id: str):
        self._pause(id, False)

    def jobs(self) -> tuple[JobSnapshot, ...]:
        self._check_open()
        with self._store.reading():
            return tuple(
                JobSnapshot(
                    id=r["id"],
                    trigger=r["trigger"],
                    timezone=r["timezone"],
                    catch_up=r["catch_up"],
                    paused=bool(r["paused"]),
                    enabled=bool(r["enabled"]),
                    priority=r["priority"],
                    interval_anchor=as_datetime(r["anchor"])
                    if r["trigger"].startswith("interval")
                    else None,
                    next_scheduled_time=as_datetime(r["cursor"]),
                    reconciliation_target=as_datetime(r["scan_target"]),
                    outstanding=self._store.outstanding(r["id"]),
                )
                for r in self._store.db.execute(
                    "SELECT * FROM jobs ORDER BY priority,id"
                )
            )

    def history(
        self, job_id: str | None = None, limit: int = 100
    ) -> tuple[RunSnapshot, ...]:
        self._check_open()
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        return self._store.history(job_id, limit)

    def install_startup(
        self, module: str, working_directory: str | Path
    ) -> StartupStatus:
        self._check_open()
        return startup(
            "install",
            self.app_id,
            self._store.meta("startup_token"),
            module,
            working_directory,
        )

    def uninstall_startup(self) -> StartupStatus:
        self._check_open()
        return startup("uninstall", self.app_id, self._store.meta("startup_token"))

    def startup_status(self) -> StartupStatus:
        self._check_open()
        return startup("status", self.app_id, self._store.meta("startup_token"))
