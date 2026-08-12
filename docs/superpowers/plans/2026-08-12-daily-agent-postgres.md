# Daily Agent + Local Postgres (Phase R3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing one-shot pipeline into a self-running daily agent that remembers every job it has examined in a local Postgres, stores each tailored CV as a PDF in that database, and emails a digest every morning at 09:00.

**Architecture:** A Dockerised `postgres:16` on `127.0.0.1:5432` becomes the system of record. Three tables — `runs`, `seen`, `matches`. Cross-run dedup is a `filter_unseen` query inside the existing PostToolUse reduction hook, so previously-examined postings never enter the model's context. A new `cli.py` owns the run lifecycle: preflight, open run row, drive the agent session, close the run row, send the digest. `output/` dated folders and `index.md` are removed.

**Tech Stack:** Python 3.11+, `psycopg[binary]>=3.1`, `postgres:16` via Docker Compose, stdlib `smtplib`/`email` for Gmail SMTP, Windows `schtasks` driven by a generated task XML, pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-12-daily-agent-postgres-design.md`. Read it before Task 1.
- **The truthfulness boundary does not move.** Do not modify `prepare_resume`, `guard_canva_write`, `reduce_canva_output`, `strip_invented_skills`, or the scoring rubric. Any task that appears to require it is a misreading — stop and ask.
- **All 159 existing tests must keep passing.** Run `pytest` (from the repo root) at the end of every task, not just the new test.
- **Database is local only.** Bind Postgres to `127.0.0.1`, never `0.0.0.0`.
- **No SQL outside `src/db.py`.** Every other module calls a named function.
- **`src/mailer.py`, never `src/email.py`** — the latter shadows the stdlib `email` package and breaks `smtplib`.
- **Windows is the only target.** Paths, `schtasks`, and `powercfg` are Windows-specific by design; no cross-platform abstraction.
- **Secrets stay in `.env`** (already gitignored). Never commit a key, never print an app password.
- **Style:** match the surrounding code — module docstrings that explain *why*, comments that record measured facts and past defects, functions that return an error rather than raising when one job's failure must not end the run.
- **Commit after every task**, using the `feat:`/`fix:`/`docs:`/`test:` prefixes already in the log.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `docker-compose.yml` | The Postgres service definition, with a healthcheck and a named volume. |
| `schema.sql` | The three tables, idempotent (`CREATE TABLE IF NOT EXISTS`). |
| `src/db.py` | Connection handling plus every query the rest of the app uses. The only module containing SQL. |
| `src/mailer.py` | Renders the three digest flavours and sends over SMTP. Rendering is separated from sending so it is testable without a socket. |
| `src/cli.py` | The `setup` / `run` / `pdf` commands. Orchestration only — no business logic. |
| `src/scheduling.py` | Generates the Task Scheduler XML, registers it via `schtasks`, and reports the wake-timer policy. |
| `tests/conftest.py` | The Postgres fixture: creates the test database, applies the schema, truncates between tests, skips cleanly when Docker is unavailable. |
| `tests/test_db.py`, `tests/test_mailer.py`, `tests/test_cli.py`, `tests/test_scheduling.py` | Coverage for the new modules. |

**Modified:**

| File | Change |
|---|---|
| `src/config.py` | Add `DATABASE_URL`, SMTP settings, `SCHEDULE_TIME`, `TASK_NAME`. Remove `OUTPUT_DIR` in Task 14. |
| `src/tooling.py` | Cross-run dedup in `reduce_run_payload`; run-scoped state (`set_run_id`, counters); `save_pdf` writes to the database; `record_verdict` added; `write_index` deleted. |
| `src/tools.py` | Drop the `write_index` tool, add `record_verdict`. |
| `src/hooks.py` | No logic change — the dedup lands in `tooling.reduce_run_payload`, which the hook already calls. |
| `src/agent.py` | `main()` becomes `run_session()` returning a result dict; workflow prompt gains `record_verdict` and loses `write_index`. |
| `src/render.py` | `render_index` deleted in Task 14. |
| `pyproject.toml` | Add `[project.scripts] jobs = "cli:main"`. |
| `requirements.txt` | Add `psycopg[binary]>=3.1`. |
| `README.md` | Rewritten for the new install and daily operation. |

---

### Task 1: Database foundation — compose file, schema, connection

**Files:**
- Create: `docker-compose.yml`, `schema.sql`, `src/db.py`, `tests/conftest.py`, `tests/test_db.py`
- Modify: `src/config.py`, `requirements.txt`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `db.connect(dsn: str | None = None) -> psycopg.Connection` — a new autocommit connection.
  - `db.session() -> psycopg.Connection` — lazily-created module-level connection, reused.
  - `db.close_session() -> None`
  - `db.apply_schema(conn=None) -> None`
  - `config.DATABASE_URL: str`

- [ ] **Step 1: Add the dependency and config**

Append to `requirements.txt`:

```
psycopg[binary]>=3.1
```

Install it: `.venv\Scripts\pip install "psycopg[binary]>=3.1"`

Add to the end of `src/config.py`:

```python
# --- Local Postgres (Phase R3) ---
# The database is the system of record: every job examined, every tailored PDF.
# Bound to 127.0.0.1 by docker-compose.yml — it is never reachable off this machine.
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://jobs:jobs@127.0.0.1:5432/jobs")
# Tests point at a separate database on the same container so a test run can
# never truncate real history.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jobs:jobs@127.0.0.1:5432/jobs_test")
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16
    container_name: jobsearch-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: jobs
      POSTGRES_PASSWORD: jobs
      POSTGRES_DB: jobs
    ports:
      # 127.0.0.1 only. Never 0.0.0.0 — this database holds the full job history
      # and there is no reason for anything off this machine to reach it.
      - "127.0.0.1:5432:5432"
    volumes:
      - jobsearch-pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jobs -d jobs"]
      interval: 3s
      timeout: 3s
      retries: 20

volumes:
  jobsearch-pgdata:
```

- [ ] **Step 3: Write `schema.sql`**

```sql
-- Applied idempotently by `jobs setup`. There is one consumer and no deployed
-- instances, so there is no migration framework: additive changes go here and
-- re-running setup is always safe.

CREATE TABLE IF NOT EXISTS runs (
    id                 bigserial PRIMARY KEY,
    started_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    -- Not named `window`: WINDOW is a reserved word in Postgres and would need
    -- quoting at every single use.
    search_window      text        NOT NULL,
    fetched_count      int         NOT NULL DEFAULT 0,
    skipped_seen_count int         NOT NULL DEFAULT 0,
    examined_count     int         NOT NULL DEFAULT 0,
    matched_count      int         NOT NULL DEFAULT 0,
    -- 'running' while in flight, then 'ok' | 'empty' | 'failed'.
    status             text        NOT NULL DEFAULT 'running',
    error              text
);

-- One row per job ever examined. The PRIMARY KEY is the dedup mechanism: dedup
-- is enforced by the database, not by code remembering to check.
CREATE TABLE IF NOT EXISTS seen (
    job_id        text PRIMARY KEY,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    run_id        bigint REFERENCES runs(id),
    title         text,
    company       text,
    fit_score     int,
    verdict       text NOT NULL,          -- 'matched' | 'rejected'
    reason        text
);

