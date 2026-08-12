# Job Search Agent

A daily agent that finds junior software jobs in Israel, judges which ones genuinely fit
*you*, and renders a job-tailored version of your real Canva résumé as a PDF for each
strong match. It emails you a digest every morning at 09:00.

It stops at "here's a tailored CV worth sending." It does **not** apply for you.

---

## What happens each morning

Windows Task Scheduler runs `jobs run` at 09:00 — waking the laptop if it is asleep and
plugged in, otherwise running the moment you next log in. The agent then:

1. **Searches** LinkedIn (via Monid → Apify's harvestapi scraper) for five role types in
   Israel, entry-level, posted in the last 24 hours.
2. **Reduces** the raw scrape in-process, before the model sees any of it: dedupe by id,
   keep only Israel-located roles, and **drop every posting already judged on an earlier
   day**. Yesterday's jobs cost nothing today — not even tokens.
3. **Judges** each remaining posting against your CV, then records a verdict for every one
   of them — kept or rejected — so tomorrow skips it.
4. **Tailors** your résumé for each job above the fit threshold, copies your Canva design,
   applies the edits, and exports a PDF.
5. **Stores** each CV in Postgres with its score, reasoning and Canva link.
6. **Emails** you one digest: the matches, or "nothing today", or the failure.

You get exactly one email every morning, so silence means the schedule itself is broken —
the one failure nothing inside the program can report.

---

## Install

Requires Python 3.11+, Docker Desktop, and a Canva account with your résumé in it.

```bash
python -m venv .venv
.venv/Scripts/pip install -e .
```

Put these in `.env` at the repo root (git-ignored):

```
ANTHROPIC_API_KEY=sk-ant-...
MONID_API_KEY=monid_live_...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=sixteencharacters
```

The Gmail app password comes from Google Account → Security → App passwords, and needs
2-step verification switched on.

You also need your résumé at the repo root as `base_cv.md` (git-ignored). **Canva supplies
the layout; `base_cv.md` supplies the facts.** It is what the agent is allowed to reword,
and — more importantly — it is what the truthfulness guards diff against to catch an
invented skill or a fabricated bullet. That check cannot read from Canva, because Canva is
the thing being written to: you cannot validate a write against its own target.

When your real CV changes, update both — `base_cv.md` for the content and the Canva
template for the design. If they drift apart, drafts start getting rejected because the
guards insist on content the design no longer has.

Then:

```bash
jobs setup
```

That starts the Postgres container, creates the schema, checks every key, logs into Gmail
to prove the app password works, registers the 09:00 task, and tells you whether your power
plan will let it wake the machine. It is safe to re-run.

---

## Daily use

Nothing. Read the email.

```bash
jobs pdf 4446164871   # write one stored CV next to you and open it
jobs run              # start a run by hand
```

The digest carries no attachments — `jobs pdf <id>` is how a CV gets back out of the
database. The id is in the email.

---

## Where the data lives

A local `postgres:16` container bound to `127.0.0.1:5433`, data in the `jobsearch-pgdata`
Docker volume. Port 5433 rather than the default because this machine already runs a native
PostgreSQL 18 service on 5432.

| Table | What it holds |
|---|---|
| `runs` | One row per invocation: counts, window, status, error. |
| `seen` | Every job ever examined, with its score, verdict and one-line reason. The primary key **is** the dedup mechanism. |
| `matches` | The full record for jobs that passed, including the PDF bytes. |

Score and reasoning live only in `seen`; `matches` joins to them, so the email can never
report a different score than the one the CV was gated on.

There are **no backups**. `docker compose down -v` or a Docker Desktop factory reset
destroys the history permanently. The agent itself recovers — it just forgets.

---

## How truthfulness is enforced

Claude does the judging and the drafting. Nothing else is delegated to it.

- **`prepare_resume`** gates every draft in code: fit threshold, invented-skills check,
  entry coverage, length budget. It returns the exact Canva operations and writes nothing.
- **A PreToolUse hook** inspects what is genuinely about to be written to Canva. The agent
  makes the MCP calls itself, so this — not `prepare_resume` — is the real boundary.
- **A PostToolUse hook** reads back the actual geometry to confirm nothing overflowed the
  page and the text really landed. Canva reports `success` for replacements that matched
  nothing, so results are never trusted on their own.

The agent has no Bash, Read, Write, WebFetch or Agent tools. A failure cannot degrade into
hand-parsing.

---

## Project structure

```
src/
  config.py       tunable settings, paths, measured limits
  agent.py        the workflow prompt, SDK options, hooks — one autonomous session
  tools.py        the in-process MCP tools the agent may call
  tooling.py      everything those tools actually do; the payload reducer
  hooks.py        payload reduction + the Canva write guard
  canva.py        element parsing, capacity maths, overflow detection
  db.py           every database access — no SQL lives anywhere else
  mailer.py       renders and sends the daily digest
  cli.py          the `jobs` command: setup, run, pdf
  scheduling.py   Task Scheduler XML and registration
  jobs.py         normalises raw scraper JSON
  resume.py       parses base_cv.md
  tailoring.py    truthfulness guards
  render.py       PDF filenames
schema.sql        the three tables
docker-compose.yml
tests/            the suite; database tests use a real throwaway Postgres
docs/             design specs and implementation plans
```

Run the tests with `.venv/Scripts/pytest`. They skip the database tests with a clear
message if Docker is not running.

---

## Tuning

All in `src/config.py`: the five role queries, `MAX_ITEMS_PER_QUERY`, `FIT_THRESHOLD` (the
score a job needs to earn a CV), `POSTED_LIMIT` (the search window, `24h` for daily),
`EXPERIENCE_LEVELS`, `LOCATION_KEYWORD`, and `SCHEDULE_TIME`.

A normal day costs roughly $1.50–2.50, mostly Claude, plus about $0.12 on Monid.

---

## What it does NOT do

No auto-apply, no cover letters, no ranking beyond the fit score. Canva has no delete API,
so per-run designs accumulate and need occasional manual cleanup. A failed run is a lost
day: transient source errors retry inside the run, but the search window is never widened
afterwards to catch up.
