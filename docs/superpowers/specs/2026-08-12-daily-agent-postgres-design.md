# Daily Agent + Local Postgres (Phase R3) — Design

**Date**: 2026-08-12
**Status**: Draft, awaiting review
**Branch**: `r3-daily-agent-postgres`
**Builds on**: R1 (autonomous Agent SDK session, Monid MCP, payload reduction) + R2 (Canva PDF
output), both merged to `master` at `5e8448b`

## Problem

The pipeline produces tailored CVs, but it is still *a thing you run*. Three gaps keep it
from being a daily agent:

1. **No memory across runs.** Every invocation re-scrapes, re-reads and re-scores postings it
   already judged yesterday. Dedup is within-run only.
2. **No durable record.** PDFs land in `output/<date>/` beside an `index.md`. There is no way
   to ask what was seen, what it scored, or why something was rejected.
3. **Nothing invokes it, and nothing reports back.** The operator must remember to run it and
   then go and read a file.

**Goal:** a daily agent that runs itself at 09:00, remembers everything it has ever examined
in a local Postgres, stores every tailored CV as a PDF in that database, and emails a digest
of what it found.

The Claude reasoning, the Canva render path and the truthfulness enforcement boundary are
**unchanged**. This phase adds state, scheduling and reporting around them.

## Decisions locked

- **Postgres, in Docker, local only.** `postgres:16` bound to `127.0.0.1:5433`, data in a
  named volume. Chosen over Mongo because the data is relational (runs → jobs → matches),
  dedup is naturally a primary-key constraint, and `bytea` stores the PDFs without any
  large-object machinery.
- **Postgres is the system of record.** PDF bytes, fit score, full reasoning and the Canva
  link all live in it. `output/` dated folders and `index.md` are removed.
- **Every examined job is remembered, matched or not** — in a `seen` table, so a rejected
  posting is never paid for twice and a rejection can be audited.
- **Cross-run dedup happens in the reduction hook, in code**, not in the agent's judgement.
- **One email every morning**, in three flavours: matches digest, empty run, failed run.
  Silence therefore means the scheduler itself is broken.
- **Digest carries no attachments.** PDFs are retrieved on demand with `jobs pdf <id>`.
- **Gmail SMTP with an app password.** No third-party mail service.
- **Windows Task Scheduler, 09:00 daily, wake-the-computer AND catch-up-on-missed-start.**
- **A failed run is a lost day.** Transient source errors retry inside the run; the window
  is never widened afterwards to compensate.
- **No backups.** Accepted risk, revisitable later (see "Deferred").
- **Three CLI commands:** `setup`, `run`, `pdf`.

## Non-goals

- Changing scoring, tailoring, the Canva write path, `prepare_resume`, or either enforcement
  hook. The truthfulness boundary does not move in this phase.
- Auto-apply, cover letters, or anything that submits an application.
- Running anywhere but this machine. No cloud host, no remote database.
- A migration framework. There is one consumer and no deployed instances.
- Content-hash dedup of identical postings under different job ids (still deferred from R1).

## Architecture

```
jobs run
  │
  ├─ preflight: Docker up? container healthy?        ── fail → runs.status='failed' + email
  ├─ INSERT runs row (started_at, window)
  │
  ├─ Claude Agent SDK session
  │    Monid MCP  ─► PostToolUse reduction hook (src/hooks.py)
  │                    ├ within-run dedupe, Israel filter, field projection   (existing)
  │                    └ DROP job ids present in `seen`                       (NEW)
  │    per job ─► get_job (description, one at a time)  (existing)
  │            ─► score against base_cv.md             (existing)
  │            ─► record_verdict ──────────────────────► seen                 (NEW tool)
  │            ─► if kept: prepare_resume ─► Canva MCP ─► export              (existing)
  │                        save_pdf ──────────────────► matches               (CHANGED sink)
  │
  ├─ UPDATE runs row (finished_at, counts, status)
  └─ compose + send digest  ◄── SELECT over this run's committed rows
```

The email is built from **committed database rows**, never from the agent's own summary of
what it did. The agent can be wrong about what it wrote; the table cannot.

### New modules

| Module | Responsibility | Depends on |
|---|---|---|
| `src/db.py` | Connection, schema application, and every query used elsewhere. No SQL outside this module. | `psycopg`, `config` |
| `src/mailer.py` | Compose the three digest flavours and send over SMTP. Pure rendering split from sending, so rendering is testable without a socket. | `config` |
| `src/cli.py` | Argument parsing and the three commands. Orchestration only; no business logic. | `db`, `mailer`, `agent` |
| `src/scheduling.py` | Register/inspect the Task Scheduler job and report on wake-timer policy. | stdlib `subprocess` |

