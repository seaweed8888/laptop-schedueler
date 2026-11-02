from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Outcome = Literal["pending", "running", "succeeded", "failed", "expired", "skipped"]


@dataclass(frozen=True)
class EventSnapshot:
    time: datetime
    kind: str
    attempt: int
    error: str | None


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    job_id: str
    scheduled_time: datetime
    manual: bool
    outcome: Outcome
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    next_attempt_at: datetime
    expires_at: datetime | None
    error: str | None
    events: tuple[EventSnapshot, ...]


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    trigger: str
    timezone: str
    catch_up: str
    paused: bool
    enabled: bool
    priority: int
    interval_anchor: datetime | None
    next_scheduled_time: datetime | None
    reconciliation_target: datetime | None
    outstanding: int


@dataclass(frozen=True)
class StartupStatus:
    registered: bool
    task_name: str
    owned: bool = False
    python: str | None = None
    arguments: str | None = None
    working_directory: str | None = None
    state: str | None = None