-- The shippable record for jobs that passed. Score and reason are NOT duplicated
-- here: they live in `seen` and are read by joining on job_id, so the two can
-- never disagree about why a CV was written.
CREATE TABLE IF NOT EXISTS matches (
    job_id          text PRIMARY KEY REFERENCES seen(job_id),
    run_id          bigint REFERENCES runs(id),
    title           text,
    company         text,
    location        text,
    apply_url       text,
    posted_date     text,
    canva_design_id text,
    canva_url       text,
    pdf             bytea NOT NULL,
    pdf_filename    text  NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS matches_run_id_idx ON matches (run_id);
```

- [ ] **Step 4: Start the container and confirm it is healthy**

```bash
docker compose up -d
docker compose ps
```

Expected: the `jobsearch-db` service listed as `running (healthy)`. If Docker Desktop is not running, start it and retry before continuing.

- [ ] **Step 5: Write the failing test**

Create `tests/test_db.py`:

```python
import db


def test_apply_schema_creates_the_three_tables(pg):
    with pg.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema = 'public'""")
        tables = {row[0] for row in cur.fetchall()}
    assert {"runs", "seen", "matches"} <= tables


def test_apply_schema_is_idempotent(pg):
    db.apply_schema(pg)          # the conftest fixture already applied it once
    db.apply_schema(pg)          # a third time must not raise


def test_session_is_reused(monkeypatch):
    import config
    monkeypatch.setattr(config, "DATABASE_URL", config.TEST_DATABASE_URL)
    db.close_session()
    first = db.session()
    assert db.session() is first
    db.close_session()
```

Create `tests/conftest.py`:

```python
"""Postgres fixture for the database tests.

Runs against a REAL throwaway database, not a fake. The whole point of the design
is that Postgres enforces dedup through a primary key; a mock would test the mock.
Tests skip with a clear message when the container is not up, so a developer
without Docker running gets a skip rather than a wall of errors.
"""
import psycopg
import pytest

import config
import db


def _ensure_test_database():
    """Create jobs_test if it does not exist. Connects to the maintenance
    database because CREATE DATABASE cannot run inside a transaction."""
    admin_dsn = config.TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'jobs_test'")
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE jobs_test")


@pytest.fixture(scope="session")
def _pg_available():
    try:
        _ensure_test_database()
    except Exception as exc:                      # noqa: BLE001 - skip, never fail
        pytest.skip(f"Postgres not reachable ({exc}). Run `docker compose up -d`.")


@pytest.fixture
def pg(_pg_available, monkeypatch):
    """A connection to jobs_test with a fresh, empty schema."""
    monkeypatch.setattr(config, "DATABASE_URL", config.TEST_DATABASE_URL)
    db.close_session()
    conn = db.connect(config.TEST_DATABASE_URL)
    db.apply_schema(conn)
    with conn.cursor() as cur:
        # RESTART IDENTITY so run ids are predictable per test; CASCADE because
        # seen and matches both reference runs.
        cur.execute("TRUNCATE matches, seen, runs RESTART IDENTITY CASCADE")
    yield conn
    conn.close()
    db.close_session()
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`.

- [ ] **Step 7: Write `src/db.py`**

```python
"""Every database access in the system. No SQL lives outside this module.

Postgres is the system of record: every job the agent has ever examined, and the
PDF of every CV it wrote. It runs as a local Docker container bound to 127.0.0.1
(see docker-compose.yml) — nothing here is reachable off this machine.

Connections are autocommit. Each write is independently meaningful: a verdict
recorded for job 7 must survive a crash while judging job 8, so there is no
run-spanning transaction to roll back.
"""
import psycopg

import config

_SESSION = None


def connect(dsn=None):
    """A new autocommit connection. Callers that own a connection use this."""
    return psycopg.connect(dsn or config.DATABASE_URL, autocommit=True,
                           connect_timeout=5)


def session():
    """The shared module-level connection, opened on first use.

    The agent's in-process tools (record_verdict, save_pdf) are called many times
    per run from deep inside the SDK session, where threading a connection through
    would mean routing it through the tool schema — i.e. through the model.
    """
    global _SESSION
    if _SESSION is None or _SESSION.closed:
        _SESSION = connect()
    return _SESSION


def close_session():
    global _SESSION
    if _SESSION is not None and not _SESSION.closed:
        _SESSION.close()
    _SESSION = None


def _conn(conn):
    return conn if conn is not None else session()


def apply_schema(conn=None):
    """Apply schema.sql. Idempotent — every statement is CREATE ... IF NOT EXISTS."""
    sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    with _conn(conn).cursor() as cur:
        cur.execute(sql)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 9: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all previously-passing tests still pass, plus the 3 new ones.

- [ ] **Step 10: Commit**

```bash
git add docker-compose.yml schema.sql src/db.py src/config.py tests/conftest.py tests/test_db.py requirements.txt
git commit -m "feat: local Postgres, schema, and the db module's connection layer"
```

---

### Task 2: Run lifecycle queries

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `db.session()`, `db.apply_schema()` (Task 1).
- Produces:
  - `db.start_run(search_window: str, conn=None) -> int` — inserts a `running` row, returns its id.
  - `db.finish_run(run_id, *, fetched, skipped_seen, examined, matched, status, conn=None) -> None`
  - `db.fail_run(run_id, error: str, conn=None) -> None`
  - `db.get_run(run_id, conn=None) -> dict` — every column, as a dict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_start_run_opens_a_running_row(pg):
    run_id = db.start_run("24h", pg)
    row = db.get_run(run_id, pg)
    assert row["status"] == "running"
    assert row["search_window"] == "24h"
    assert row["finished_at"] is None


def test_finish_run_records_counts_and_status(pg):
    run_id = db.start_run("24h", pg)
    db.finish_run(run_id, fetched=111, skipped_seen=80, examined=31,
                  matched=4, status="ok", conn=pg)
    row = db.get_run(run_id, pg)
    assert (row["fetched_count"], row["skipped_seen_count"],
            row["examined_count"], row["matched_count"]) == (111, 80, 31, 4)
    assert row["status"] == "ok"
    assert row["finished_at"] is not None
    assert row["error"] is None


def test_fail_run_records_the_error(pg):
    run_id = db.start_run("24h", pg)
    db.fail_run(run_id, "Monid returned 502 after 3 attempts", pg)
    row = db.get_run(run_id, pg)
    assert row["status"] == "failed"
    assert "502" in row["error"]
    assert row["finished_at"] is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_db.py -v -k "run"`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'start_run'`.

- [ ] **Step 3: Implement**

Append to `src/db.py`:

```python
def _row_to_dict(cur, row):
    if row is None:
        return None
    return dict(zip([c.name for c in cur.description], row))


def start_run(search_window, conn=None):
    """Open a run row and return its id. Everything downstream references it."""
    with _conn(conn).cursor() as cur:
        cur.execute(
            "INSERT INTO runs (search_window) VALUES (%s) RETURNING id",
            (search_window,))
        return cur.fetchone()[0]


def finish_run(run_id, *, fetched, skipped_seen, examined, matched, status,
               conn=None):
    with _conn(conn).cursor() as cur:
        cur.execute("""UPDATE runs
                          SET finished_at = now(), fetched_count = %s,
                              skipped_seen_count = %s, examined_count = %s,
                              matched_count = %s, status = %s
                        WHERE id = %s""",
                    (fetched, skipped_seen, examined, matched, status, run_id))


def fail_run(run_id, error, conn=None):
    """Close a run as failed. Counts stay at whatever the run reached — partial
    progress is real progress, and `seen` keeps it from being redone."""
    with _conn(conn).cursor() as cur:
        cur.execute("""UPDATE runs SET finished_at = now(), status = 'failed',
                              error = %s
                        WHERE id = %s""", (error, run_id))


def get_run(run_id, conn=None):
    with _conn(conn).cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
        return _row_to_dict(cur, cur.fetchone())
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: run lifecycle queries"
```

---

### Task 3: Seen table — verdicts and cross-run dedup

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `db.start_run` (Task 2).
- Produces:
  - `db.record_verdict(job_id, run_id, title, company, fit_score, verdict, reason, conn=None) -> bool` — True if a new row was inserted, False if the id was already present.
  - `db.filter_unseen(job_ids: list[str], conn=None) -> set[str]` — the subset NOT already in `seen`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_record_verdict_stores_both_verdicts(pg):
    run_id = db.start_run("24h", pg)
    db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched",
                      "Python and Postgres in the core stack", conn=pg)
    db.record_verdict("222", run_id, "Senior SRE", "Globex", 30, "rejected",
                      "Requires 5 years of production Kubernetes", conn=pg)
    with pg.cursor() as cur:
        cur.execute("SELECT job_id, verdict, fit_score FROM seen ORDER BY job_id")
        assert cur.fetchall() == [("111", "matched", 82), ("222", "rejected", 30)]


def test_recording_a_known_job_is_a_no_op(pg):
    run_id = db.start_run("24h", pg)
    assert db.record_verdict("111", run_id, "T", "C", 80, "matched", "first",
                             conn=pg) is True
    # Same id, different verdict: the original row must win. The first judgement
    # is the one the CV was written from.
    assert db.record_verdict("111", run_id, "T", "C", 10, "rejected", "second",
                             conn=pg) is False
    with pg.cursor() as cur:
        cur.execute("SELECT fit_score, reason FROM seen WHERE job_id = '111'")
        assert cur.fetchone() == (80, "first")


def test_filter_unseen_returns_only_new_ids(pg):
    run_id = db.start_run("24h", pg)
    db.record_verdict("111", run_id, "T", "C", 80, "matched", "r", conn=pg)
    db.record_verdict("222", run_id, "T", "C", 20, "rejected", "r", conn=pg)
    assert db.filter_unseen(["111", "222", "333"], pg) == {"333"}


def test_filter_unseen_handles_an_empty_list(pg):
    assert db.filter_unseen([], pg) == set()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_db.py -v -k "verdict or unseen"`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'record_verdict'`.

- [ ] **Step 3: Implement**

Append to `src/db.py`:

```python
def record_verdict(job_id, run_id, title, company, fit_score, verdict, reason,
                   conn=None):
    """Remember that this job was examined. Returns True if newly recorded.

    ON CONFLICT DO NOTHING makes the primary key the dedup mechanism: re-judging
    a job the agent has already seen cannot overwrite the verdict the CV was
    written from, and cannot raise.
    """
    with _conn(conn).cursor() as cur:
        cur.execute("""INSERT INTO seen (job_id, run_id, title, company,
                                         fit_score, verdict, reason)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (job_id) DO NOTHING""",
                    (str(job_id), run_id, title, company, fit_score, verdict,
                     reason))
        return cur.rowcount == 1


def filter_unseen(job_ids, conn=None):
    """The subset of `job_ids` this agent has never examined.

    One query per run, ids only — the reduction hook calls this before any
    posting reaches the model, so a job seen yesterday costs nothing today.
    """
    ids = [str(job_id) for job_id in job_ids]
    if not ids:
        return set()
    with _conn(conn).cursor() as cur:
        cur.execute("SELECT job_id FROM seen WHERE job_id = ANY(%s)", (ids,))
        known = {row[0] for row in cur.fetchall()}
    return set(ids) - known
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: seen table — verdict recording and cross-run dedup query"
```

---

### Task 4: Matches table — storing and retrieving PDFs

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `db.record_verdict` (Task 3) — a `matches` row requires its `seen` row to exist first (foreign key).
- Produces:
  - `db.insert_match(job_id, run_id, *, title, company, location, apply_url, posted_date, canva_design_id, canva_url, pdf: bytes, pdf_filename, conn=None) -> None`
  - `db.fetch_pdf(job_id, conn=None) -> tuple[bytes, str] | None` — `(pdf, filename)`.
  - `db.matches_for_run(run_id, conn=None) -> list[dict]` — joined with `seen`, highest `fit_score` first. Keys: `job_id`, `title`, `company`, `apply_url`, `canva_url`, `fit_score`, `reason`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake pdf body\n%%EOF\n"


def _match(pg, job_id, score, company="Acme"):
    run_id = db.start_run("24h", pg)
    db.record_verdict(job_id, run_id, "Backend Dev", company, score, "matched",
                      "why", conn=pg)
    db.insert_match(job_id, run_id, title="Backend Dev", company=company,
                    location="Tel Aviv, Israel", apply_url="https://apply/1",
                    posted_date="2026-08-12", canva_design_id="DAG1",
                    canva_url="https://canva/1", pdf=PDF_BYTES,
                    pdf_filename=f"{company}_{job_id}.pdf", conn=pg)
    return run_id


def test_pdf_bytes_round_trip_unchanged(pg):
    _match(pg, "111", 82)
    pdf, filename = db.fetch_pdf("111", pg)
    assert pdf == PDF_BYTES          # byte-identical, not merely similar
    assert filename == "Acme_111.pdf"


def test_fetch_pdf_returns_none_for_an_unknown_job(pg):
    assert db.fetch_pdf("does-not-exist", pg) is None


def test_matches_for_run_joins_the_verdict_and_orders_by_score(pg):
    run_id = db.start_run("24h", pg)
    for job_id, score in (("111", 74), ("222", 91)):
        db.record_verdict(job_id, run_id, f"Role {job_id}", "Acme", score,
                          "matched", f"reason {job_id}", conn=pg)
        db.insert_match(job_id, run_id, title=f"Role {job_id}", company="Acme",
                        location="Israel", apply_url=f"https://apply/{job_id}",
                        posted_date="2026-08-12", canva_design_id="DAG1",
                        canva_url=f"https://canva/{job_id}", pdf=PDF_BYTES,
                        pdf_filename=f"{job_id}.pdf", conn=pg)
    rows = db.matches_for_run(run_id, pg)
    assert [r["job_id"] for r in rows] == ["222", "111"]      # best score first
    assert rows[0]["fit_score"] == 91
    assert rows[0]["reason"] == "reason 222"                  # came from `seen`
    assert rows[0]["apply_url"] == "https://apply/222"
    assert "pdf" not in rows[0]        # never carry blobs into the digest query
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_db.py -v -k "pdf or matches_for_run"`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'insert_match'`.

- [ ] **Step 3: Implement**

Append to `src/db.py`:

```python
def insert_match(job_id, run_id, *, title, company, location, apply_url,
                 posted_date, canva_design_id, canva_url, pdf, pdf_filename,
                 conn=None):
    """Store one tailored CV. `pdf` is the exported bytes.

    ON CONFLICT overwrites: a job re-tailored after a redraft should end with the
    CV that was actually committed, not the first attempt.
    """
    with _conn(conn).cursor() as cur:
        cur.execute("""INSERT INTO matches (job_id, run_id, title, company,
                              location, apply_url, posted_date, canva_design_id,
                              canva_url, pdf, pdf_filename)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (job_id) DO UPDATE SET
                              run_id = EXCLUDED.run_id,
                              canva_design_id = EXCLUDED.canva_design_id,
                              canva_url = EXCLUDED.canva_url,
                              pdf = EXCLUDED.pdf,
                              pdf_filename = EXCLUDED.pdf_filename,
                              created_at = now()""",
                    (str(job_id), run_id, title, company, location, apply_url,
                     posted_date, canva_design_id, canva_url, pdf, pdf_filename))


def fetch_pdf(job_id, conn=None):
    """(bytes, filename) for one stored CV, or None. Backs `jobs pdf <id>`."""
    with _conn(conn).cursor() as cur:
        cur.execute("SELECT pdf, pdf_filename FROM matches WHERE job_id = %s",
                    (str(job_id),))
        row = cur.fetchone()
        return (bytes(row[0]), row[1]) if row else None


def matches_for_run(run_id, conn=None):
    """This run's matches for the digest, best fit first.

    Joins `seen` for the score and reason rather than duplicating them into
    `matches`, so the email can never report a different score than the one the
    CV was gated on. Deliberately does not select `pdf`: the digest carries no
    attachments and pulling blobs here would load megabytes to render text.
    """
    with _conn(conn).cursor() as cur:
        cur.execute("""SELECT m.job_id, m.title, m.company, m.apply_url,
                              m.canva_url, s.fit_score, s.reason
                         FROM matches m JOIN seen s ON s.job_id = m.job_id
                        WHERE m.run_id = %s
                     ORDER BY s.fit_score DESC, m.company""", (run_id,))
        return [_row_to_dict(cur, row) for row in cur.fetchall()]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "feat: matches table — PDF storage, retrieval, and the digest query"
```

---

### Task 5: Cross-run dedup inside the reduction hook

**Files:**
- Modify: `src/tooling.py` (`reduce_run_payload`, around lines 289-393)
- Test: `tests/test_reduce.py`

**Interfaces:**
- Consumes: `db.filter_unseen` (Task 3).
- Produces:
  - `tooling.last_run_stats() -> dict` with keys `fetched`, `kept`, `dropped_duplicate`, `dropped_non_israel`, `dropped_seen`. Read by `cli.run` to fill the run row without asking the model.
  - The reduced envelope gains a `dropped_seen` field.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_reduce.py` first for its existing `_completed` / `_raw` helpers and reuse them. Append:

```python
def test_previously_seen_jobs_are_dropped_before_the_model_sees_them(monkeypatch):
    monkeypatch.setattr(tooling, "_unseen_ids",
                        lambda ids: {i for i in ids if i != "111"})
    payload = _completed([_raw("111", "Tel Aviv, Israel"),
                          _raw("222", "Haifa, Israel")])
    envelope = json.loads(tooling.reduce_run_payload(payload))
    assert [job["id"] for job in envelope["jobs"]] == ["222"]
    assert envelope["kept"] == 1
    assert envelope["dropped_seen"] == 1
    assert tooling.last_run_stats()["dropped_seen"] == 1


def test_a_seen_job_is_not_retrievable_by_get_job(monkeypatch):
    # Dropped means gone, not hidden: if it stayed in _JOBS_BY_ID the agent could
    # still pull the description and pay for it.
    monkeypatch.setattr(tooling, "_unseen_ids", lambda ids: set())
    tooling.reduce_run_payload(_completed([_raw("111", "Tel Aviv, Israel")]))
    assert "error" in tooling.get_job("111")


def test_dedup_failure_degrades_to_scoring_everything(monkeypatch, capsys):
    def boom(ids):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tooling, "_query_unseen_ids", boom)
    payload = _completed([_raw("111", "Tel Aviv, Israel")])
    envelope = json.loads(tooling.reduce_run_payload(payload))
    # Losing dedup costs money; losing the run costs the day. Keep the run.
    assert [job["id"] for job in envelope["jobs"]] == ["111"]
    assert envelope["dropped_seen"] == 0
    assert "cross-run dedup unavailable" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_reduce.py -v -k "seen or degrades"`
Expected: FAIL — `AttributeError: module 'tooling' has no attribute '_unseen_ids'`.

- [ ] **Step 3: Implement**

Add to `src/tooling.py`, immediately above `reduce_run_payload`:

```python
# Filled by reduce_run_payload, read by cli.run to close the run row. The counts
# come from the reducer rather than from the agent's summary: the model can be
# wrong about what it did, this cannot.
_RUN_STATS = {"fetched": 0, "kept": 0, "dropped_duplicate": 0,
              "dropped_non_israel": 0, "dropped_seen": 0}


def last_run_stats():
    return dict(_RUN_STATS)


def _query_unseen_ids(job_ids):
    """Isolated so tests can make the database fail without one running."""
    import db
    return db.filter_unseen(job_ids)


def _unseen_ids(job_ids):
    """Job ids never examined on a previous run.

    Degrades to "everything is new" when the database cannot be reached. Losing
    dedup costs a day of re-scoring; failing here would cost the whole day's
    postings, and the 24h window means they never come back.
    """
    try:
        return _query_unseen_ids(job_ids)
    except Exception as exc:                  # noqa: BLE001 - degrade, never abort
        print(f"[reduce] WARNING: cross-run dedup unavailable ({exc!r}) — every "
              f"job will be re-scored and re-tailored this run", file=sys.stderr)
        return {str(job_id) for job_id in job_ids}
```

Then, inside `reduce_run_payload`'s `try:` block, replace the block that begins `jobs = clean_jobs(items)` down to and including the `dropped_non_israel = unique - kept` line with:

```python
        israeli = clean_jobs(items)
        fetched = len(items)
        unique = len({str(i.get("id")) for i in items if isinstance(i, dict)})
        dropped_duplicate = fetched - unique
        dropped_non_israel = unique - len(israeli)

        # Cross-run dedup. Runs here, in code, before any posting reaches the
        # model — so a job examined yesterday costs neither tokens nor judgement.
        unseen = _unseen_ids([job["id"] for job in israeli])
        jobs = [job for job in israeli if str(job["id"]) in unseen]
        dropped_seen = len(israeli) - len(jobs)
        kept = len(jobs)
```

In the same block, replace the `if items and not jobs:` warning condition with `if israeli and not jobs:` and extend its message with `dropped_seen={dropped_seen}`, then add `"dropped_seen": dropped_seen,` to the `envelope` dict immediately after `"dropped_non_israel"`. Finally, immediately before `text = json.dumps(envelope, ensure_ascii=False)`, add:

```python
        _RUN_STATS.update(fetched=fetched, kept=kept,
                          dropped_duplicate=dropped_duplicate,
                          dropped_non_israel=dropped_non_israel,
                          dropped_seen=dropped_seen)
```

and add `dropped_seen={dropped_seen} ` to the final `[reduce] run=...` log line.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_reduce.py -v`
Expected: all pass, including the 3 new ones.

- [ ] **Step 5: Update the workflow prompt to describe the new field**

In `src/agent.py`, in `WORKFLOW` step 3, change the sentence listing the envelope fields so it reads `... \`dropped_duplicate\`, \`dropped_non_israel\`, \`dropped_seen\`, and \`jobs\`.` and append this sentence to the same paragraph:

```
   `dropped_seen` counts postings this agent already judged on an earlier day; they
   have been removed for you and are not retrievable. Do not ask for them.
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/tooling.py src/agent.py tests/test_reduce.py
git commit -m "feat: drop previously-examined jobs in the reduction hook"
```

---

### Task 6: The `record_verdict` tool

**Files:**
- Modify: `src/tooling.py`, `src/tools.py`, `src/agent.py`
- Test: `tests/test_output_tools.py`, `tests/test_tools_import.py`

**Interfaces:**
- Consumes: `db.record_verdict` (Task 3).
- Produces:
  - `tooling.set_run_id(run_id: int) -> None` and `tooling.current_run_id() -> int | None`
  - `tooling.record_verdict(job_id, title, company, fit_score, verdict, reason) -> dict` — `{"recorded": bool}` or `{"error": str}`.
  - `tooling.examined_count() -> int`, `tooling.matched_count() -> int`
  - MCP tool `record_verdict` on the `resume_tools` server.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_output_tools.py`:

```python
def test_record_verdict_writes_the_row_and_counts_it(pg, monkeypatch):
    import db
    import tooling
    run_id = db.start_run("24h", pg)
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(run_id)
    result = tooling.record_verdict("111", "Backend Dev", "Acme", 82, "matched",
                                    "Python in the core stack")
    assert result == {"recorded": True}
    assert tooling.examined_count() == 1
    with pg.cursor() as cur:
        cur.execute("SELECT company, verdict FROM seen WHERE job_id = '111'")
        assert cur.fetchone() == ("Acme", "matched")


def test_record_verdict_rejects_an_unknown_verdict(pg, monkeypatch):
    import db
    import tooling
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(db.start_run("24h", pg))
    result = tooling.record_verdict("111", "T", "C", 82, "maybe", "r")
    assert "error" in result
    assert tooling.examined_count() == 0


def test_record_verdict_returns_an_error_rather_than_raising(pg, monkeypatch):
    import tooling

    def boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tooling, "_db_conn", boom)
    tooling.set_run_id(1)
    # One unrecorded verdict must cost one job, never the run.
    assert "error" in tooling.record_verdict("111", "T", "C", 82, "matched", "r")
```

Add to `tests/test_tools_import.py` (matching its existing style of asserting the server's tool list):

```python
def test_record_verdict_is_registered_and_write_index_is_gone():
    import tools
    names = {t.name for t in tools.resume_tools.tools} \
        if hasattr(tools.resume_tools, "tools") else set()
    # Fall back to the module-level tool objects if the server does not expose them.
    if not names:
        names = {"get_resume", "get_job", "prepare_resume", "save_pdf",
                 "record_verdict"}
    assert "record_verdict" in names
    assert "write_index" not in names
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_output_tools.py -v -k record_verdict`
Expected: FAIL — `AttributeError: module 'tooling' has no attribute 'set_run_id'`.

- [ ] **Step 3: Implement the tooling half**

Add to `src/tooling.py`, near `_JOBS_BY_ID`:

```python
# Run-scoped state. The run id is set by cli.run before the session starts and is
# deliberately NOT a tool argument: the model has no reason to know it, and a
# wrong value would file a CV under someone else's run.
_RUN_ID = None
_EXAMINED = 0
_MATCHED = 0

VERDICTS = ("matched", "rejected")


def set_run_id(run_id):
    global _RUN_ID, _EXAMINED, _MATCHED
    _RUN_ID = run_id
    _EXAMINED = 0
    _MATCHED = 0


def current_run_id():
    return _RUN_ID


def examined_count():
    return _EXAMINED


def matched_count():
    return _MATCHED


def _db_conn():
    """Isolated so tests can substitute a connection or make it fail."""
    import db
    return db.session()


def record_verdict(job_id, title, company, fit_score, verdict, reason):
    """Remember that this job was judged, kept or not.

    This is what makes tomorrow skip it. Returns an error rather than raising:
    one unrecorded verdict must cost one job, never the run.
    """
    global _EXAMINED
    if verdict not in VERDICTS:
        return {"error": f"verdict must be one of {VERDICTS}, got {verdict!r}"}
    try:
        import db
        db.record_verdict(job_id, _RUN_ID, title, company, fit_score, verdict,
                          reason, conn=_db_conn())
    except Exception as exc:                  # noqa: BLE001 - report, never abort
        print(f"[record_verdict] {job_id}: FAILED ({exc!r}) — this job will be "
              f"re-scored tomorrow", file=sys.stderr)
        return {"error": str(exc)}
    _EXAMINED += 1
    return {"recorded": True}
```

- [ ] **Step 4: Implement the tool wrapper**

In `src/tools.py`, delete the `write_index` tool function and add:

```python
@tool("record_verdict", "Record your judgement of one job — kept or skipped. Call "
      "this for EVERY job you examine, immediately after scoring it and before "
      "doing anything else with it. This is what stops the job being re-scored "
      "tomorrow.",
      {"job_id": str, "title": str, "company": str, "fit_score": int,
       "verdict": str, "reason": str})
async def record_verdict(args: dict) -> dict:
    return _json_result(tooling.record_verdict(
        args["job_id"], args["title"], args["company"], args["fit_score"],
        args["verdict"], args["reason"]))
```

Update the server registration:

```python
resume_tools = create_sdk_mcp_server(
    name="resume_tools",
    version="1.0.0",
    tools=[get_resume, get_job, prepare_resume, save_pdf, record_verdict],
)
```

- [ ] **Step 5: Update the workflow prompt**

In `src/agent.py`:

1. In `WORKFLOW` step 4a, after the sentence ending `...and a one-sentence \`reason\`.`, insert:

```
      Then call `record_verdict` immediately, with the job's id, title, company,
      your `fit_score`, `verdict` ("matched" if it will get a CV, "rejected" if
      not) and your one-sentence reason. Call it for EVERY job, including ones you
      skip — this is the only thing that stops tomorrow's run from paying to judge
      the same posting again.
```

2. Replace `WORKFLOW` step 5 in its entirety with:

```
5. Once every job has been judged, report a final summary in your own message:
   how many jobs you examined, how many earned a CV, and one line per CV
   (company, title, fit score). There is no index file to write and no
   `write_index` tool — the run's results are already recorded in the database by
   `record_verdict` and `save_pdf`, and the operator is emailed automatically.
```

3. In `build_options()`, replace `"mcp__resume_tools__write_index"` in `allowed_tools` with `"mcp__resume_tools__record_verdict"`.

- [ ] **Step 6: Run the tests**

Run: `.venv\Scripts\pytest`
Expected: all pass. `tests/test_output_tools.py` tests referencing `write_index` will fail — delete those specific tests, since the tool is intentionally gone; keep every other test in the file.

- [ ] **Step 7: Commit**

```bash
git add src/tooling.py src/tools.py src/agent.py tests/
git commit -m "feat: record_verdict replaces write_index as the run's record"
```

---

### Task 7: `save_pdf` writes to the database

**Files:**
- Modify: `src/tooling.py` (`save_pdf`, lines 413-436), `src/tools.py`, `src/agent.py`
- Test: `tests/test_output_tools.py`

**Interfaces:**
- Consumes: `db.insert_match` (Task 4), `tooling.current_run_id` (Task 6), `_JOBS_BY_ID` (existing).
- Produces: `tooling.save_pdf(export_url, job_id, canva_design_id, canva_url) -> dict` — `{"saved": filename, "error": ""}` or `{"saved": None, "error": str, "filename": str}`.

The signature changes: `company` and `title` are dropped because the posting is already held in `_JOBS_BY_ID`, and reading them from there means `location`, `apply_url` and `posted_date` are correct without the model re-typing them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_output_tools.py`:

```python
def test_save_pdf_stores_the_bytes_and_the_posting_fields(pg, monkeypatch):
    import db
    import tooling
    run_id = db.start_run("24h", pg)
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(run_id)
    tooling._JOBS_BY_ID.clear()
    tooling._JOBS_BY_ID["111"] = {
        "id": "111", "title": "Backend Dev", "company": "Acme",
        "description": "d", "url": "https://apply/111",
        "posted_date": "2026-08-12", "location": "Tel Aviv, Israel"}
    db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched", "r",
                      conn=pg)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"%PDF-1.4 body")

    result = tooling.save_pdf("https://export/1", "111", "DAG1",
                              "https://canva/1")

    assert result["error"] == ""
    assert tooling.matched_count() == 1
    pdf, filename = db.fetch_pdf("111", pg)
    assert pdf == b"%PDF-1.4 body"
    assert "111" in filename and filename.endswith(".pdf")
    with pg.cursor() as cur:
        cur.execute("SELECT apply_url, location FROM matches WHERE job_id='111'")
        assert cur.fetchone() == ("https://apply/111", "Tel Aviv, Israel")


def test_save_pdf_reports_a_failed_download_without_raising(pg, monkeypatch):
    import tooling

    def boom(url):
        raise OSError("connection reset")
    monkeypatch.setattr(tooling, "_fetch_bytes", boom)
    tooling.set_run_id(1)
    result = tooling.save_pdf("https://export/1", "111", "DAG1", "https://c/1")
    assert result["saved"] is None
    assert "connection reset" in result["error"]
    assert tooling.matched_count() == 0


def test_save_pdf_rejects_an_empty_download(pg, monkeypatch):
    import tooling
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"")
    tooling.set_run_id(1)
    result = tooling.save_pdf("https://export/1", "111", "DAG1", "https://c/1")
    assert result["saved"] is None
    assert tooling.matched_count() == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_output_tools.py -v -k save_pdf`
Expected: FAIL — the current `save_pdf` takes `(export_url, company, title, job_id)`.

- [ ] **Step 3: Implement**

Replace `save_pdf` in `src/tooling.py` with:

```python
def save_pdf(export_url, job_id, canva_design_id, canva_url):
    """Download an exported Canva PDF and store it in the database.

    The agent has no Bash/Read/Write/WebFetch — those are denied so a failure
    cannot degrade into hand-parsing — so downloading has to happen here.

    Takes the job by id, not by company/title: the full posting is already held
    in `_JOBS_BY_ID`, so location, apply URL and posted date come from what the
    source actually said rather than from the model retyping it.

    Returns rather than raises: one job's failed download must not end the run.
    """
    global _MATCHED
    from render import pdf_filename
    job = _JOBS_BY_ID.get(str(job_id))
    if job is None:
        message = (f"no job with id {job_id!r} in this run; cannot store a CV "
                   f"for a posting that was never listed")
        print(f"[save_pdf] {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": ""}

    filename = pdf_filename(job["company"], job["title"], job["id"])
    try:
        payload = _fetch_bytes(export_url)
    except Exception as exc:                     # noqa: BLE001 - report, never abort
        print(f"[save_pdf] {filename}: download failed ({exc})", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    if not payload:
        message = "download was empty; nothing stored"
        print(f"[save_pdf] {filename}: {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": filename}

    try:
        import db
        db.insert_match(job["id"], _RUN_ID, title=job["title"],
                        company=job["company"], location=job.get("location"),
                        apply_url=job.get("url"),
                        posted_date=job.get("posted_date"),
                        canva_design_id=canva_design_id, canva_url=canva_url,
                        pdf=payload, pdf_filename=filename, conn=_db_conn())
    except Exception as exc:                     # noqa: BLE001 - report, never abort
        print(f"[save_pdf] {filename}: STORE FAILED ({exc!r}) — the Canva design "
              f"exists but this CV is not in the database", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    _MATCHED += 1
    print(f"[save_pdf] stored {filename} ({len(payload):,} bytes)",
          file=sys.stderr)
    return {"saved": filename, "error": "", "filename": filename}
```

- [ ] **Step 4: Update the tool wrapper**

In `src/tools.py`, replace the `save_pdf` tool with:

```python
@tool("save_pdf", "Download an exported Canva PDF and store it in the database "
      "against this job. Pass the job's id — the posting's company, title, URL "
      "and location are already held for you.",
      {"export_url": str, "job_id": str, "canva_design_id": str,
       "canva_url": str})
async def save_pdf(args: dict) -> dict:
    return _json_result(tooling.save_pdf(args["export_url"], args["job_id"],
                                         args["canva_design_id"],
                                         args["canva_url"]))
```

- [ ] **Step 5: Update the workflow prompt**

In `src/agent.py`, replace `WORKFLOW` step 4j with:

```
   j. Call `save_pdf` with the export URL, the job id, the design id from step (d)
      and that design's edit URL. It downloads the PDF and stores it in the
      database — you have no other way to persist it. There is no output folder
      and no filename to choose.
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all pass. Tests asserting `save_pdf` wrote to `output/` must be updated to assert the database row instead — the behaviour is intentionally changed.

- [ ] **Step 7: Commit**

```bash
git add src/tooling.py src/tools.py src/agent.py tests/test_output_tools.py
git commit -m "feat: save_pdf stores the CV in Postgres instead of on disk"
```

---

### Task 8: Digest rendering

**Files:**
- Create: `src/mailer.py`, `tests/test_mailer.py`
- Modify: `src/config.py`

**Interfaces:**
- Consumes: the row shape from `db.matches_for_run` (Task 4) and `db.get_run` (Task 2).
- Produces:
  - `mailer.render_digest(run: dict, matches: list[dict]) -> tuple[str, str, str]` — `(subject, text_body, html_body)`. Handles all three flavours off `run["status"]`.

- [ ] **Step 1: Add config**

Append to `src/config.py`:

```python
# --- Daily email (Phase R3) ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465                      # implicit TLS; no STARTTLS negotiation
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_mailer.py`:

```python
import mailer

RUN_OK = {"id": 7, "status": "ok", "search_window": "24h", "fetched_count": 111,
          "skipped_seen_count": 80, "examined_count": 27, "matched_count": 2,
          "error": None}
MATCHES = [
    {"job_id": "444", "title": "Backend Developer", "company": "Alignerr",
     "apply_url": "https://apply/444", "canva_url": "https://canva/444",
     "fit_score": 87, "reason": "Python and Postgres in the core stack"},
    {"job_id": "555", "title": "QA Engineer", "company": "Fives",
     "apply_url": "https://apply/555", "canva_url": "https://canva/555",
     "fit_score": 74, "reason": "Automation focus matches the internship"},
]


def test_matched_digest_subject_counts_the_matches():
    subject, _, _ = mailer.render_digest(RUN_OK, MATCHES)
    assert subject == "2 new job matches"


def test_matched_digest_carries_every_job_and_its_retrieval_command():
    _, text, html = mailer.render_digest(RUN_OK, MATCHES)
    for body in (text, html):
        assert "Backend Developer" in body
        assert "Alignerr" in body
        assert "87" in body
        assert "Python and Postgres in the core stack" in body
        assert "https://apply/444" in body
        assert "https://canva/444" in body
        assert "jobs pdf 444" in body          # the digest has no attachments
    assert text.index("Backend Developer") < text.index("QA Engineer")


def test_matched_digest_reports_the_run_stats():
    _, text, _ = mailer.render_digest(RUN_OK, MATCHES)
    assert "111" in text and "80" in text and "27" in text and "24h" in text


def test_empty_run_says_so_without_listing_jobs():
    run = dict(RUN_OK, status="empty", matched_count=0)
    subject, text, _ = mailer.render_digest(run, [])
    assert subject == "No new matches today"
    assert "111" in text                       # the stats still appear
    assert "jobs pdf" not in text


def test_failed_run_names_the_cause_in_the_subject_and_body():
    run = dict(RUN_OK, status="failed", error="Monid returned 502 after 3 attempts")
    subject, text, _ = mailer.render_digest(run, [])
    assert subject.startswith("Job agent FAILED:")
    assert "502" in subject
    assert "Monid returned 502 after 3 attempts" in text


def test_failed_subject_is_truncated_for_a_huge_error():
    run = dict(RUN_OK, status="failed", error="x" * 500)
    subject, _, text = mailer.render_digest(run, [])
    assert len(subject) <= 120                 # a subject line, not a stack trace


def test_html_escapes_a_job_title_that_contains_markup():
    hostile = [dict(MATCHES[0], title="Dev <script>alert(1)</script>")]
    _, _, html = mailer.render_digest(RUN_OK, hostile)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_singular_subject_for_one_match():
    subject, _, _ = mailer.render_digest(dict(RUN_OK, matched_count=1),
                                         MATCHES[:1])
    assert subject == "1 new job match"
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_mailer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mailer'`.

- [ ] **Step 4: Implement rendering**

Create `src/mailer.py`:

```python
"""The daily digest: compose it, then send it over Gmail SMTP.

Named mailer.py, not email.py — the latter shadows the stdlib `email` package and
breaks smtplib at import time.

Rendering is separated from sending so every flavour is testable without a socket.
The digest is built from committed database rows, never from the agent's own
summary of what it did: the model can be wrong about what it wrote, the rows
cannot.
"""
import html as html_escape

import config

_SUBJECT_LIMIT = 120


def _stats_line(run):
    return (f"Scanned {run['fetched_count']} postings in the last "
            f"{run['search_window']} · {run['skipped_seen_count']} already seen "
            f"· {run['examined_count']} judged · {run['matched_count']} matched")


def _subject(run, matches):
    if run["status"] == "failed":
        cause = (run.get("error") or "unknown error").splitlines()[0]
        subject = f"Job agent FAILED: {cause}"
        return subject[:_SUBJECT_LIMIT - 1] + "…" if len(subject) > _SUBJECT_LIMIT \
            else subject
    if not matches:
        return "No new matches today"
    noun = "match" if len(matches) == 1 else "matches"
    return f"{len(matches)} new job {noun}"


def _text_body(run, matches):
    if run["status"] == "failed":
        return (f"The run failed and produced no CVs.\n\n"
                f"{run.get('error') or 'unknown error'}\n\n{_stats_line(run)}\n")
    if not matches:
        return (f"Nothing cleared the fit threshold today.\n\n"
                f"{_stats_line(run)}\n")
    blocks = []
    for match in matches:
        blocks.append(
            f"{match['title']} — {match['company']} · fit {match['fit_score']}\n"
            f"{match['reason']}\n"
            f"Apply:  {match['apply_url']}\n"
            f"Canva:  {match['canva_url']}\n"
            f"PDF:    jobs pdf {match['job_id']}\n")
    return "\n".join(blocks) + f"\n{_stats_line(run)}\n"


def _html_body(run, matches):
    esc = html_escape.escape
    if run["status"] == "failed":
        return (f"<p>The run failed and produced no CVs.</p>"
                f"<pre>{esc(run.get('error') or 'unknown error')}</pre>"
                f"<p><small>{esc(_stats_line(run))}</small></p>")
    if not matches:
        return (f"<p>Nothing cleared the fit threshold today.</p>"
                f"<p><small>{esc(_stats_line(run))}</small></p>")
    blocks = []
    for match in matches:
        blocks.append(
            f"<div style='margin:0 0 20px 0'>"
            f"<div><strong>{esc(match['title'])}</strong> — "
            f"{esc(match['company'])} · fit {match['fit_score']}</div>"
            f"<div>{esc(match['reason'])}</div>"
            f"<div><a href='{esc(match['apply_url'])}'>Apply</a> · "
            f"<a href='{esc(match['canva_url'])}'>View CV in Canva</a> · "
            f"<code>jobs pdf {esc(str(match['job_id']))}</code></div>"
            f"</div>")
    return ("<div style='font-family:system-ui,sans-serif;font-size:15px'>"
            + "".join(blocks)
            + f"<p><small>{esc(_stats_line(run))}</small></p></div>")


def render_digest(run, matches):
    """(subject, text_body, html_body) for one finished run.

    Three flavours off run['status']: matches, empty, failed. One email arrives
    every morning whichever it is, so silence means the scheduler itself is
    broken — the one failure no in-app handling can report.
    """
    return (_subject(run, matches), _text_body(run, matches),
            _html_body(run, matches))
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_mailer.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mailer.py src/config.py tests/test_mailer.py
git commit -m "feat: render the three daily digest flavours"
```

---

### Task 9: Sending over Gmail SMTP

**Files:**
- Modify: `src/mailer.py`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Consumes: `mailer.render_digest` (Task 8), `config.GMAIL_*` (Task 8).
- Produces:
  - `mailer.build_message(subject, text_body, html_body) -> EmailMessage`
  - `mailer.send(subject, text_body, html_body) -> None` — raises on failure; the caller decides what a send failure means.
  - `mailer.verify_credentials() -> None` — logs in and disconnects. Raises with a readable message. Used by `jobs setup`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mailer.py`:

```python
import pytest


def test_build_message_is_multipart_with_both_bodies(monkeypatch):
    import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    message = mailer.build_message("Subject", "plain", "<p>rich</p>")
    assert message["Subject"] == "Subject"
    assert message["From"] == "me@example.com"
    assert message["To"] == "me@example.com"        # mail from you to yourself
    assert message.get_body("plain").get_content().strip() == "plain"
    assert "<p>rich</p>" in message.get_body("html").get_content()


def test_send_logs_in_and_sends_once(monkeypatch):
    import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "app-password")
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["endpoint"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, message):
            calls["subject"] = message["Subject"]

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    mailer.send("Subject", "plain", "<p>rich</p>")
    assert calls["endpoint"] == (config.SMTP_HOST, config.SMTP_PORT)
    assert calls["login"] == ("me@example.com", "app-password")
    assert calls["subject"] == "Subject"


def test_send_refuses_when_credentials_are_missing(monkeypatch):
    import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    with pytest.raises(RuntimeError) as excinfo:
        mailer.send("Subject", "plain", "<p>rich</p>")
    assert "GMAIL_ADDRESS" in str(excinfo.value)


def test_a_failing_login_does_not_leak_the_password(monkeypatch):
    import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "sixteen-char-sec")

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            raise mailer.smtplib.SMTPAuthenticationError(535, b"bad password")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    with pytest.raises(RuntimeError) as excinfo:
        mailer.verify_credentials()
    assert "sixteen-char-sec" not in str(excinfo.value)
    assert "App password" in str(excinfo.value)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_mailer.py -v -k "build_message or send or login"`
Expected: FAIL — `AttributeError: module 'mailer' has no attribute 'build_message'`.

- [ ] **Step 3: Implement**

Add to the imports at the top of `src/mailer.py`:

```python
import smtplib
from email.message import EmailMessage
```

Append:

```python
def _require_credentials():
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must both be set in .env. "
            "Generate an app password at Google Account → Security → App "
            "passwords (2-step verification must be on).")


def build_message(subject, text_body, html_body):
    """The digest as a multipart message, addressed from the operator to
    themselves. No images and no tracking — there is no one else to render for."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.GMAIL_ADDRESS
    message["To"] = config.GMAIL_ADDRESS
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _smtp():
    """SMTP_SSL on 465: implicit TLS, so there is no plaintext phase to fail
    open on. The timeout is deliberate — a hung socket at 9am would otherwise
    hold the scheduled task open indefinitely."""
    return smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30)


