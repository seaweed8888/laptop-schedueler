# 1.0.0 release verification

The package is implemented. Publication and laptop hardware acceptance remain separate gates. This file records what was exercised, rather than treating CI as proof of sleep or logon behavior.

## Automated verification

Local verification runs on Windows. The suite covers:

- Atomic enqueue/progress and outcome/event writes, duplicate notifications, persistent retry budgets/deadlines, and success deduplication.
- A real subprocess killed while executing a job, followed by recovery, successful retry, and another restart without replay.
- First-registration catch-up, all three policies, 100-item batch replenishment, expiry while queued, bounded cron scans, and high-frequency interval arithmetic.
- Short-gap replay for interval and cron schedules, the 30-second boundary, replenishment through the last replay occurrence, live overlap suppression, and lateness limits shorter than the dispatch grace period.
- DST fold/gap behavior, stable interval/date anchors, clock changes, pause/resume across restart, prospective declaration changes, and disabled missing jobs.
- Same-process and cross-process ownership conflicts, SQLite write contention, failure propagation, schema initialization/rejection, and event retention without loss of deduplication.
- Prerequisite deferral, worker-thread keep-awake cleanup, sequential priority dispatch, and shutdown with a still-running function that retains ownership.
- Actual native startup registration/settings verification, quoted/Unicode paths, repeat installation, conflicting settings/owners, removal without deleting data, and rollback after injected verification failure.

Native task tests require `LAPTOPSCHEDULER_NATIVE_TESTS=1`; they create uniquely named temporary tasks and clean them up. They do not log the user out, reboot, suspend the laptop, or prove that a logon trigger launches Python.

Verified locally on Windows build 26200, with native task tests enabled. Each environment imported the installed wheel from `site-packages`, not the source checkout:

| Python | Result |
|---|---|
| 3.10.22 | 73 passed |
| 3.11.1 | 73 passed |
| 3.12.15 | 73 passed |
| 3.13.16 | 73 passed |
| 3.14.2 | 73 passed |

Wheel and sdist builds passed `twine check`. Wheel helper/typing resources and sdist documentation were checked. Ruff lint and formatting checks passed. Native network/AC/idle probes returned boolean results, without assuming a particular connectivity or power state. No temporary native-test or rollback-test tasks remained after the suite.

GitHub CI is configured to repeat this matrix but has not been run remotely in this session. Graphify's code graph was refreshed and checked: 170 nodes, 353 edges, no dangling endpoints. Documentation is not included in that code-only graph.

## Laptop acceptance still required

Use a disposable app ID and a local test database. Give the test job a short interval and write an idempotent destination record so duplicate effects can be observed. Save the IDs and UTC timestamps of several successful occurrences before each step.

| Check | Procedure and pass condition | Status |
|---|---|---|
| Real logon | Install startup from the intended venv, sign out/in, confirm the ledger gains a successful occurrence without manually starting Python | Not performed |
| Powered-off catch-up | Shut down across several due times; log in and verify `latest`, `skip`, and `all`, including a backlog over 100 | Not performed on hardware; deterministic scheduling tests cover the ledger behavior |
| Forced termination | Terminate the Python process during a job; restart and verify its original run ID, incremented attempt count, and eventual outcome | Passed with an actual subprocess in automated tests |
| Sleep/resume | Sleep across due times, resume, and verify catch-up without reclaiming a still-live worker | Not performed on hardware |
| Keep-awake | With ordinary idle-sleep settings, run a bounded long job and confirm idle sleep is held; after completion, confirm normal idle sleep resumes | Native API calls exercised; power-state behavior not hardware-validated |
| Old success stays terminal | After each scenario, verify earlier successful `(job_id, scheduled_time)` rows still have their original IDs/attempt counts and were not executed again | Automated restart/termination tests pass; repeat after hardware scenarios |

Record the Windows build, hardware/power settings, Python version, exact release commit, and observed run IDs when completing this table. Explicit sleep and lid closure may still suspend the laptop by design. Do not use production credentials or destinations for crash tests.

## Build and publish

Build from the reviewed commit:

```powershell
python -m pip install build twine
python -m build
python -m twine check dist/*
python -m venv .venv-wheel
.venv-wheel\Scripts\python -m pip install dist/laptopscheduler-1.0.0-py3-none-any.whl pytest
$env:LAPTOPSCHEDULER_NATIVE_TESTS='1'
.venv-wheel\Scripts\python -m pytest -q
```

`python -m build` builds the wheel from the generated source distribution. The wheel includes the fixed PowerShell helper and `py.typed` marker. The source distribution also includes tests and these instructions.

The public PyPI JSON endpoint returned HTTP 404 for `laptopscheduler` during this implementation session (2026-11-01 local date). That means no public project was visible then. It does **not** prove that the name is claimable, reserved for this account, or owned by the repository owner. No PyPI account ownership or publisher identity has been verified, and no package has been uploaded. Recheck the name when preparing publication.

Before publishing:

1. Complete the laptop acceptance table for the release commit.
2. In the intended PyPI account, confirm name eligibility and create a pending trusted publisher, or confirm owner rights on the existing project. Configure GitHub owner `seaweed8888`, repository `laptop-schedueler`, workflow `publish.yml`, and environment `pypi`.
3. Create the GitHub `pypi` environment with a required release reviewer. Set repository variable `PYPI_OWNERSHIP_VERIFIED=true` only after step 2 is complete.
4. Tag the reviewed commit `v1.0.0`. Dispatch **Publish accepted release** with that tag and `hardware_validated=true`.
5. The workflow runs the Windows version matrix against that ref, checks package metadata against the requested tag, and sends its wheel/sdist to PyPI using OIDC. Review the environment deployment before publication.

The publish workflow deliberately does not run on ordinary pushes. It needs no stored PyPI password or API token. See [PyPI's trusted-publisher documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/) for account-side configuration.

## Known limits

Eligible work may be attempted again after a crash, so external writes can repeat. Retry budgets and lateness limits can end recovery without success. A hung function cannot be safely terminated by Python's worker thread. Occurrence records are retained for deduplication and the database grows over time. Schema 1 creates a fresh application ledger; there is no migration from another application's database or an unsupported future schema.
