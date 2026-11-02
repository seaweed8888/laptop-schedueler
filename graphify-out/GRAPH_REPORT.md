# Graph Report - laptop-schedueler  (2026-11-01)

## Corpus Check
- 14 files · ~10,126 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 170 nodes · 353 edges · 11 communities (10 shown, 1 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 62 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f7bf77ff`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- LaptopScheduler
- make_scheduler
- LaptopScheduler
- test_ledger.py
- Store
- test_runtime.py
- Windows
- test_windows.py
- startup.ps1
- laptopscheduler
- 1.0.0 release verification

## God Nodes (most connected - your core abstractions)
1. `make_scheduler()` - 50 edges
2. `LaptopScheduler` - 35 edges
3. `Store` - 19 edges
4. `as_datetime()` - 16 edges
5. `declare()` - 14 edges
6. `enqueue()` - 11 edges
7. `Windows` - 10 edges
8. `StartupStatus` - 9 edges
9. `LaptopScheduler` - 9 edges
10. `RunSnapshot` - 8 edges

## Surprising Connections (you probably didn't know these)
- `test_unversioned_nonempty_database_is_not_migrated()` --uses--> `Store`  [INFERRED]
  tests/test_ledger.py → src/laptopscheduler/storage.py
- `test_native_task_registration_idempotency_conflict_unicode_and_removal()` --calls--> `startup()`  [INFERRED]
  tests/test_runtime.py → src/laptopscheduler/windows.py
- `declare()` --calls--> `as_datetime()`  [EXTRACTED]
  tests/test_ledger.py → src/laptopscheduler/storage.py
- `test_delayed_retry_does_not_block_other_work()` --calls--> `as_datetime()`  [EXTRACTED]
  tests/test_ledger.py → src/laptopscheduler/storage.py
- `test_public_enqueue_failure_stops_claims()` --calls--> `make_scheduler()`  [INFERRED]
  tests/test_ledger.py → tests/conftest.py

## Import Cycles
- None detected.

## Communities (11 total, 1 thin omitted)

### Community 0 - "LaptopScheduler"
Cohesion: 0.12
Nodes (9): StartupStatus, _json(), LaptopScheduler, One bounded pass. Progress and queued occurrences share a transaction., Block until shutdown; propagate storage/platform failures to the caller., Stop claims. A timed-out worker retains ownership until it exits., Own one app/database until shutdown. Declare jobs before calling run()., _seconds() (+1 more)

### Community 1 - "make_scheduler"
Cohesion: 0.14
Nodes (29): fixture, parametrize, as_datetime(), clock(), make_scheduler(), test_all_batches_replenish_without_losing_tail(), test_all_replays_accumulated_occurrences_after_short_gap(), test_all_still_skips_single_live_occurrence_when_busy() (+21 more)

### Community 2 - "LaptopScheduler"
Cohesion: 0.20
Nodes (9): API, Catch-up and schedule changes, Delivery guarantee and operational limits, Development, Install and run, LaptopScheduler, Snapshots and persistence, Start after login (+1 more)

### Community 3 - "test_ledger.py"
Cohesion: 0.23
Nodes (18): declare(), enqueue(), test_backward_clock_during_probe_does_not_claim_early(), test_delayed_retry_does_not_block_other_work(), test_duplicate_notifications_and_success_survive_restart(), test_enqueue_progress_rolls_back_together(), test_final_interrupted_attempt_exhausts_budget(), test_keep_awake_cleanup_after_outcome_and_on_database_failure() (+10 more)

### Community 4 - "Store"
Cohesion: 0.15
Nodes (9): Durable scheduled jobs for Windows laptops., EventSnapshot, JobSnapshot, RunSnapshot, Job, Declarations, bounded reconciliation, and a single durable worker., Path, SQLite ledger. Callers hold transaction() across related mutations. (+1 more)

### Community 5 - "test_runtime.py"
Cohesion: 0.18
Nodes (12): integration, skipif, test_actual_runtime_executes_sequentially(), test_database_contention_stops_without_claim(), test_forced_process_death_recovers_committed_claim(), test_native_task_registration_idempotency_conflict_unicode_and_removal(), test_ownership_for_same_app_different_database_and_same_database(), test_priority_lower_first() (+4 more)

### Community 6 - "Windows"
Cohesion: 0.16
Nodes (8): Path, Ownership, powershell(), Windows ownership, power/prerequisite probes, and Task Scheduler bridge., Kernel object existence owns app + database across sessions, without leases., require_windows(), Windows, timedelta

### Community 7 - "test_windows.py"
Cohesion: 0.29
Nodes (5): integration, skipif, test_native_registration_verification_failure_rolls_back(), test_power_prerequisites_fail_closed(), test_startup_input_is_json_not_shell_code()

### Community 8 - "startup.ps1"
Cohesion: 0.60
Nodes (3): Get-Sid(), Test-Configuration(), Test-Owned()

### Community 10 - "1.0.0 release verification"
Cohesion: 0.33
Nodes (5): 1.0.0 release verification, Automated verification, Build and publish, Known limits, Laptop acceptance still required

## Knowledge Gaps
- **14 isolated node(s):** `laptopscheduler`, `Job`, `Install and run`, `Start after login`, `API` (+9 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 37 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LaptopScheduler` connect `LaptopScheduler` to `Store`, `Windows`?**
  _High betweenness centrality (0.214) - this node is a cross-community bridge._
- **Why does `make_scheduler()` connect `make_scheduler` to `test_ledger.py`, `test_runtime.py`, `test_windows.py`?**
  _High betweenness centrality (0.192) - this node is a cross-community bridge._
- **Why does `Store` connect `Store` to `LaptopScheduler`, `test_ledger.py`, `Windows`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Are the 48 inferred relationships involving `make_scheduler()` (e.g. with `test_backward_clock_during_probe_does_not_claim_early()` and `test_delayed_retry_does_not_block_other_work()`) actually correct?**
  _`make_scheduler()` has 48 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `LaptopScheduler` (e.g. with `JobSnapshot` and `RunSnapshot`) actually correct?**
  _`LaptopScheduler` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Store` (e.g. with `LaptopScheduler` and `EventSnapshot`) actually correct?**
  _`Store` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `laptopscheduler`, `Job`, `Install and run` to the rest of the system?**
  _14 weakly-connected nodes found - possible documentation gaps or missing edges._