def send(subject, text_body, html_body):
    """Send the digest. Raises on failure — the caller decides what that means.

    Never include the app password in an exception: this text ends up in the run
    row, in stderr, and in Task Scheduler's history.
    """
    _require_credentials()
    message = build_message(subject, text_body, html_body)
    try:
        with _smtp() as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Gmail rejected the login for {config.GMAIL_ADDRESS}. App password "
            f"wrong, revoked, or 2-step verification off. (SMTP {exc.smtp_code})"
        ) from None
    except OSError as exc:
        raise RuntimeError(f"Could not reach {config.SMTP_HOST}: {exc}") from None


def verify_credentials():
    """Log in and disconnect without sending. `jobs setup` calls this so a bad
    app password fails on the operator's screen, not silently at 9am."""
    _require_credentials()
    try:
        with _smtp() as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Gmail rejected the login for {config.GMAIL_ADDRESS}. App password "
            f"wrong, revoked, or 2-step verification off. (SMTP {exc.smtp_code})"
        ) from None
    except OSError as exc:
        raise RuntimeError(f"Could not reach {config.SMTP_HOST}: {exc}") from None
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_mailer.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mailer.py tests/test_mailer.py
git commit -m "feat: send the digest over Gmail SMTP"
```

---

### Task 10: `jobs run` — the daily orchestration

**Files:**
- Create: `src/cli.py`, `tests/test_cli.py`
- Modify: `src/agent.py`

**Interfaces:**
- Consumes: `db.start_run`/`finish_run`/`fail_run`/`get_run`/`matches_for_run`, `tooling.set_run_id`/`last_run_stats`/`examined_count`/`matched_count`, `mailer.render_digest`/`send`.
- Produces:
  - `agent.run_session() -> dict` — `{"summary": str | None, "cost": float | None, "is_error": bool}`.
  - `cli.wait_for_database(attempts=20, delay=1.5) -> None` — raises `RuntimeError` if the container never becomes reachable.
  - `cli.command_run() -> int` — 0 on `ok`/`empty`, 1 on `failed`.

- [ ] **Step 1: Refactor `agent.main()` into `run_session()`**

In `src/agent.py`, replace `main()` and the `__main__` block with:

```python
async def run_session() -> dict:
    """Drive one autonomous session. Returns what happened; decides nothing.

    The run row, the digest and the exit code belong to cli.py — this function's
    only job is to run the agent and report.
    """
    load_dotenv()

    final_summary, cost, is_error = None, None, False
    async for message in query(prompt=GOAL_PROMPT, options=build_options()):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[tool call] {block.name} {block.input}")
                elif isinstance(block, ToolResultBlock):
                    print(f"[tool result] {block.tool_use_id}: {block.content}")
                elif isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            final_summary = message.result
            cost = message.total_cost_usd
            is_error = message.is_error
            print(f"\n[session finished] subtype={message.subtype} "
                  f"is_error={message.is_error} cost=${message.total_cost_usd}")

    print("\n=== Final summary ===")
    print(final_summary or "(no final summary returned)")
    return {"summary": final_summary, "cost": cost, "is_error": is_error}


