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
