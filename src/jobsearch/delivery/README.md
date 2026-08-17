# `delivery` — running the agent, and telling you what happened

| File | Responsibility |
|---|---|
| `cli.py` | The `jobs` command: `init`, `setup`, `run`, `pdf`. The shape of a run. |
| `mailer.py` | Renders and sends the daily digest. |
| `scheduling.py` | Registers the scheduled task. |

`jobs init` runs once, before anything is installed: it reads the user's Canva CV and writes
the profile and `base_cv.md`. It needs no database, so it works on a machine where nothing
else is set up yet — and both `setup` and `run` refuse to proceed without a usable profile,
naming what is missing.

`cli.py` orchestrates and decides nothing of substance. Its job is the run's *shape*: get the
database up, check whether today is already done, open the run row, drive the session, close
the row, report by email.

---

## The contract: one email every morning

Three flavours — the matches, "nothing today", or the failure. Always exactly one.

That consistency is what makes **silence meaningful**. If no email arrives, either the
scheduler never fired or the network was down — the one class of failure that can't report
itself, because reporting needs the very thing that broke. Every other failure emails you.

The digest carries **no attachments**. It lists each match with its score, reasoning and a
link, and `jobs pdf <id>` fetches the CV itself when you want it. Exports are filed under
`output/<run date>/`, dated by when the CV was made rather than when you asked for it, so
running the command twice never scatters one job across two folders.

---

## Running every day on a laptop

A laptop is not a server. It sleeps, it hibernates, it gets closed mid-morning and opened at
lunchtime. A single alarm at 09:00 misses all of that.

So the task has **three triggers**, and whichever fires first does the day's work:

| Trigger | When | Delay |
|---|---|---|
| Daily | 09:00 | — |
| Resume | The machine wakes from sleep or hibernation | 1 min |
| Logon | You sign in | 2 min |

The short delays give Wi-Fi and the Docker daemon a moment to come up first.

**Extra triggers are only safe because of the guard.** Before doing anything, `jobs run` asks
the database whether today already has a finished run, and exits in one query if so — no
search, no email, no cost. Without it, every lid-open would mean another search and another
message in your inbox. A *failed* run doesn't count as done, so a morning that broke gets
another attempt the next time the machine comes back.

`jobs run --force` overrides the guard when you want to re-run a finished day deliberately.

**The run starts its own dependencies.** If the Docker daemon isn't answering it launches
Docker Desktop, waits for it, brings up the database container, and only then connects. It
doesn't assume the machine was left in a working state, because after a reboot it usually
wasn't. If that recovery fails it still tries to connect, so the error you're sent is
"Postgres never answered" — the fact you need — rather than a symptom further up the chain.

### Power settings that matter

Waking a sleeping machine needs wake timers enabled **on battery as well as mains**. Disabled
on battery is the Windows default, and it quietly turns the 09:00 alarm into nothing the
moment you unplug.

```powershell
powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE   # both indexes should read 0x1
```

There's a limit worth knowing: on a laptop with modern standby, Windows hibernates the
machine once it has spent its overnight power budget — typically after a few hours on
battery, regardless of how much charge is left. A hibernating machine is electrically off and
no timer can reach it. That's what the resume trigger is for: the run happens when you open
the lid instead.

---

## When something goes wrong

Every run writes `logs/run-YYYY-MM-DD.log`, one file per day, flushed as it goes. A scheduled
task's console closes with the process, so without this an unattended failure would leave
nothing behind at all — and a failure during startup happens before there's a run row to
record it in.

**If a morning's log file doesn't exist, the task never ran.** That single check separates
"the agent broke" from "the agent never started", which are very different problems.

```powershell
Get-ScheduledTaskInfo JobSearchAgent     # last run, last result, missed runs
```

---

## Email

Gmail over SMTP with an app password, on implicit TLS. Google displays app passwords in four
groups of four; the spaces are for readability and SMTP rejects them, so they're stripped
before use. Failure messages never quote the password back.