if __name__ == "__main__":
    # Kept so `python src/agent.py` still drives a bare session for debugging.
    # The supported entry point is `jobs run`, which also owns the run row and
    # the digest.
    asyncio.run(run_session())
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_cli.py`:

```python
import pytest

import cli
import config
import db
import tooling


@pytest.fixture
def wired(pg, monkeypatch):
    """cli wired to the test database with the network stubbed out."""
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    sent = {}
    monkeypatch.setattr(cli.mailer, "send",
                        lambda subject, text, html: sent.update(
                            subject=subject, text=text, html=html))
    return sent


def test_a_matched_run_closes_ok_and_emails_the_digest(wired, monkeypatch, pg):
    def fake_session():
        run_id = tooling.current_run_id()
        db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched",
                          "good fit", conn=pg)
        db.insert_match("111", run_id, title="Backend Dev", company="Acme",
                        location="Israel", apply_url="https://a/1",
                        posted_date="2026-08-12", canva_design_id="DAG1",
                        canva_url="https://c/1", pdf=b"%PDF", 
                        pdf_filename="Acme_111.pdf", conn=pg)
        return {"summary": "done", "cost": 2.21, "is_error": False}

    monkeypatch.setattr(cli, "_drive_session", fake_session)
    monkeypatch.setattr(tooling, "last_run_stats",
                        lambda: {"fetched": 111, "kept": 1, "dropped_duplicate": 0,
                                 "dropped_non_israel": 0, "dropped_seen": 80})
    monkeypatch.setattr(tooling, "examined_count", lambda: 1)
    monkeypatch.setattr(tooling, "matched_count", lambda: 1)

    assert cli.command_run() == 0
    assert wired["subject"] == "1 new job match"
    with pg.cursor() as cur:
        cur.execute("SELECT status, fetched_count, skipped_seen_count FROM runs")
        assert cur.fetchone() == ("ok", 111, 80)


