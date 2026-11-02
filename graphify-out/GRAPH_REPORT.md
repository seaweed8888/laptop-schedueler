# Graph Report - laptop-schedueler  (2026-11-01)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 150 nodes · 332 edges · 10 communities (8 shown, 2 thin omitted)
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 63 edges (avg confidence: 0.86)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9

## God Nodes (most connected - your core abstractions)
1. `make_scheduler()` - 46 edges
2. `LaptopScheduler` - 40 edges
3. `Store` - 19 edges
4. `as_datetime()` - 15 edges
5. `declare()` - 14 edges
6. `enqueue()` - 11 edges
7. `Windows` - 10 edges
8. `StartupStatus` - 9 edges
9. `RunSnapshot` - 8 edges
10. `startup()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `test_unversioned_nonempty_database_is_not_migrated()` --uses--> `Store`  [INFERRED]
  tests/test_ledger.py → src/laptopscheduler/storage.py
- `test_forced_process_death_recovers_committed_claim()` --calls--> `LaptopScheduler`  [INFERRED]
  tests/test_runtime.py → src/laptopscheduler/scheduler.py
- `test_native_task_registration_idempotency_conflict_unicode_and_removal()` --calls--> `LaptopScheduler`  [INFERRED]
  tests/test_runtime.py → src/laptopscheduler/scheduler.py
- `test_ownership_for_same_app_different_database_and_same_database()` --calls--> `LaptopScheduler`  [INFERRED]
  tests/test_runtime.py → src/laptopscheduler/scheduler.py
- `test_native_registration_verification_failure_rolls_back()` --calls--> `LaptopScheduler`  [INFERRED]
  tests/test_windows.py → src/laptopscheduler/scheduler.py

## Import Cycles
- None detected.

## Communities (10 total, 2 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.12
Nodes (8): _json(), LaptopScheduler, One bounded pass. Progress and queued occurrences share a transaction., Block until shutdown; propagate storage/platform failures to the caller., Stop claims. A timed-out worker retains ownership until it exits., Own one app/database until shutdown. Declare jobs before calling run()., _seconds(), test_schema_and_database_ownership()

### Community 1 - "Community 1"
Cohesion: 0.15
Nodes (25): fixture, parametrize, as_datetime(), clock(), make_scheduler(), test_all_batches_replenish_without_losing_tail(), test_async_duplicate_and_decorator_identity(), test_changes_prospective_and_missing_declarations_disabled() (+17 more)

### Community 2 - "Community 2"
Cohesion: 0.16
Nodes (14): Durable scheduled jobs for Windows laptops., EventSnapshot, JobSnapshot, RunSnapshot, StartupStatus, Job, Declarations, bounded reconciliation, and a single durable worker., SQLite ledger. Callers hold transaction() across related mutations. (+6 more)

### Community 3 - "Community 3"
Cohesion: 0.25
Nodes (17): declare(), enqueue(), test_backward_clock_during_probe_does_not_claim_early(), test_delayed_retry_does_not_block_other_work(), test_duplicate_notifications_and_success_survive_restart(), test_enqueue_progress_rolls_back_together(), test_final_interrupted_attempt_exhausts_budget(), test_keep_awake_cleanup_after_outcome_and_on_database_failure() (+9 more)

### Community 5 - "Community 5"
Cohesion: 0.18
Nodes (12): integration, skipif, test_actual_runtime_executes_sequentially(), test_database_contention_stops_without_claim(), test_forced_process_death_recovers_committed_claim(), test_native_task_registration_idempotency_conflict_unicode_and_removal(), test_ownership_for_same_app_different_database_and_same_database(), test_priority_lower_first() (+4 more)

### Community 6 - "Community 6"
Cohesion: 0.24
Nodes (3): Path, Windows, timedelta

### Community 7 - "Community 7"
Cohesion: 0.29
Nodes (5): integration, skipif, test_native_registration_verification_failure_rolls_back(), test_power_prerequisites_fail_closed(), test_startup_input_is_json_not_shell_code()

### Community 8 - "Community 8"
Cohesion: 0.60
Nodes (3): Get-Sid(), Test-Configuration(), Test-Owned()

## Knowledge Gaps
- **2 isolated node(s):** `laptopscheduler`, `Job`
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 23 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `LaptopScheduler` connect `Community 0` to `Community 2`, `Community 4`, `Community 5`, `Community 6`, `Community 7`?**
  _High betweenness centrality (0.380) - this node is a cross-community bridge._
- **Why does `make_scheduler()` connect `Community 1` to `Community 0`, `Community 3`, `Community 5`, `Community 7`?**
  _High betweenness centrality (0.253) - this node is a cross-community bridge._
- **Why does `Store` connect `Community 4` to `Community 0`, `Community 2`, `Community 3`, `Community 6`?**
  _High betweenness centrality (0.161) - this node is a cross-community bridge._
- **Are the 44 inferred relationships involving `make_scheduler()` (e.g. with `test_backward_clock_during_probe_does_not_claim_early()` and `test_delayed_retry_does_not_block_other_work()`) actually correct?**
  _`make_scheduler()` has 44 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `LaptopScheduler` (e.g. with `JobSnapshot` and `RunSnapshot`) actually correct?**
  _`LaptopScheduler` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Store` (e.g. with `LaptopScheduler` and `EventSnapshot`) actually correct?**
  _`Store` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `laptopscheduler`, `Job` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._