Named `mailer.py`, not `email.py`, to avoid shadowing the stdlib `email` package.

### Changed code

| Location | Change |
|---|---|
| `src/hooks.py` reduction hook | After the Israel filter, drop ids returned by `db.filter_unseen()`. One query per run, ids only. |
| `src/tooling.py::save_pdf` | Download the export as today, then `db.insert_match(...)` with the bytes instead of `path.write_bytes`. Still returns rather than raises: one failed download must not end the run. |
| `src/tooling.py::write_index` | Deleted, with `src/render.py::render_index` and the `write_index` tool. |
| `src/tools.py` | Remove `write_index`; add `record_verdict`. |
| `src/agent.py` | `main()` becomes a callable the CLI drives, receiving `run_id`; the prompt instructs `record_verdict` for every job examined. |
| `src/config.py` | Add `DB_*`, `SMTP_*`, `SCHEDULE_TIME`. Remove `OUTPUT_DIR` once nothing writes there. `POSTED_LIMIT` is already `"24h"`; unchanged. |

## Data model

`schema.sql`, applied idempotently by `jobs setup` (`CREATE TABLE IF NOT EXISTS`).

### `runs` — one row per invocation

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `started_at` / `finished_at` | `timestamptz` | `finished_at` null while in flight |
| `search_window` | `text` | the `POSTED_LIMIT` searched, e.g. `24h`. Not named `window`, which is a reserved word in Postgres and would need quoting at every use. |
| `fetched_count` | `int` | postings returned by the source |
| `skipped_seen_count` | `int` | dropped by cross-run dedup |
| `examined_count` | `int` | jobs actually scored |
| `matched_count` | `int` | jobs that earned a CV |
| `status` | `text` | `ok` \| `empty` \| `failed` |
| `error` | `text` | populated only when `failed` |

### `seen` — one row per job ever examined

| Column | Type | Notes |
|---|---|---|
| `job_id` | `text` PK | the dedup key; re-insert is `ON CONFLICT DO NOTHING` |
| `first_seen_at` | `timestamptz` | |
| `run_id` | `bigint` FK → `runs.id` | the run that first saw it |
| `title`, `company` | `text` | |
| `fit_score` | `int` | |
| `verdict` | `text` | `matched` \| `rejected` |
| `reason` | `text` | one line, for auditing a rejection |

The primary key is the dedup mechanism. Dedup is enforced by the database, not by code
remembering to check.

### `matches` — the full record for jobs that passed

| Column | Type | Notes |
|---|---|---|
| `job_id` | `text` PK, FK → `seen.job_id` | |
| `run_id` | `bigint` FK → `runs.id` | |
| `title`, `company`, `location` | `text` | |
| `apply_url` | `text` | |
| `posted_date` | `text` | as the source reports it |
| `canva_design_id`, `canva_url` | `text` | permanent design id, per R2 |
| `pdf` | `bytea` | the exported CV |
| `pdf_filename` | `text` | the R2 filename, retained for `jobs pdf` |
| `created_at` | `timestamptz` | |

The fit score and its reasoning are **not** repeated here — they live in `seen` and are read
by joining on `job_id`. The digest and the CV therefore cannot disagree about why a job
qualified.

PDF bytes go in `bytea`: ~100KB per CV at a handful per day is a few MB a year, well inside
what Postgres handles inline, and it keeps the record in one place with no parallel file
tree to drift out of sync.

## CLI

Installed as a `jobs` console script via `pyproject.toml`'s `[project.scripts]`, pointing at
`src/cli.py:main`. The scheduled task invokes the venv's `jobs.exe` by absolute path, so it
does not depend on `PATH` being set up for a non-interactive session.

### `jobs setup` — once

1. Write `docker-compose.yml`; `docker compose up -d`; wait for the healthcheck.
2. Apply `schema.sql`.
3. Verify `.env`: `MONID_API_KEY`, `ANTHROPIC_API_KEY`, Canva auth, `GMAIL_ADDRESS`,
   `GMAIL_APP_PASSWORD`. Name every missing key.
4. Live SMTP login, so a bad app password fails here rather than silently at 09:00.
5. Register the Task Scheduler job (below) and report whether the active power plan permits
   wake timers.

Idempotent: re-running repairs a partial install rather than erroring.

### `jobs run` — daily, invoked by the scheduler

The full flow in "Architecture". Exit code 0 on `ok`/`empty`, nonzero on `failed`, so a
failure is visible in Task Scheduler's history even if the failure email cannot be sent.

### `jobs pdf <job_id>` — on demand