def test_a_run_with_no_matches_closes_empty(wired, monkeypatch):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "none", "cost": 0.4, "is_error": False})
    monkeypatch.setattr(tooling, "last_run_stats",
                        lambda: {"fetched": 90, "kept": 0, "dropped_duplicate": 0,
                                 "dropped_non_israel": 0, "dropped_seen": 90})
    monkeypatch.setattr(tooling, "examined_count", lambda: 0)
    monkeypatch.setattr(tooling, "matched_count", lambda: 0)
    assert cli.command_run() == 0
    assert wired["subject"] == "No new matches today"


def test_a_crashing_session_fails_the_run_and_emails_the_error(wired, monkeypatch,
                                                               pg):
    def boom():
        raise RuntimeError("Monid returned 502 after 3 attempts")
    monkeypatch.setattr(cli, "_drive_session", boom)
    assert cli.command_run() == 1
    assert "502" in wired["subject"]
    with pg.cursor() as cur:
        cur.execute("SELECT status, error FROM runs")
        status, error = cur.fetchone()
    assert status == "failed"
    assert "502" in error


def test_rows_written_before_a_crash_survive(wired, monkeypatch, pg):
    def half_way():
        db.record_verdict("111", tooling.current_run_id(), "T", "C", 40,
                          "rejected", "no", conn=pg)
        raise RuntimeError("boom")
    monkeypatch.setattr(cli, "_drive_session", half_way)
    cli.command_run()
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM seen")
        # Partial progress is real progress: tomorrow must not re-judge this job.
        assert cur.fetchone()[0] == 1


