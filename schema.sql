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
