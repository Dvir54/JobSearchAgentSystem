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
