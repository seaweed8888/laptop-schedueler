from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from laptopscheduler.storage import as_datetime


@pytest.mark.parametrize("policy,count", [("latest", 1), ("skip", 0), ("all", 6)])
def test_first_registration_catchup(make_scheduler, clock, policy, count):
    s = make_scheduler()
    s.interval(
        id="job", minutes=1, start_date=as_datetime(clock.now - 300), catch_up=policy
    )(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    assert len(s.history()) == count
    if policy == "latest":
        assert s.history()[0].scheduled_time.timestamp() == clock.now


def test_interval_anchor_stable_and_no_invented_history(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", minutes=1, catch_up="all")(lambda: None)
    anchor = s.jobs()[0].interval_anchor
    s._prepare(clock.now)
    s._reconcile(clock.now)
    assert len(s.history()) == 1
    s._execute_one()
    s.shutdown()
    clock.advance(150)
    s = make_scheduler()
    s.interval(id="job", minutes=1, catch_up="all")(lambda: None)
    assert s.jobs()[0].interval_anchor == anchor
    s._prepare(clock.now)
    s._reconcile(clock.now)
    assert len(s.history()) == 3


def test_all_batches_replenish_without_losing_tail(make_scheduler, clock):
    s = make_scheduler()
    s.interval(
        id="job", seconds=1, start_date=as_datetime(clock.now - 250), catch_up="all"
    )(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    assert s.jobs()[0].outstanding == 100
    assert len(s.history(limit=1000)) == 100
    for _ in range(251):
        assert s._execute_one()
        s._reconcile(clock.now)
        assert s.jobs()[0].outstanding <= 100
    assert not s._execute_one()
    assert len(s.history(limit=1000)) == 251
    assert all(r.outcome == "succeeded" for r in s.history(limit=1000))


def test_expiration_rechecked_before_execution(make_scheduler, clock):
    s = make_scheduler()
    s.date(id="job", run_date=as_datetime(clock.now), max_lateness=60)(
        lambda: pytest.fail("expired job ran")
    )
    s._reconcile(clock.now)
    clock.advance(61)
    assert s._execute_one()
    assert s.history()[0].outcome == "expired"
    assert s.history()[0].attempts == 0


def test_latest_cron_scan_is_bounded_and_persisted(make_scheduler, clock, monkeypatch):
    monkeypatch.setattr("laptopscheduler.scheduler.SCAN_LIMIT", 10)
    s = make_scheduler()
    options = dict(id="job", second="*", max_lateness=35)
    s.cron(**options)(lambda: None)
    assert s._reconcile(clock.now)
    assert not s.history()
    scan = s.jobs()[0]
    assert scan.reconciliation_target is not None
    s.shutdown()
    s = make_scheduler()
    s.cron(**options)(lambda: None)
    assert s.jobs()[0].next_scheduled_time == scan.next_scheduled_time
    while s._reconcile(clock.now):
        assert not s.history()
    assert len(s.history()) == 1
    assert s.history()[0].scheduled_time.timestamp() == clock.now


def test_high_frequency_latest_interval_uses_arithmetic(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=0.01, start_date=as_datetime(clock.now - 86400))(
        lambda: None
    )
    assert not s._reconcile(clock.now)
    assert len(s.history()) == 1
    assert s.history()[0].scheduled_time.timestamp() == pytest.approx(clock.now)


def test_clock_backward_never_replays_and_forward_catches_up(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=60)(lambda: None)
    s._reconcile(clock.now)
    s._execute_one()
    clock.advance(-3600)
    s._reconcile(clock.now)
    assert len(s.history()) == 1
    clock.advance(7200)
    s._reconcile(clock.now)
    assert len(s.history()) == 2
    assert s.history()[0].scheduled_time.timestamp() == clock.now


def test_pause_holds_pending_suppresses_period_and_allows_manual(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="all")(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    pending = s.history()[0].run_id
    s.pause_job("job")
    clock.advance(600)
    s._reconcile(clock.now)
    assert not s._execute_one()
    s.run_job("job")
    assert s._execute_one()
    s.resume_job("job")
    s._reconcile(clock.now)
    assert s._execute_one()
    assert len(s.history()) == 2
    assert next(r for r in s.history() if r.run_id == pending).outcome == "succeeded"


def test_changes_prospective_and_missing_declarations_disabled(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", minutes=1)(lambda: None)
    s._reconcile(clock.now)
    s.shutdown()
    clock.advance(600)
    s = make_scheduler()
    assert not s.jobs()[0].enabled
    assert not s._execute_one()
    s.interval(id="job", minutes=2)(lambda: None)
    s._reconcile(clock.now)
    assert len(s.history()) == 1
    assert s.jobs()[0].next_scheduled_time.timestamp() == clock.now + 120
    assert s._execute_one()


def test_live_overlap_skip_and_dispatch_grace(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="skip")(lambda: None)
    s._prepare(clock.now)
    clock.advance(65)
    s._reconcile(clock.now)
    assert s.history()[0].outcome == "pending"
    clock.advance(60)
    s._reconcile(clock.now)
    assert s.history()[0].outcome == "skipped"
    clock.advance(100)
    s._reconcile(clock.now)
    assert len(s.history()) == 2


def test_dst_fold_has_distinct_utc_occurrences(make_scheduler, clock):
    clock.now = datetime(2026, 11, 1, 11, tzinfo=timezone.utc).timestamp()
    s = make_scheduler(timezone="America/Los_Angeles")
    s.cron(id="fold", hour=1, minute=30, catch_up="all", max_lateness=4 * 3600)(
        lambda: None
    )
    s._prepare(clock.now)
    s._reconcile(clock.now)
    times = sorted(r.scheduled_time for r in s.history())
    assert times == [datetime(2026, 11, 1, h, 30, tzinfo=timezone.utc) for h in (8, 9)]


def test_dst_gap_uses_apscheduler_rules_and_interval_utc_spacing(make_scheduler, clock):
    clock.now = datetime(2026, 3, 8, 11, tzinfo=timezone.utc).timestamp()
    s = make_scheduler(timezone="America/Los_Angeles")
    s.cron(id="gap", hour=2, minute=30, catch_up="all", max_lateness=4 * 3600)(
        lambda: None
    )
    s.interval(
        id="interval",
        minutes=30,
        catch_up="all",
        start_date=as_datetime(clock.now - 7200),
    )(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(hour=2, minute=30, timezone=ZoneInfo("America/Los_Angeles"))
    expected = trigger.get_next_fire_time(None, as_datetime(clock.now - 14400))
    assert [r.scheduled_time for r in s.history("gap")] == [
        expected.astimezone(timezone.utc)
    ]
    times = sorted(r.scheduled_time for r in s.history("interval"))
    assert all(b - a == timedelta(minutes=30) for a, b in zip(times, times[1:]))


def test_default_timezone_persisted_explicit_override(make_scheduler, monkeypatch):
    monkeypatch.setattr(
        "laptopscheduler.scheduler.get_localzone_name", lambda: "Asia/Tokyo"
    )
    s = make_scheduler()
    assert str(s.timezone) == "Asia/Tokyo"
    s.shutdown()
    monkeypatch.setattr("laptopscheduler.scheduler.get_localzone_name", lambda: "UTC")
    s = make_scheduler()
    assert str(s.timezone) == "Asia/Tokyo"
    s.shutdown()
    s = make_scheduler(timezone="Europe/London")
    assert str(s.timezone) == "Europe/London"


@pytest.mark.parametrize(
    "options",
    [
        {"catch_up": "bad"},
        {"overlap": "allow"},
        {"jitter": None},
        {"max_lateness": -1},
        {"retries": [float("nan")]},
        {"args": [object()]},
        {"kwargs": {1: "bad"}},
        {"requires_network": "yes"},
    ],
)
def test_invalid_declarations(make_scheduler, options):
    with pytest.raises((ValueError, TypeError)):
        make_scheduler().date(id="bad", **options)(lambda: None)


def test_async_duplicate_and_decorator_identity(make_scheduler):
    s = make_scheduler()

    def function():
        pass

    assert s.date(id="job")(function) is function
    with pytest.raises(ValueError, match="Duplicate"):
        s.date(id="job")(function)

    async def async_function():
        pass

    with pytest.raises(ValueError, match="synchronous"):
        s.date(id="async")(async_function)


def test_sleep_gap_all_replays_whole_window_and_replenishes(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=1, catch_up="all")(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    s._execute_one()
    clock.advance(130)
    s._reconcile(clock.now)
    assert s.jobs()[0].outstanding == 100
    for _ in range(130):
        assert s._execute_one()
        s._reconcile(clock.now)
    assert len(s.history(limit=1000)) == 131
    assert all(r.outcome == "succeeded" for r in s.history(limit=1000))


def test_skip_restart_preserves_committed_pending(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="skip")(lambda: None)
    s._prepare(clock.now)
    clock.advance(60)
    s._reconcile(clock.now)
    pending = s.history()[0].run_id
    s.shutdown()
    clock.advance(600)
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="skip")(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    assert s._execute_one()
    assert len(s.history()) == 1
    assert s.history()[0].run_id == pending


def test_pause_survives_restart_without_backfill(make_scheduler, clock):
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="all")(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    s.pause_job("job")
    s.shutdown()
    clock.advance(600)
    s = make_scheduler()
    s.interval(id="job", seconds=60, catch_up="all")(lambda: None)
    s._prepare(clock.now)
    assert s.jobs()[0].paused
    assert not s._execute_one()
    s.resume_job("job")
    s._reconcile(clock.now)
    assert len(s.history()) == 1
    assert s._execute_one()


def test_date_without_explicit_time_keeps_original_anchor(make_scheduler, clock):
    s = make_scheduler()
    s.date(id="job")(lambda: None)
    scheduled = s.jobs()[0].next_scheduled_time
    s.shutdown()
    clock.advance(600)
    s = make_scheduler()
    s.date(id="job")(lambda: None)
    s._reconcile(clock.now)
    assert s.history()[0].scheduled_time == scheduled
    assert s._execute_one()


def test_replay_expiration_drains_and_replenishes(make_scheduler, clock):
    s = make_scheduler()
    s.interval(
        id="job",
        seconds=1,
        start_date=as_datetime(clock.now - 120),
        catch_up="all",
        max_lateness=120,
    )(lambda: None)
    s._prepare(clock.now)
    s._reconcile(clock.now)
    clock.advance(200)
    for _ in range(100):
        assert s._execute_one()
    assert all(r.outcome == "expired" for r in s.history(limit=1000))
    s._reconcile(clock.now)
    s._reconcile(clock.now)
    assert s.jobs()[0].outstanding == 100
    assert all(
        r.scheduled_time.timestamp() >= clock.now - 120
        for r in s.history(limit=1000)
        if r.outcome == "pending"
    )
