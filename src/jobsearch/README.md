# `jobsearch` — settings and the system of record

Two modules live at the package root because everything else depends on them.

| File | Responsibility |
|---|---|
| `config.py` | Every tunable setting, path, and measured limit. No logic. |
| `db.py` | Every SQL statement in the system. No SQL lives anywhere else. |

Then three subpackages: [`agent/`](agent/README.md) runs the autonomous session,
[`resume/`](resume/README.md) owns the CV domain, [`delivery/`](delivery/README.md) gets
results to the operator.

## The invariant: Postgres is the system of record

Not files. Not a JSON state file. Every job ever examined and every tailored PDF lives in the
database, and **no SQL exists outside `db.py`** — so there is exactly one place to look when
you want to know what the system remembers.

A local `postgres:16` container bound to `127.0.0.1:5433`, data in the `jobsearch-pgdata`
Docker volume. Port **5433**, not the default: this machine already runs a native
PostgreSQL 18 service that owns 5432, and taking it would mean stopping an install this
project knows nothing about.

| Table | What it holds |
|---|---|
| `runs` | One row per invocation: counts, window, status, error. |
| `seen` | Every job ever examined, with score, verdict and a one-line reason. |
| `matches` | The full record for jobs that passed, including the PDF bytes in `bytea`. |

Two details in that schema are load-bearing:

**`seen.job_id` is the primary key, and that *is* the dedup mechanism.** Not a check in
application code that someone could forget to call — an insert that cannot succeed twice.
`record_verdict` uses `ON CONFLICT DO NOTHING`, so re-judging a job the agent has already
seen can neither overwrite the verdict a CV was written from nor raise.

**Score and reason live only in `seen`.** `matches` joins to them rather than duplicating
them, so the digest email can never report a different score than the one the CV was gated
on. The two cannot drift because there is only one copy.

Connections are autocommit. Each write is independently meaningful: a verdict recorded for
job 7 must survive a crash while judging job 8, so there is no run-spanning transaction.

## No backups, by explicit choice

`docker compose down -v`, `docker volume rm`, or a Docker Desktop factory reset destroys the
history permanently. Nothing else does — the volume survives container stop and reboot.

The consequence is mild and was accepted deliberately: losing the database means the agent
forgets which jobs it has judged and re-examines them once, at the cost of one day's tokens.
It does not lose the ability to work.

## `config.py` loads `.env` at import

The module calls `load_dotenv()` at import time, before it reads `os.environ`. This looks
redundant and is not. It previously ran later, inside a CLI command, by which point config
had already snapshotted empty strings — so `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` were
always blank no matter what `.env` held. `jobs setup` reported credentials missing that were
sitting in the file, and the same ordering would have silently killed the digest every
morning while every test passed.

`PROJECT_ROOT` is computed by walking three levels up from this directory. Everything read
from disk hangs off it, and a wrong value still produces valid `Path` objects — so nothing
would crash, the agent would simply read a CV that isn't there.
`tests/test_config_paths.py` exists to make that fail loudly.
