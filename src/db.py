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