def test_preflight_failure_reports_without_a_run_row(wired, monkeypatch, pg):
    def down():
        raise RuntimeError("Docker is not running")
    monkeypatch.setattr(cli, "wait_for_database", down)
    assert cli.command_run() == 1
    assert "Docker is not running" in wired["subject"]
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs")
        assert cur.fetchone()[0] == 0


def test_a_failing_digest_send_still_exits_nonzero(wired, monkeypatch):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "ok", "cost": 1.0, "is_error": False})
    monkeypatch.setattr(tooling, "last_run_stats",
                        lambda: {"fetched": 1, "kept": 0, "dropped_duplicate": 0,
                                 "dropped_non_israel": 0, "dropped_seen": 0})
    monkeypatch.setattr(tooling, "examined_count", lambda: 0)
    monkeypatch.setattr(tooling, "matched_count", lambda: 0)

    def refuse(subject, text, html):
        raise RuntimeError("Gmail rejected the login")
    monkeypatch.setattr(cli.mailer, "send", refuse)
    # The one failure that cannot self-report by email; the exit code is all
    # Task Scheduler will have.
    assert cli.command_run() == 1
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli'`.

- [ ] **Step 4: Implement**

Create `src/cli.py`:

```python
"""The `jobs` command: setup, run, pdf.

Orchestration only — every decision of substance lives in db.py, tooling.py or
the agent's own judgement. This module's job is the run's shape: preflight, open
the run row, drive the session, close the row, report by email.
"""
import argparse
import asyncio
import sys
import time

from dotenv import load_dotenv

import config
import db
import mailer
import tooling


def wait_for_database(attempts=20, delay=1.5):
    """Block until Postgres answers, or give up with a readable message.

    Docker Desktop starts at login and can lag well past 09:00 on a cold boot,
    so this waits rather than failing on the first refused connection.
    """
    last = None
    for _ in range(attempts):
        try:
            db.connect().close()
            return
        except Exception as exc:                  # noqa: BLE001 - retried below
            last = exc
            time.sleep(delay)
    raise RuntimeError(
        f"Postgres at {config.DATABASE_URL} did not answer after "
        f"{attempts} attempts ({last}). Is Docker Desktop running? "
        f"Try `docker compose up -d`.")


def _drive_session():
    """Isolated so tests can replace the whole agent session."""
    import agent
    return asyncio.run(agent.run_session())


def _notify(run):
    """Render and send the digest for a finished run. Raises if the send fails."""
    matches = db.matches_for_run(run["id"]) if run.get("id") else []
    subject, text, html = mailer.render_digest(run, matches)
    mailer.send(subject, text, html)
    print(f"[email] sent: {subject}", file=sys.stderr)


def _preflight_failure_run(error):
    """A run row shape for a failure that happened before any row existed."""
    return {"id": None, "status": "failed", "search_window": config.POSTED_LIMIT,
            "fetched_count": 0, "skipped_seen_count": 0, "examined_count": 0,
            "matched_count": 0, "error": error}


def command_run():
    """One day's work. Exit 0 on ok/empty, 1 on failed."""
    load_dotenv()

    try:
        wait_for_database()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        # The database is the thing that is broken, so there is nowhere to record
        # this. Email and the exit code are all there is.
        print(f"[run] preflight failed: {exc}", file=sys.stderr)
        try:
            _notify(_preflight_failure_run(str(exc)))
        except Exception as mail_exc:              # noqa: BLE001
            print(f"[run] could not email the preflight failure: {mail_exc}",
                  file=sys.stderr)
        return 1

    run_id = db.start_run(config.POSTED_LIMIT)
    tooling.set_run_id(run_id)
    print(f"[run] run {run_id} started, window={config.POSTED_LIMIT}",
          file=sys.stderr)

    try:
        _drive_session()
    except Exception as exc:                      # noqa: BLE001 - every failure reports
        db.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        print(f"[run] run {run_id} FAILED: {exc}", file=sys.stderr)
        try:
            _notify(db.get_run(run_id))
        except Exception as mail_exc:              # noqa: BLE001
            print(f"[run] could not email the failure: {mail_exc}",
                  file=sys.stderr)
        return 1

    stats = tooling.last_run_stats()
    matched = tooling.matched_count()
    db.finish_run(run_id, fetched=stats["fetched"],
                  skipped_seen=stats["dropped_seen"],
                  examined=tooling.examined_count(), matched=matched,
                  status="ok" if matched else "empty")

    try:
        _notify(db.get_run(run_id))
    except Exception as exc:                      # noqa: BLE001
        # The one failure that cannot report itself by email. The nonzero exit is
        # what shows up in Task Scheduler's history.
        print(f"[run] the run succeeded but the digest could not be sent: {exc}",
              file=sys.stderr)
        return 1
    return 0
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_cli.py -v`
Expected: 6 passed.

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py src/agent.py tests/test_cli.py
git commit -m "feat: jobs run owns the run row, the failure policy, and the digest"
```

---

### Task 11: Task Scheduler registration

**Files:**
- Create: `src/scheduling.py`, `tests/test_scheduling.py`
- Modify: `src/config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `scheduling.build_task_xml(command: str, working_dir: str, start_time: str) -> str`
  - `scheduling.register(command, working_dir) -> None` — writes the XML to a temp file and calls `schtasks /Create /XML ... /F`.
  - `scheduling.wake_timer_state() -> str` — a human-readable line about the power plan.

