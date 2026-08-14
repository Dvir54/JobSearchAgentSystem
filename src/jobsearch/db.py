"""Every database access in the system. No SQL lives outside this module.

Postgres is the system of record: every job the agent has ever examined, and the
PDF of every CV it wrote. It runs as a local Docker container bound to 127.0.0.1
(see docker-compose.yml) — nothing here is reachable off this machine.

Connections are autocommit. Each write is independently meaningful: a verdict
recorded for job 7 must survive a crash while judging job 8, so there is no
run-spanning transaction to roll back.
"""
import psycopg

from jobsearch import config

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
    """(bytes, filename, run_date) for one stored CV, or None.

    Backs `jobs pdf <id>`. The date is the day the CV was made, not today, so
    exporting the same job twice always lands in the same dated directory
    instead of scattering one job across several.
    """
    with _conn(conn).cursor() as cur:
        cur.execute("""SELECT pdf, pdf_filename, created_at::date
                       FROM matches WHERE job_id = %s""", (str(job_id),))
        row = cur.fetchone()
        return (bytes(row[0]), row[1], row[2]) if row else None


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
