# `delivery` — getting results to the operator

| File | Responsibility |
|---|---|
| `cli.py` | The `jobs` command: setup, run, pdf. Preflight, run shape, logging. |
| `mailer.py` | Renders and sends the daily digest. |
| `scheduling.py` | Task Scheduler XML and registration. |

`cli.py` is orchestration only — every decision of substance lives in `db.py`, the agent's
tooling, or the agent's own judgement. Its job is the run's *shape*: preflight, open the run
row, drive the session, close the row, report by email.

## The invariant: exactly one email every morning

Three flavours — the matches, "nothing today", or the failure. Always one.

**That is what makes silence meaningful.** If no email arrives, the scheduler never fired or
the network was down: the one class of failure nothing inside the program can report, because
reporting it needs the very thing that broke. Any *other* failure emails you.

The digest carries **no attachments** by design, which makes `jobs pdf <id>` load-bearing —
it is the only route from the database back to a file you can attach. Exports land in
`output/<run date>/`, dated by when the CV was made rather than when you exported it, so
running the command twice never scatters one job across two folders.

## `jobs run` starts its own dependencies

It does not merely wait for Docker — it starts it. `ensure_database()` launches Docker
Desktop if the daemon is silent, polls up to 120s for it (a cold start takes 30–90s, and
`docker compose up` against a half-booted daemon just fails), brings up the container, then
waits for Postgres.

This exists because of a real morning. On 2026-08-14 the 09:00 task fired correctly seven
minutes after a reboot, found Docker Desktop not running, waited two minutes for a container
nobody was going to start, and died before writing a `runs` row.

The recovery is deliberately **best-effort**: if Docker cannot be started, that is reported
and `wait_for_database()` still decides whether the run proceeds. "Could not start Docker" is
a symptom; "Postgres never answered on 5433" is the fact worth emailing.

## Every run leaves a log

`logs/run-YYYY-MM-DD.log`, one file per day, stderr teed and flushed on every write. A
scheduled task's console closes with the process, and a preflight failure dies *before* the
`runs` row exists — so without this, an unattended failure leaves nothing behind anywhere.
The 2026-08-14 diagnosis had to be reconstructed from a Windows exit code and an absent
database row.

**If a morning's log file does not exist at all, the task never ran.**

## Three triggers, because one moment a day is not enough

| Trigger | When | Delay |
|---|---|---|
| Daily | 09:00 | — |
| Resume | Power-Troubleshooter event 1 — any wake from sleep or hibernation | 1 min |
| Logon | This user signs in | 2 min |

Whichever fires first does the day's work; the rest cost one database query. The delays let
Wi-Fi and the Docker daemon come up first.

**Why the resume trigger exists.** On 2026-08-15 the machine hibernated at 06:12 — it has no
S3 sleep, only Modern Standby, and on battery Windows hibernates once the standby budget is
spent (at 92% charge; this is about elapsed standby time, not charge level). A hibernating
machine is electrically off, so `WakeToRun` could not reach it and 09:00 was missed. And
`StartWhenAvailable` did not rescue it: **it re-checks missed runs after a boot, and a resume
from hibernation is not a boot.** Six hours after the machine came back,
`NumberOfMissedRuns` was still 1 and nothing had run.

**The guard is not optional.** These triggers fire several times a day. `jobs run` calls
`db.successful_run_today()` before doing anything and exits immediately if today is already
done — otherwise every lid-open would mean another search and another email. `jobs run
--force` overrides it. A *failed* run deliberately does not count as done, so a run that died
at 09:00 gets another chance when the machine wakes.

**A `LogonTrigger` must name a `UserId`.** Without one it means *any* user logs on, which
Windows treats as privileged: `schtasks` rejects the whole task with `ERROR: Access is
denied`. Naming the account keeps registration possible without elevation.

The task XML is generated rather than built with `schtasks /SC DAILY`, because neither these
triggers nor `WakeToRun` and `StartWhenAvailable` can be expressed on that command line. It
runs with `InteractiveToken`, so no password is stored.

```powershell
Get-ScheduledTaskInfo JobSearchAgent                 # LastRunTime, LastResult
powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE     # BOTH indexes must be 0x1
```

**Wake timers must be enabled on battery as well as AC.** Disabled-on-battery is the Windows
default and it turns `WakeToRun` into dead weight the moment you unplug — the task then waits
for your next login instead of waking the machine. That is exactly why 2026-08-13 never ran.

Waking covers sleep and hibernation. A machine fully shut down cannot be woken by anything,
and falls back to `StartWhenAvailable` at next login.

The task invokes `jobs.exe` by **absolute path**, because a non-interactive session does not
inherit the interactive PATH. Consequence: changing the package layout or the entry point
requires `pip install -e .` — a stale entry point fails silently until 09:00.

## Gmail

SMTP over implicit TLS on port 465. Google displays app passwords in four groups of four, and
pasting it as shown is the obvious thing to do — but SMTP rejects the spaced form, so
`app_password()` strips spaces. Failure messages never include the password.