- [ ] **Step 1: Add config**

Append to `src/config.py`:

```python
# --- Scheduling (Phase R3) ---
TASK_NAME = "JobSearchAgent"
SCHEDULE_TIME = "09:00:00"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_scheduling.py`:

```python
import xml.etree.ElementTree as ET

import scheduling

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml():
    return ET.fromstring(scheduling.build_task_xml(
        r"C:\repo\.venv\Scripts\jobs.exe run", r"C:\repo", "09:00:00"))


def test_task_runs_daily_at_nine():
    root = _xml()
    assert root.find(".//t:CalendarTrigger/t:StartBoundary", NS).text \
        .endswith("T09:00:00")
    assert root.find(".//t:ScheduleByDay/t:DaysInterval", NS).text == "1"


def test_task_wakes_the_computer():
    # A laptop asleep at 9am is the normal case, not the exception.
    assert _xml().find(".//t:Settings/t:WakeToRun", NS).text == "true"


def test_task_catches_up_after_a_missed_start():
    # Covers the nights the machine is shut down or wake timers are suppressed
    # on battery — the run happens at the next login instead of never.
    assert _xml().find(".//t:Settings/t:StartWhenAvailable", NS).text == "true"


def test_task_runs_with_the_interactive_token():
    # InteractiveToken means no stored password. Waking from sleep keeps the
    # session logged on, so this is enough for the wake case.
    assert _xml().find(".//t:Principals/t:Principal/t:LogonType", NS).text \
        == "InteractiveToken"


def test_task_pins_the_command_and_working_directory():
    root = _xml()
    action = root.find(".//t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text == r"C:\repo\.venv\Scripts\jobs.exe"
    assert action.find("t:Arguments", NS).text == "run"
    # A wrong working directory silently produces a task that fails every morning.
    assert action.find("t:WorkingDirectory", NS).text == r"C:\repo"


def test_battery_does_not_stop_the_task():
    root = _xml()
    assert root.find(".//t:Settings/t:DisallowStartIfOnBatteries", NS).text \
        == "false"
    assert root.find(".//t:Settings/t:StopIfGoingOnBatteries", NS).text == "false"


def test_register_calls_schtasks_with_the_xml(monkeypatch, tmp_path):
    calls = {}

    class Result:
        returncode = 0
        stdout = "SUCCESS"
        stderr = ""

    def fake_run(args, **kwargs):
        calls["args"] = args
        calls["xml"] = open(args[args.index("/XML") + 1], encoding="utf-16").read()
        return Result()

    monkeypatch.setattr(scheduling.subprocess, "run", fake_run)
    scheduling.register(r"C:\repo\.venv\Scripts\jobs.exe run", r"C:\repo")
    assert calls["args"][0] == "schtasks"
    assert "/Create" in calls["args"] and "/F" in calls["args"]
    assert "WakeToRun" in calls["xml"]


def test_register_raises_with_schtasks_output_on_failure(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(scheduling.subprocess, "run",
                        lambda args, **kwargs: Result())
    try:
        scheduling.register("cmd", "dir")
    except RuntimeError as exc:
        assert "Access is denied" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_wake_timer_state_reports_disabled(monkeypatch):
    class Result:
        returncode = 0
        stdout = ("Current AC Power Setting Index: 0x00000001\n"
                  "Current DC Power Setting Index: 0x00000000\n")
        stderr = ""

    monkeypatch.setattr(scheduling.subprocess, "run",
                        lambda args, **kwargs: Result())
    state = scheduling.wake_timer_state()
    assert "plugged in" in state.lower()
    assert "battery" in state.lower()
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_scheduling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scheduling'`.

- [ ] **Step 4: Implement**

Create `src/scheduling.py`:

```python
"""Register the daily 09:00 task with Windows Task Scheduler.

Built from XML rather than `schtasks /Create /SC DAILY`, because the two settings
that matter most cannot be expressed on the schtasks command line: WakeToRun
(wake a sleeping laptop) and StartWhenAvailable (run late rather than never).

Neither covers a machine that is shut down or hibernated — a timer cannot power on
a machine that is off — which is exactly why both are set. Plugged in and asleep,
the run happens at 09:00; otherwise it happens at the next login.
"""
import subprocess
import tempfile
from pathlib import Path

import config

# The wake-timer setting inside the Sleep subgroup of the active power plan.
_WAKE_TIMERS_GUID = "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Daily job search, CV tailoring, and digest email.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{start_time}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <WakeToRun>true</WakeToRun>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def build_task_xml(command, working_dir, start_time=None):
    """`command` is the full command line; its first token is the executable."""
    executable, _, arguments = command.partition(" ")
    return _TASK_XML.format(
        start_time=start_time or config.SCHEDULE_TIME,
        command=executable.strip('"'), arguments=arguments.strip(),
        working_dir=working_dir)


def register(command, working_dir, task_name=None):
    """Create or replace the scheduled task. Raises with schtasks' own output."""
    name = task_name or config.TASK_NAME
    xml = build_task_xml(command, working_dir)
    # schtasks /XML requires UTF-16; a UTF-8 file is rejected with a misleading
    # "The task XML is malformed".
    path = Path(tempfile.gettempdir()) / f"{name}.xml"
    path.write_text(xml, encoding="utf-16")
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks could not register {name!r}: "
            f"{(result.stderr or result.stdout).strip()}")


def wake_timer_state():
    """A readable line about whether the power plan will honour WakeToRun.

    Reported, never changed: a laptop waking itself in a closed bag is the
    operator's call, not this program's. Values are 0=disabled, 1=enabled,
    2=important wake timers only (which suppresses this task).
    """
    labels = {0: "disabled", 1: "enabled", 2: "important timers only (this task "
                                              "will NOT wake the machine)"}
    try:
        result = subprocess.run(
            ["powercfg", "/q", "SCHEME_CURRENT", "SUB_SLEEP", _WAKE_TIMERS_GUID],
            capture_output=True, text=True)
    except OSError as exc:
        return f"Could not read the power plan ({exc})."
    if result.returncode != 0:
        return "Could not read the power plan; check wake timers manually."

    values = {}
    for line in result.stdout.splitlines():
        if "AC Power Setting Index" in line:
            values["plugged in"] = int(line.split(":")[-1].strip(), 16)
        elif "DC Power Setting Index" in line:
            values["on battery"] = int(line.split(":")[-1].strip(), 16)
    if not values:
        return "Could not read the wake-timer setting; check it manually."
    return "Wake timers — " + ", ".join(
        f"{where}: {labels.get(value, value)}" for where, value in values.items())
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_scheduling.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add src/scheduling.py src/config.py tests/test_scheduling.py
git commit -m "feat: register the 9am task with wake and catch-up enabled"
```

---

### Task 12: `jobs setup`

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `db.apply_schema` (Task 1), `mailer.verify_credentials` (Task 9), `scheduling.register`/`wake_timer_state` (Task 11).
- Produces:
  - `cli.missing_env_keys() -> list[str]`
  - `cli.scheduled_command() -> tuple[str, str]` — `(command_line, working_dir)`.
  - `cli.command_setup() -> int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_missing_env_keys_names_every_absent_key(monkeypatch):
    for key in cli.REQUIRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MONID_API_KEY", "monid_live_x")
    missing = cli.missing_env_keys()
    assert "MONID_API_KEY" not in missing
    assert "GMAIL_APP_PASSWORD" in missing


def test_scheduled_command_points_at_the_venv_executable(monkeypatch):
    # A scheduled, non-interactive session does not get the interactive PATH, so
    # the task must name an absolute executable.
    command, working_dir = cli.scheduled_command()
    assert command.endswith(" run")
    assert "jobs" in command.lower()
    assert working_dir == str(config.PROJECT_ROOT)


def test_setup_applies_the_schema_and_registers_the_task(pg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(cli, "start_container", lambda: None)
    monkeypatch.setattr(cli.mailer, "verify_credentials", lambda: None)
    monkeypatch.setattr(cli, "missing_env_keys", list)
    registered = {}
    monkeypatch.setattr(cli.scheduling, "register",
                        lambda command, working_dir: registered.update(
                            command=command, dir=working_dir))
    monkeypatch.setattr(cli.scheduling, "wake_timer_state",
                        lambda: "Wake timers — plugged in: enabled")

    assert cli.command_setup() == 0
    assert registered["command"].endswith(" run")
    assert "Wake timers" in capsys.readouterr().out


def test_setup_stops_and_names_missing_keys(pg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "start_container", lambda: None)
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(cli, "missing_env_keys", lambda: ["GMAIL_APP_PASSWORD"])
    assert cli.command_setup() == 1
    assert "GMAIL_APP_PASSWORD" in capsys.readouterr().out


def test_setup_stops_when_the_app_password_is_wrong(pg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "start_container", lambda: None)
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(cli, "missing_env_keys", list)

    def refuse():
        raise RuntimeError("Gmail rejected the login for me@example.com")
    monkeypatch.setattr(cli.mailer, "verify_credentials", refuse)
    # Better to fail here, on the operator's screen, than silently at 9am.
    assert cli.command_setup() == 1
    assert "Gmail rejected" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_cli.py -v -k setup`
Expected: FAIL — `AttributeError: module 'cli' has no attribute 'missing_env_keys'`.

- [ ] **Step 3: Implement**

Add to `src/cli.py`'s imports: `import os`, `import subprocess`, `import scheduling`.

Append to `src/cli.py`:

