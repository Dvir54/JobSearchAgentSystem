# `jobsearch` — the package

Two modules sit at the root because everything else depends on them.

| File | Responsibility |
|---|---|
| `config.py` | Every tunable setting, path and measured limit. No logic. |
| `db.py` | Every SQL statement in the system. |

Then three packages, each with its own README:

- **[`agent/`](agent/README.md)** — the autonomous session: what Claude sees, what it may do,
  and the hooks that hold the line
- **[`resume/`](resume/README.md)** — the CV domain: parsing, truthfulness, page geometry
- **[`delivery/`](delivery/README.md)** — the CLI, the daily email, the scheduled task

They layer one way: `delivery` and `agent` build on `resume`, and `resume` depends on nothing
but `config`.

---

## The database is the system of record

Not files, not a JSON state file. Every job the agent has ever examined and every CV it has
ever written lives in Postgres — a local container bound to `127.0.0.1`, reachable from
nowhere else.

**All SQL lives in `db.py`.** There is exactly one file to read to know everything the system
remembers.

| Table | What it holds |
|---|---|
| `runs` | One row per run: window, counts, status, error. |
| `seen` | Every job ever examined, with its score, verdict and one-line reason. |
| `matches` | The full record for jobs that passed, including the PDF itself. |

Three details in that schema carry real weight:

**`seen.job_id` is the primary key, and that *is* the deduplication.** Not a check somewhere
in the code that a future change might skip — an insert that cannot succeed twice. Re-judging
a job the agent has already seen can neither overwrite the original verdict nor raise an
error. Being unable to forget is a property of the storage, not a promise made by the caller.

**Score and reasoning live only in `seen`.** `matches` joins to them rather than keeping its
own copy, so the score in your inbox is necessarily the score the CV was gated on. There is
one number, so there is nothing to drift.

**PDFs are stored as bytes, in the row.** A CV and the reasoning that produced it can't become
separated, and there's no directory of orphaned files to tidy up. `jobs pdf <id>` writes one
back out when you want it.

Writes commit individually. A verdict recorded for one job survives a crash while judging the
next, so an interrupted run keeps everything it had already established.

---

## No backups, by choice

`docker compose down -v`, removing the volume, or resetting Docker will destroy the history
permanently. Nothing else will — the data survives container restarts and reboots.

The consequence is deliberately mild. Losing the database means the agent forgets which jobs
it has judged and re-examines them once, costing a single day's tokens. It doesn't lose the
ability to work, and none of it is data you couldn't regenerate by running again tomorrow.

---

## Configuration

`config.py` is the one place to change behaviour: the role queries, the fit score a job needs
to earn a CV, the search window, the experience levels, the location filter, and the time the
scheduled run fires.

It loads `.env` when it is imported, before reading any environment variable, so every setting
is populated no matter which command runs first.

`PROJECT_ROOT` anchors everything read from disk — your CV, the schema, the logs, the exports.
It is derived from this file's own location rather than the working directory, so the agent
finds the same files whether it is started by you or by the scheduler.
