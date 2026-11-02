import time
import uuid

import pytest

from laptopscheduler import LaptopScheduler


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 1_800_000_000.0

        def advance(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(time, "time", lambda: clock.now)
    return clock


@pytest.fixture
def make_scheduler(tmp_path):
    schedulers = []
    apps = {}

    def make(name="default", **kwargs):
        app_id = apps.setdefault(name, "test-" + uuid.uuid4().hex)
        scheduler = LaptopScheduler(
            app_id, database=tmp_path / (name + ".sqlite3"), **kwargs
        )
        scheduler._windows.ready = lambda job: True
        schedulers.append(scheduler)
        return scheduler

    yield make
    for scheduler in schedulers:
        scheduler.shutdown(timeout=1)