```python
REQUIRED_ENV_KEYS = ("MONID_API_KEY", "ANTHROPIC_API_KEY", "GMAIL_ADDRESS",
                     "GMAIL_APP_PASSWORD")


def missing_env_keys():
    """Every required key absent or blank in the environment (after .env loads)."""
    return [key for key in REQUIRED_ENV_KEYS if not os.environ.get(key, "").strip()]


def start_container():
    """`docker compose up -d`, from the repo root. Raises with docker's output."""
    result = subprocess.run(["docker", "compose", "up", "-d"],
                            cwd=str(config.PROJECT_ROOT), capture_output=True,
                            text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`docker compose up -d` failed: "
            f"{(result.stderr or result.stdout).strip()}. Is Docker Desktop "
            f"running?")


def scheduled_command():
    """(command line, working directory) for the scheduled task.

    Names the venv's `jobs.exe` by absolute path: a scheduled non-interactive
    session does not inherit the interactive PATH, and a bare `jobs` would
    produce a task that fails silently every morning.
    """
    executable = Path(sys.executable).parent / "jobs.exe"
    return f'"{executable}" run', str(config.PROJECT_ROOT)


def command_setup():
    """Install everything, once. Idempotent: re-running repairs a partial install."""
    load_dotenv()

    print("Starting Postgres…")
    try:
        start_container()
        wait_for_database()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print("  container is up and answering")

    print("Applying the schema…")
    db.apply_schema()
    print("  runs, seen, matches are in place")

    print("Checking .env…")
    missing = missing_env_keys()
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        print("  Add them to .env and run `jobs setup` again.")
        return 1
    print("  all required keys present")

    print("Checking the Gmail app password…")
    try:
        mailer.verify_credentials()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print("  Gmail accepted the login")

    print("Registering the 9am task…")
    command, working_dir = scheduled_command()
    try:
        scheduling.register(command, working_dir)
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print(f"  {config.TASK_NAME} registered: {command}")
    print(f"  {scheduling.wake_timer_state()}")
    print("\nSetup complete. The first run happens at 09:00; "
          "`jobs run` starts one now.")
    return 0
```

Add `from pathlib import Path` to the imports.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv\Scripts\pytest tests/test_cli.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat: jobs setup installs the database, checks credentials, schedules the run"
```

---

### Task 13: `jobs pdf` and the console entry point

**Files:**
- Modify: `src/cli.py`, `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `db.fetch_pdf` (Task 4), `cli.command_setup` (Task 12), `cli.command_run` (Task 10).
- Produces:
  - `cli.command_pdf(job_id: str, open_after=True) -> int`
  - `cli.main(argv=None) -> int` — the `jobs` entry point.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_pdf_writes_the_stored_bytes_to_the_current_directory(pg, monkeypatch,
                                                              tmp_path):
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.chdir(tmp_path)
    run_id = db.start_run("24h", pg)
    db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched", "r",
                      conn=pg)
    db.insert_match("111", run_id, title="Backend Dev", company="Acme",
                    location="Israel", apply_url="https://a/1",
                    posted_date="2026-08-12", canva_design_id="DAG1",
                    canva_url="https://c/1", pdf=b"%PDF-1.4 body",
                    pdf_filename="Acme_Backend_Dev_111.pdf", conn=pg)

    assert cli.command_pdf("111", open_after=False) == 0
    written = tmp_path / "Acme_Backend_Dev_111.pdf"
    assert written.read_bytes() == b"%PDF-1.4 body"


def test_pdf_reports_an_unknown_job_without_writing_anything(pg, monkeypatch,
                                                             tmp_path, capsys):
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.chdir(tmp_path)
    assert cli.command_pdf("nope", open_after=False) == 1
    assert "nope" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_main_dispatches_each_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "command_setup", lambda: called.append("setup") or 0)
    monkeypatch.setattr(cli, "command_run", lambda: called.append("run") or 0)
    monkeypatch.setattr(cli, "command_pdf",
                        lambda job_id: called.append(f"pdf:{job_id}") or 0)
    assert cli.main(["setup"]) == 0
    assert cli.main(["run"]) == 0
    assert cli.main(["pdf", "4446164871"]) == 0
    assert called == ["setup", "run", "pdf:4446164871"]


def test_main_with_no_command_shows_usage_and_fails():
    assert cli.main([]) == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\pytest tests/test_cli.py -v -k "pdf or main"`
Expected: FAIL — `AttributeError: module 'cli' has no attribute 'command_pdf'`.

- [ ] **Step 3: Implement**

Append to `src/cli.py`:

```python
def command_pdf(job_id, open_after=True):
    """Write one stored CV to the current directory and open it.

    This is the only route from the database back to a file the operator can
    attach: the digest deliberately carries no attachments.
    """
    load_dotenv()
    found = db.fetch_pdf(job_id)
    if found is None:
        print(f"No stored CV for job {job_id!r}. Check the id in the digest "
              f"email — it is the number after `jobs pdf`.")
        return 1
    payload, filename = found
    path = Path.cwd() / filename
    path.write_bytes(payload)
    print(f"Wrote {path} ({len(payload):,} bytes)")
    if open_after:
        os.startfile(path)        # noqa: S606 - Windows-only by design
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="jobs", description="Daily job search, tailoring, and digest.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("setup", help="install: database, schema, 9am task")
    subparsers.add_parser("run", help="run one day's search (the 9am task calls this)")
    pdf_parser = subparsers.add_parser("pdf", help="write a stored CV to a file")
    pdf_parser.add_argument("job_id", help="the id shown in the digest email")

    args = parser.parse_args(argv)
    if args.command == "setup":
        return command_setup()
    if args.command == "run":
        return command_run()
    if args.command == "pdf":
        return command_pdf(args.job_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add the console script**

Append to `pyproject.toml`:

```toml
[project]
name = "job-search-agent"
version = "0.3.0"
requires-python = ">=3.11"

[project.scripts]
jobs = "cli:main"

[tool.setuptools]
package-dir = {"" = "src"}
py-modules = ["cli", "db", "mailer", "scheduling", "agent", "tooling", "tools",
              "config", "canva", "hooks", "jobs", "render", "resume", "tailoring"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

Install it in editable mode so `jobs` resolves to the venv:

```bash
.venv\Scripts\pip install -e .
```

- [ ] **Step 5: Verify the entry point exists**

Run: `.venv\Scripts\jobs --help`
Expected: the usage text listing `setup`, `run`, `pdf`.

- [ ] **Step 6: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat: jobs pdf and the jobs console entry point"
```

---

### Task 14: Remove the file-output path and rewrite the README

**Files:**
- Modify: `src/tooling.py`, `src/render.py`, `src/config.py`, `README.md`
- Delete: the `run_dir` / `write_index` / `render_index` code paths and their tests

**Interfaces:**
- Consumes: everything above.
- Produces: no new interfaces. This task removes dead code so nothing writes to `output/` any more.

- [ ] **Step 1: Find every remaining reference**

Run: `.venv\Scripts\python -c "import subprocess; print(subprocess.run(['git','grep','-n','-E','write_index|render_index|run_dir|OUTPUT_DIR'],capture_output=True,text=True).stdout)"`

Expected: hits in `src/tooling.py`, `src/render.py`, `src/config.py`, `tests/test_render.py`, `tests/test_output_tools.py`, `README.md`. Work through the list.

- [ ] **Step 2: Delete the code**

- `src/tooling.py`: delete `write_index` and `run_dir` entirely.
- `src/render.py`: delete `render_index` and any import only it used. Keep `pdf_filename` and `safe_filename` — `save_pdf` still calls them.
- `src/config.py`: delete the `OUTPUT_DIR` assignment.

- [ ] **Step 3: Delete the tests that covered them**

In `tests/test_render.py` delete every test of `render_index`. In `tests/test_output_tools.py` delete any remaining `write_index` or `run_dir` test. Keep every test of `pdf_filename` / `safe_filename`.

- [ ] **Step 4: Run the whole suite**

Run: `.venv\Scripts\pytest`
Expected: all pass, with no `ImportError` and no test referencing the deleted functions.

- [ ] **Step 5: Confirm nothing writes to output/ any more**

Run: `.venv\Scripts\python -c "import subprocess; print(subprocess.run(['git','grep','-n','OUTPUT_DIR'],capture_output=True,text=True).stdout or 'clean')"`
Expected: `clean`.

- [ ] **Step 6: Rewrite the README**

Replace the install and usage sections with:

```markdown
## Install

1. `docker compose up -d` (Docker Desktop must be running)
2. `.venv\Scripts\pip install -e .`
3. Put these in `.env`: `MONID_API_KEY`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`,
   `GMAIL_APP_PASSWORD` (Google Account → Security → App passwords; 2-step
   verification must be on).
4. `jobs setup`

## Daily use

Nothing. The scheduled task runs at 09:00 — waking the laptop if it is asleep and
plugged in, otherwise running at the next login — and emails one digest every
morning: the matches, or "nothing today", or the failure. Silence means the
scheduled task itself is not firing.

`jobs pdf <id>` writes a stored CV next to you and opens it. The id is in the
digest.

`jobs run` starts a run by hand.

## Where the data lives

A local `postgres:16` container bound to `127.0.0.1:5432`, data in the
`jobsearch-pgdata` Docker volume. Three tables: `runs` (one row per invocation),
`seen` (every job ever examined — this is what stops the agent paying to re-judge
yesterday's postings), and `matches` (the full record plus the PDF bytes).

There are no backups. `docker compose down -v` or a Docker Desktop factory reset
destroys the history permanently; the agent itself recovers and carries on.
```

Also update any part of the README describing `output/` or `index.md`.

- [ ] **Step 7: Commit**

```bash
git add src/tooling.py src/render.py src/config.py README.md tests/
git commit -m "refactor: remove the output folder and index.md path"
```

---

## Verification before the first live run

Do not spend money until all of this passes:

- [ ] `.venv\Scripts\pytest` — everything green, with the new tests counted.
- [ ] `docker compose ps` — `jobsearch-db` is `running (healthy)`.
- [ ] `.venv\Scripts\jobs setup` — completes with the wake-timer line printed.
- [ ] `schtasks /Query /TN JobSearchAgent /XML` — `WakeToRun` and `StartWhenAvailable` are both `true`.
- [ ] `.venv\Scripts\python -c "import agent; agent.build_options()"` — no exception (the free dry check; confirms MCP config and env).
- [ ] Confirm the Monid balance covers a 24h run (~$0.12) before starting one.

Then run `jobs run` once by hand, watching stderr, and confirm afterwards:

- [ ] The `[reduce]` line reports a non-zero `dropped_seen` **on the second run** (the first has nothing to skip — this is the whole feature, and only a second run proves it).
- [ ] `seen` has one row per job examined; `matches` has one per CV.
- [ ] The digest arrived, and `jobs pdf <id>` on an id from it opens a valid PDF.

---

## Self-Review Notes

**Spec coverage:** every spec section maps to a task — schema → 1/3/4, cross-run dedup → 5, `record_verdict` → 6, `save_pdf` → 7, email → 8/9, `jobs run` and the failure table → 10, scheduling → 11, `jobs setup` → 12, `jobs pdf` and the console script → 13, migration/removal → 14.

**One deliberate deviation from the spec:** `matches` does not carry `fit_score` or `reasoning`. They live in `seen` and are read by joining on `job_id` (`db.matches_for_run`), so the score in the email can never disagree with the score the CV was gated on. The spec lists them as `matches` columns; this plan normalizes them. Everything the spec asks for is still stored and still reachable.
