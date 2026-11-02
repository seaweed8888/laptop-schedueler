# Manual Windows acceptance tests

Use [tools/hardware_check.py](../tools/hardware_check.py) to collect evidence on your laptop. It creates an isolated application, SQLite database, and startup task. Jobs do no useful work and use no credentials. Its `crash` command deliberately terminates **only its own test worker**.

The helper never signs you out, changes your power settings, disconnects your network, sleeps, or shuts down Windows. Save your other work before doing those actions yourself. Do not cut power or hold the power button to test a crash; use the helper's `crash` command.

## Start once

From the repository root, use the Python environment containing the package you want to test. For the existing clean-wheel environment in this checkout:

```powershell
.\.venv-wheel\Scripts\Activate.ps1
python tools\hardware_check.py setup
```

If you use another environment, activate that instead. The helper tests its installed `laptopscheduler`, not necessarily the current source checkout. It records the package version, Python executable, Windows version, and script path. No additional dependencies are required.

`setup` registers a current-user logon task and starts a background worker immediately. Wait five seconds, then run:

```powershell
python tools\hardware_check.py report
```

You should see recent snapshots and successful `all`, `latest`, and `skip` jobs. The three jobs run every two seconds. The helper prints the evidence directory under `%LOCALAPPDATA%\LaptopSchedulerChecks\` and the exact task name. Startup uses the environment's `pythonw.exe`, so no terminal window opens at login. `worker.log` records worker failures, including those from Windows logon launches. For failures before the helper can initialize, also check Task Scheduler's history/last result.

If you installed a startup task with an older package version that used `python.exe`, upgrade the package in your test environment, run `uninstall`, then `setup` to register the console-free action. Keep your existing evidence directory; this does not delete it.

You can rename the script to another valid ASCII Python module name **before setup**. Substitute that path in the commands. Keep the script and virtual environment at their registered locations until you uninstall. The script's path determines its isolated application identity. Do not start two workers from the same path simultaneously, or run `setup` again while its worker is active. Send one control command at a time.

## Sign out and sign in

```powershell
python tools\hardware_check.py checkpoint signout
```

Save your work. Use Windows **Start → account menu → Sign out**. Stay signed out for at least one minute, then sign back into the same account. **Do not start the worker yourself.** Open PowerShell, activate the same environment, and run `report` again.

Pass conditions:

- The report is fresh, says `new process: True`, and records a restart gap.
- The `all` job has successes scheduled inside the downtime window.
- The report says the checkpoint's old successes are unchanged.

Locking the screen with Win+L does not test sign-out or the logon trigger.

## Powered-off catch-up, including more than 100 missed runs

```powershell
python tools\hardware_check.py checkpoint poweroff
```

Shut down normally. **Leave the laptop off for at least four minutes**, then turn it on and sign in. Wait about 15 seconds for catch-up before running `report`. The two-second interval produces more than 100 missed occurrences during four minutes off.

Expect a new process, unchanged old successes, and more than 100 `all` successes inside the downtime window. If work is still pending, wait and report again. Inspect `status.json` or the saved report for individual UTC epoch timestamps and run IDs:

- `all` should replay the eligible missed occurrences and drain its batches.
- `latest` should execute the newest eligible missed occurrence, then continue its live schedule.
- `skip` should resume with future occurrences and omit pre-start downtime occurrences.

The report excludes four seconds at each edge of a gap when counting interior occurrences because the heartbeat and scheduling threads run independently. `latest` and `skip` should normally show zero successes in that interior. That count alone does **not** prove correct handling of the newest edge occurrence; inspect the recorded rows. Automated tests cover the exact boundary rules.

## Sleep and resume

```powershell
python tools\hardware_check.py checkpoint sleep
```

Choose **Start → Power → Sleep**, wait at least a minute, then resume. Do not manually relaunch the helper. Run `report` after catch-up finishes.

Expect `new process: False`, an observed live-process gap, catch-up, and unchanged old successes. A gap establishes that the helper stopped making progress; it does not independently prove Windows slept. Note whether you actually observed sleep. If Windows hibernates or restarts instead, record that distinction.

Also test resuming an active job:

```powershell
python tools\hardware_check.py checkpoint sleep-during-job
python tools\hardware_check.py hold 180
```

Use `report` to confirm `hold` is `running` with one attempt, then explicitly sleep and resume. Let the job finish. Its run ID should stay the same and its attempt count should remain **1**. A live sleeping worker must not be recovered as though it crashed. Run `resume` afterwards to re-enable the interval jobs.

## Forced termination and recovery

With no hold job running:

```powershell
python tools\hardware_check.py crash
python tools\hardware_check.py report
```

The helper queues a job, records that it entered, exits its process abruptly, and starts a replacement worker. It should print `PASS` with the original run ID and **attempt 2**. The report should also confirm that old successful occurrences stayed unchanged. The command tests process termination; it does not simulate a disk losing power mid-write.

## Keep-awake and restoration of normal idle sleep

First note your existing Windows idle-sleep setting. For a short test you can temporarily choose a one-minute idle-sleep timeout for the current power source. Record any change and restore it afterwards. Other applications and system policies can also prevent sleep.

```powershell
python tools\hardware_check.py hold 180
```

The helper pauses its interval jobs, then runs a three-minute job. Confirm `hold` is `running`, then stop using the mouse and keyboard. The machine should remain awake while the job runs, although the display may turn off. After it completes, leave the machine idle long enough for the normal sleep timeout. It should now be allowed to sleep.

After waking, run `report`. The hold job should have succeeded on one attempt. Record your actual observations of staying awake and subsequent sleep; a successful job alone does not prove either. Explicit Sleep and lid closure can still suspend the machine during a job by design.

```powershell
python tools\hardware_check.py resume
```

`hold` temporarily pauses the interval jobs so their repeated wake requests do not interfere with the second half of this test. Existing queued jobs may finish before the hold starts. The worker is sequential: do not combine hold and prerequisite tests.

## Network, battery/AC, and user idle prerequisites

For each prerequisite, queue the job while the prerequisite is **unmet**. Then run `report` to confirm `pending` and **attempts=0**. Restore the condition, wait up to 45 seconds for another probe, and report again. Expect `succeeded` with **attempts=1** and the same run ID.

| Test | Before queueing | Command | Satisfy the prerequisite |
|---|---|---|---|
| Network | Disconnect all Internet connections, including Ethernet/VPN as applicable | `python tools\hardware_check.py probe network` | Reconnect to the Internet |
| AC | Unplug the charger on a laptop with sufficient battery | `python tools\hardware_check.py probe ac` | Reconnect the charger |
| Idle | Move the mouse or type | `python tools\hardware_check.py probe idle` | Leave mouse/keyboard untouched for at least 75 seconds |

Network readiness follows Windows' Internet-connectivity report, not whether one particular website works. Check the idle result only after waiting, since typing another command resets your idle time. If your machine cannot safely operate on battery, mark AC testing **not performed**.

For Task Scheduler battery behavior, also perform the sign-out/login test while unplugged. Verify the worker starts and the ordinary interval jobs run. The `ac` job must remain pending until you plug in.

## Stop and remove startup

```powershell
python tools\hardware_check.py uninstall
```

This requests a graceful stop, removes only the helper's owned startup task, and keeps the evidence. Let a long hold job finish before uninstalling. `stop` stops the worker without removing startup. To start a stopped worker without a new login, run `python tools\hardware_check.py` in a terminal; leave it open until you stop the worker.

Every `report` saves a separate JSON report containing the environment, checkpoint, and current snapshot. Keep these files with your release notes and record hardware model, power settings, release commit, observed actions, and pass/fail for each scenario. A fresh checkpoint replaces the active comparison baseline, so save a report before moving to the next scenario.

The helper intentionally does not automatically mark hardware acceptance complete. Sign-out, actual power-off, and power-state behavior need human observation. Run the automated test suite separately for DST, simulated clock jumps, contention, and injected persistence failures; do not change your Windows clock or damage the test database to reproduce them.
