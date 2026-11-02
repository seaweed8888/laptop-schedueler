"""SQLite ledger. Callers hold transaction() across related mutations."""

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import EventSnapshot, RunSnapshot

SCHEMA_VERSION = 1
SCHEMA = (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """CREATE TABLE jobs (
        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, trigger TEXT NOT NULL,
        timezone TEXT NOT NULL, anchor REAL, cursor REAL, scan_target REAL,
        candidate REAL, paused INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1, catch_up TEXT NOT NULL, priority INTEGER NOT NULL
    )""",
    """CREATE TABLE runs (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
        scheduled REAL NOT NULL, manual INTEGER NOT NULL,
        state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        created REAL NOT NULL, started REAL, finished REAL,
        due REAL NOT NULL, expires REAL, error TEXT,
        args TEXT NOT NULL, kwargs TEXT NOT NULL, retries TEXT NOT NULL,
        priority INTEGER NOT NULL
    )""",
    "CREATE UNIQUE INDEX occurrence ON runs(job_id, scheduled) WHERE manual = 0",
    "CREATE INDEX ready ON runs(state, due, priority)",
    """CREATE TABLE events (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
        time REAL NOT NULL, kind TEXT NOT NULL, attempt INTEGER NOT NULL, error TEXT
    )""",
    "CREATE INDEX run_events ON events(run_id, id)",
)


def as_datetime(value):
    return None if value is None else datetime.fromtimestamp(value, timezone.utc)


class Store:
    def __init__(self, path: Path, app_id: str, timezone_name: str):
        self.lock = threading.RLock()
        self.on_error = None
        self.db = sqlite3.connect(
            path, timeout=5, check_same_thread=False, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            with self.transaction() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchone():
                        raise ValueError("Refusing an unversioned, nonempty database")
                    for statement in SCHEMA:
                        db.execute(statement)
                    db.executemany(
                        "INSERT INTO metadata VALUES (?, ?)",
                        [("app_id", app_id), ("timezone", timezone_name)],
                    )
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise ValueError(
                        f"Unsupported database schema {version}; expected {SCHEMA_VERSION}"
                    )
                if self.meta("app_id") != app_id:
                    raise ValueError("Database belongs to another application")
                db.execute("UPDATE jobs SET enabled=0")
            self.db.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def reading(self):
        with self.lock:
            try:
                yield self.db
            except sqlite3.Error as error:
                if self.on_error is not None:
                    self.on_error(error)
                raise

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    yield self.db
                    self.db.execute("COMMIT")
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise
            except sqlite3.Error as error:
                if self.on_error is not None:
                    self.on_error(error)
                raise

    def meta(self, key):
        with self.reading():
            return self.db.execute(
                "SELECT value FROM metadata WHERE key=?", (key,)
            ).fetchone()[0]

    def event(self, run_id, now, kind, attempt=0, error=None):
        self.db.execute(
            "INSERT INTO events(run_id,time,kind,attempt,error) VALUES (?,?,?,?,?)",
            (run_id, now, kind, attempt, error),
        )

    def enqueue(self, job, scheduled, now, *, manual=False, skipped=False):
        run_id = uuid.uuid4().hex
        state = "skipped" if skipped else "pending"
        inserted = self.db.execute(
            """INSERT INTO runs(id,job_id,scheduled,manual,state,created,finished,due,
                expires,args,kwargs,retries,priority)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
            (
                run_id,
                job.id,
                scheduled,
                manual,
                state,
                now,
                now if skipped else None,
                now,
                None if manual else scheduled + job.max_lateness,
                job.args_json,
                job.kwargs_json,
                json.dumps(job.retries),
                job.priority,
            ),
        ).rowcount
        if inserted:
            self.event(run_id, now, "skipped" if skipped else "queued")
            return run_id
        return self.db.execute(
            "SELECT id FROM runs WHERE job_id=? AND scheduled=? AND manual=0",
            (job.id, scheduled),
        ).fetchone()[0]

    def outstanding(self, job_id):
        return self.db.execute(
            "SELECT count(*) FROM runs WHERE job_id=? AND state IN ('pending','running')",
            (job_id,),
        ).fetchone()[0]

    def finish(self, row, now, error=None, *, interrupted=False):
        retries = json.loads(row["retries"])
        retry = error is not None and row["attempts"] <= len(retries)
        state = "pending" if retry else ("succeeded" if error is None else "failed")
        due = now + retries[row["attempts"] - 1] if retry else row["due"]
        self.db.execute(
            "UPDATE runs SET state=?,due=?,finished=?,error=? WHERE id=?",
            (state, due, None if retry else now, error, row["id"]),
        )
        if interrupted:
            self.event(row["id"], now, "interrupted", row["attempts"], error)
        self.event(row["id"], now, "retry" if retry else state, row["attempts"], error)

    def recover(self, now):
        with self.transaction() as db:
            for row in db.execute(
                "SELECT * FROM runs WHERE state='running'"
            ).fetchall():
                self.finish(
                    row,
                    now,
                    "Process interrupted before outcome commit",
                    interrupted=True,
                )

    def history(self, job_id, limit):
        with self.reading():
            rows = self.db.execute(
                "SELECT * FROM runs WHERE (? IS NULL OR job_id=?) ORDER BY created DESC,rowid DESC LIMIT ?",
                (job_id, job_id, limit),
            ).fetchall()
            return tuple(
                RunSnapshot(
                    run_id=r["id"],
                    job_id=r["job_id"],
                    scheduled_time=as_datetime(r["scheduled"]),
                    manual=bool(r["manual"]),
                    outcome=r["state"],
                    attempts=r["attempts"],
                    created_at=as_datetime(r["created"]),
                    started_at=as_datetime(r["started"]),
                    finished_at=as_datetime(r["finished"]),
                    next_attempt_at=as_datetime(r["due"]),
                    expires_at=as_datetime(r["expires"]),
                    error=r["error"],
                    events=tuple(
                        EventSnapshot(
                            as_datetime(e["time"]), e["kind"], e["attempt"], e["error"]
                        )
                        for e in self.db.execute(
                            "SELECT * FROM events WHERE run_id=? ORDER BY id",
                            (r["id"],),
                        )
                    ),
                )
                for r in rows
            )

    def prune(self, before):
        with self.transaction() as db:
            db.execute(
                """DELETE FROM events WHERE time < ? AND run_id IN
                (SELECT id FROM runs WHERE state NOT IN ('pending','running'))""",
                (before,),
            )

    def close(self):
        with self.lock:
            self.db.close()
