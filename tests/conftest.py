"""Postgres fixture for the database tests.

Runs against a REAL throwaway database, not a fake. The whole point of the design
is that Postgres enforces dedup through a primary key; a mock would test the mock.
Tests skip with a clear message when the container is not up, so a developer
without Docker running gets a skip rather than a wall of errors.
"""
from pathlib import Path

import psycopg
import pytest

from jobsearch import config
from jobsearch import db

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.json"


@pytest.fixture(autouse=True)
def _fixture_profile(monkeypatch, tmp_path):
    """Every test runs against a known profile, never the developer's own.

    A COPY, in a temp directory: anything that calls profile.save() would
    otherwise rewrite the committed fixture, which is exactly what happened once
    when a test reached the real `jobs init`.
    """
    from jobsearch.resume import profile
    # In a subdirectory: tests that patch PROFILE_PATH to tmp_path themselves
    # would otherwise find this copy where they expect no profile at all.
    working = tmp_path / "_fixture" / "profile.json"
    working.parent.mkdir(exist_ok=True)
    working.write_text(FIXTURE_PROFILE.read_text(encoding="utf-8"),
                       encoding="utf-8")
    monkeypatch.setattr(config, "PROFILE_PATH", working)
    profile.reset_cache()
    yield
    profile.reset_cache()


def _ensure_test_database():
    """Create jobs_test if it does not exist. Connects to the maintenance
    database because CREATE DATABASE cannot run inside a transaction."""
    admin_dsn = config.TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = 'jobs_test'")
            if cur.fetchone() is None:
                cur.execute("CREATE DATABASE jobs_test")


@pytest.fixture(autouse=True)
def _offline_dedup(monkeypatch):
    """Cross-run dedup defaults to "every job is new" for unit tests.

    reduce_run_payload consults the database on every call. Without this, every
    reducer test would need a live Postgres AND would depend on whatever the real
    `seen` table happens to hold that day. Tests that actually exercise dedup
    override this with their own monkeypatch.
    """
    from jobsearch.agent import tooling
    if hasattr(tooling, "_query_unseen_ids"):
        monkeypatch.setattr(tooling, "_query_unseen_ids",
                            lambda job_ids: {str(job_id) for job_id in job_ids})


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
