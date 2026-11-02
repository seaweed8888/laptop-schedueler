import sqlite3
from datetime import timedelta

import pytest

from laptopscheduler.storage import as_datetime


def declare(scheduler, function=lambda: None, **options):
    return scheduler.date(id="job", run_date=as_datetime(1_800_000_000), **options)(
        function
    )


def enqueue(s, clock):
    s._prepare(clock.now)
    s._reconcile(clock.now)


def test_duplicate_notifications_and_success_survive_restart(make_scheduler, clock):
    calls = []
    s = make_scheduler()
    declare(s, lambda: calls.append(1))
    enqueue(s, clock)
    with s._store.transaction():
        s._store.enqueue(s._jobs["job"], clock.now, clock.now)
    assert len(s.history()) == 1
    assert s._execute_one()
    successful_id = s.history()[0].run_id
    s.shutdown()
    s = make_scheduler()
    declare(s, lambda: calls.append(1))
    enqueue(s, clock)
    assert not s._execute_one()
    assert s.history()[0].run_id == successful_id
    assert s.history()[0].outcome == "succeeded"
    assert calls == [1]


def test_enqueue_progress_rolls_back_together(make_scheduler, clock, monkeypatch):
    s = make_scheduler()
    declare(s)
    before = s.jobs()[0].next_scheduled_time
    monkeypatch.setattr(
        s._store,
        "event",
        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
    )
    with pytest.raises(sqlite3.OperationalError):
        s._reconcile(clock.now)
    assert s._stop.is_set()
    assert (
        s._store.db.execute("SELECT cursor FROM jobs").fetchone()[0]
        == before.timestamp()
    )
    assert not s._store.history(None, 100)


def test_outcome_event_is_atomic_and_recoverable(make_scheduler, clock, monkeypatch):
    s = make_scheduler()
    declare(s)
    enqueue(s, clock)
    original = s._store.event

    def fail_outcome(run_id, now, kind, *args):
        if kind == "succeeded":
            raise sqlite3.OperationalError("disk full")
        return original(run_id, now, kind, *args)

    monkeypatch.setattr(s._store, "event", fail_outcome)
    with pytest.raises(sqlite3.OperationalError):
        s._execute_one()
    assert s._store.history(None, 100)[0].outcome == "running"
    assert s._store.history(None, 100)[0].attempts == 1
    s.shutdown()
    s = make_scheduler()
    declare(s)
    recovered = s.history()[0]
    assert recovered.outcome == "pending"
    assert recovered.next_attempt_at.timestamp() == clock.now + 60
    assert "interrupted" in [event.kind for event in recovered.events]


def test_retry_deadline_budget_and_no_duplicate_reset(make_scheduler, clock):
    def fail():
        raise ValueError("website failed")

    s = make_scheduler()
    declare(s, fail)
    enqueue(s, clock)
    assert s._execute_one()
    first = s.history()[0]
    assert first.attempts == 1 and first.outcome == "pending"
    with s._store.transaction():
        s._store.enqueue(s._jobs["job"], clock.now, clock.now)
    assert s.history()[0].next_attempt_at == first.next_attempt_at
    s.shutdown()
    s = make_scheduler()
    declare(s, fail)
    assert not s._execute_one()
    clock.advance(60)
    assert s._execute_one()
    assert s.history()[0].attempts == 2
    clock.advance(299)
    assert not s._execute_one()
    clock.advance(1)
    assert s._execute_one()
    assert s.history()[0].outcome == "failed"
    assert s.history()[0].attempts == 3
    assert "website failed" in s.history()[0].error
    assert not s._execute_one()


def test_final_interrupted_attempt_exhausts_budget(make_scheduler, clock):
    s = make_scheduler()
    declare(s)
    enqueue(s, clock)
    with s._store.transaction() as db:
        db.execute("UPDATE runs SET state='running',attempts=3")
    s.shutdown()
    s = make_scheduler()
    declare(s)
    assert s.history()[0].outcome == "failed"
    assert not s._execute_one()


