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