`SELECT pdf, pdf_filename FROM matches WHERE job_id = %s`, write it to the current directory,
open it with the OS default handler. Exists because the digest carries no attachments; without
it the PDFs are write-only from the operator's side.

`list` was considered and dropped: the always-email policy already reports what ran and what
was found, so it would be a second route to the same information.

## Scheduling

Registered via `schtasks` with:

- **Trigger:** daily at 09:00.
- **Conditions:** *Wake the computer to run this task* — the actual wake mechanism.
- **Settings:** *Run task as soon as possible after a scheduled start is missed*.
- **Working directory** pinned to the repo, since a wrong CWD produces a task that fails every
  morning in silence.

Wake timers cover sleep only. A shut-down or hibernated machine cannot be woken by a timer,
and Windows 11 laptops commonly default to *Important Wake Timers Only* on battery, which
suppresses the task. The catch-up setting covers both cases: the run happens within a minute
of the next login. `setup` reports the power-plan state (via `powercfg /q`) but **does not
change it** — an unattended machine waking itself in a closed bag is the operator's call.

## Error handling

Every failure after the run row is opened closes that row with `status='failed'` and the
error text, then sends the failure email. Preflight failures happen before the row exists —
the database is the thing that is broken — so they are reported by email and exit code only.

| Failure | Behaviour |
|---|---|
| Docker down / container unhealthy | Stop before the run row is opened. Failure email, exit nonzero. |
| Monid 502/503/timeout | Bounded exponential backoff inside the run (3 attempts). |
| Monid hard failure after retries | Fail the run. The day is lost; the next run uses its normal 24h window. |
| Agent session raises | Fail the run. Rows already committed for jobs completed before the error are kept — partial progress is real progress, and `seen` prevents rework. |
| One PDF export/download fails | Log, skip that job, continue. Unchanged from R2. |
| SMTP refuses | Record on the run row, exit nonzero. Surfaces as a failed task in Scheduler history — this is the one failure that cannot self-report. |

`seen` is written per job as the agent goes, not batched at the end, so a crash halfway
through does not force tomorrow to re-score the first half.

## Email

Composed from committed rows after the session closes.

**Subject** carries the answer, for triage from a lock screen:
`4 new job matches` / `No new matches today` / `Job agent FAILED: <short cause>`.

**Body (matches)** — one block per job, highest score first: title, company, fit score; the
one-line reason; the apply URL; the Canva link; and the literal `jobs pdf <id>` command.
Footer gives run stats: fetched, skipped as already seen, examined, matched, and the window.

**Body (empty)** — the same footer, with a line stating nothing cleared the threshold.

**Body (failed)** — the stage that failed and the error text.

Plain HTML with a text fallback. No images, no tracking: it is mail from the operator to
themselves.

## Testing

The 159 existing tests must continue to pass. New coverage:

- **Schema round-trip** — insert and read back a `matches` row including PDF bytes; assert
  the bytes are identical.
- **Dedup** — `filter_unseen` drops known ids and keeps new ones; re-inserting a known
  `job_id` does not raise and does not duplicate.
- **Reduction hook** — a payload containing a previously-seen id yields a manifest without
  it, and `skipped_seen_count` reflects the drop. Extends the existing hook tests.
- **`record_verdict`** — writes both `matched` and `rejected` rows.
- **`save_pdf`** — writes to the database, and a failed download still returns rather than
  raising.
- **Digest rendering** — all three flavours, asserted against row fixtures. Rendering is
  tested without a socket; sending is tested with the SMTP transport mocked.
- **Run lifecycle** — an exception mid-session leaves `status='failed'` with the error, and
  rows committed before the failure survive.

Database tests run against a **real throwaway Postgres container**, not a fake or SQLite. The
entire point of the design is that the database enforces dedup; a fake would test the mock.
Tests skip with a clear message when Docker is unavailable rather than failing.

## Migration

There is no existing data to migrate. `output/` holds one R2 run plus pre-Canva leftovers;
these are left on disk untouched and simply stop being written to. The operator can delete
the folder whenever they choose.

## Deferred

- **Backups.** Explicitly declined for now. If the archive proves valuable, the intended shape
  is an automatic `pg_dump` after each successful run into a cloud-synced folder, keeping the
  last 7 — not a command the operator must remember to type.
- **Canva cleanup.** Canva still has no delete API, so per-run designs accumulate and need
  manual pruning (deferred from R2).
- **Content-hash dedup** of identical postings published under different job ids.
- **Widening the window after a failed day.** Considered and rejected; a lost day stays lost.
- **The R2 branches that have never executed live**: overflow redraft, cancel, export polling.