def test_manual_identity_does_not_advance_schedule_and_args_snapshot(
    make_scheduler, clock
):
    seen = []
    s = make_scheduler()
    args = [{"x": [1]}]
    declare(s, lambda value: seen.append(value), args=args)
    args[0]["x"].append(2)
    before = s.jobs()[0].next_scheduled_time
    ids = {s.run_job("job"), s.run_job("job")}
    assert len(ids) == 2
    assert s.jobs()[0].next_scheduled_time == before
    assert s._execute_one() and s._execute_one()
    assert seen == [{"x": [1]}, {"x": [1]}]
    assert all(r.manual and r.expires_at is None for r in s.history())


def test_retention_preserves_dedup_and_pending_events(make_scheduler, clock):
    s = make_scheduler()
    declare(s)
    enqueue(s, clock)
    s._execute_one()
    s.run_job("job")
    clock.advance(timedelta(days=100).total_seconds())
    s._store.prune(clock.now - 90 * 86400)
    history = s.history()
    assert history[0].outcome == "pending" and history[0].events
    assert history[1].outcome == "succeeded" and not history[1].events
    with s._store.transaction():
        s._store.enqueue(s._jobs["job"], 1_800_000_000, clock.now)
    assert len(s.history()) == 2


def test_schema_and_database_ownership(make_scheduler, clock):
    s = make_scheduler()
    path = s.database
    app = s.app_id
    s.shutdown()
    from laptopscheduler import LaptopScheduler

    with pytest.raises(ValueError, match="another application"):
        LaptopScheduler(app + "x", database=path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="schema 999"):
        make_scheduler()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_prerequisites_do_not_consume_attempts(make_scheduler, clock):
    s = make_scheduler()
    declare(s, requires_network=True, requires_ac=True, idle_seconds=60)
    enqueue(s, clock)
    s._windows.ready = lambda job: False
    assert not s._execute_one()
    assert s.history()[0].attempts == 0
    s._windows.ready = lambda job: True
    assert s._execute_one()


def test_keep_awake_cleanup_after_outcome_and_on_database_failure(
    make_scheduler, clock, monkeypatch
):
    s = make_scheduler()
    declare(s)
    enqueue(s, clock)
    states = []
    s._windows.awake = lambda: 0x80000000
    s._windows.restore = lambda previous: states.append(
        s._store.history(None, 100)[0].outcome
    )
    assert s._execute_one()
    assert states == ["succeeded"]
    s.run_job("job")
    monkeypatch.setattr(
        s._store,
        "finish",
        lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("full")),
    )
    with pytest.raises(sqlite3.OperationalError):
        s._execute_one()
    assert states[-1] == "running"


def test_delayed_retry_does_not_block_other_work(make_scheduler, clock):
    s = make_scheduler()
    declare(s, lambda: (_ for _ in ()).throw(ValueError("bad")))
    enqueue(s, clock)
    s._execute_one()
    done = []
    s.date(id="other", run_date=as_datetime(clock.now))(lambda: done.append(1))
    s._reconcile(clock.now)
    assert s._execute_one()
    assert done == [1]


def test_unversioned_nonempty_database_is_not_migrated(tmp_path):
    from laptopscheduler.storage import Store

    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE baseline(value TEXT)")
    with pytest.raises(ValueError, match="unversioned"):
        Store(path, "test", "UTC")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("baseline",)]


def test_public_enqueue_failure_stops_claims(make_scheduler):
    s = make_scheduler()
    with s._store.transaction() as db:
        db.execute("DROP TABLE events")
    s.date(id="job")(lambda: None)
    with pytest.raises(sqlite3.OperationalError):
        s.run_job("job")
    assert s._stop.is_set()


def test_public_read_failure_stops_claims(make_scheduler):
    s = make_scheduler()
    s.date(id="job")(lambda: None)
    s.run_job("job")
    with s._store.transaction() as db:
        db.execute("DROP TABLE events")
    with pytest.raises(sqlite3.OperationalError):
        s.history()
    assert s._stop.is_set()


def test_backward_clock_during_probe_does_not_claim_early(make_scheduler, clock):
    s = make_scheduler()
    declare(s)
    enqueue(s, clock)

    def ready(job):
        clock.advance(-60)
        return True

    s._windows.ready = ready
    assert not s._execute_one()
    assert s.history()[0].attempts == 0


def test_return_value_is_not_serialized(make_scheduler, clock):
    s = make_scheduler()
    declare(s, lambda: object())
    enqueue(s, clock)
    assert s._execute_one()
    assert s.history()[0].outcome == "succeeded"
