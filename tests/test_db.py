from jobsearch import db


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
    from jobsearch import config
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
    pdf, filename, _ = db.fetch_pdf("111", pg)
    assert pdf == PDF_BYTES          # byte-identical, not merely similar
    assert filename == "Acme_111.pdf"


def test_fetch_pdf_returns_none_for_an_unknown_job(pg):
    assert db.fetch_pdf("does-not-exist", pg) is None


def test_fetch_pdf_returns_the_date_the_cv_was_made(pg):
    """`jobs pdf` files exports under the run's date, so the date must come back
    with the bytes.

    Compared against the DATABASE's idea of today, not Python's: created_at
    defaults to now() inside a UTC container while this machine is UTC+3, so the
    two genuinely disagree for the first hours of every local day. Asserting
    against date.today() would pass all afternoon and fail after midnight.
    """
    _match(pg, "111", 82)
    pdf, filename, run_date = db.fetch_pdf("111", pg)
    assert pdf == PDF_BYTES
    assert filename == "Acme_111.pdf"
    with pg.cursor() as cur:
        cur.execute("SELECT now()::date")
        assert run_date == cur.fetchone()[0]


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
