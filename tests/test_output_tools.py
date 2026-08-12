import tooling


def db_start(pg):
    import db
    return db.start_run("24h", pg)


def _hold_job(job_id="111", company="Acme", title="Backend Dev"):
    """Put one posting in the in-process store, as reduce_run_payload would."""
    tooling._JOBS_BY_ID.clear()
    tooling._JOBS_BY_ID[job_id] = {
        "id": job_id, "title": title, "company": company, "description": "d",
        "url": f"https://apply/{job_id}", "posted_date": "2026-08-12",
        "location": "Tel Aviv, Israel"}


def test_save_pdf_stores_the_bytes_and_the_posting_fields(pg, monkeypatch):
    import db
    run_id = db.start_run("24h", pg)
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(run_id)
    _hold_job("4446167840", "Alignerr", "Software Engineer")
    db.record_verdict("4446167840", run_id, "Software Engineer", "Alignerr", 82,
                      "matched", "r", conn=pg)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"%PDF-1.7 fake")

    out = tooling.save_pdf("https://example.com/x.pdf", "4446167840", "DAG1",
                           "https://canva/1")

    assert out["error"] == ""
    assert tooling.matched_count() == 1
    pdf, filename = db.fetch_pdf("4446167840", pg)
    assert pdf == b"%PDF-1.7 fake"
    assert "4446167840" in filename and filename.endswith(".pdf")
    with pg.cursor() as cur:
        cur.execute("SELECT apply_url, location, company FROM matches "
                    "WHERE job_id = '4446167840'")
        # Taken from the held posting, not retyped by the model.
        assert cur.fetchone() == ("https://apply/4446167840", "Tel Aviv, Israel",
                                  "Alignerr")


def test_save_pdf_refuses_a_job_that_was_never_listed(pg, monkeypatch):
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(db_start(pg))
    tooling._JOBS_BY_ID.clear()
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"%PDF")
    out = tooling.save_pdf("u", "999", "DAG1", "https://canva/1")
    assert out["saved"] is None
    assert "999" in out["error"]
    assert tooling.matched_count() == 0


def test_save_pdf_reports_a_download_failure_without_raising(pg, monkeypatch):
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(db_start(pg))
    _hold_job()

    def boom(url):
        raise OSError("connection reset")

    monkeypatch.setattr(tooling, "_fetch_bytes", boom)
    out = tooling.save_pdf("u", "111", "DAG1", "https://canva/1")
    assert out["saved"] is None
    assert "connection reset" in out["error"]
    assert tooling.matched_count() == 0


def test_save_pdf_rejects_an_empty_download(pg, monkeypatch):
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(db_start(pg))
    _hold_job()
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"")
    out = tooling.save_pdf("u", "111", "DAG1", "https://canva/1")
    assert out["saved"] is None
    assert "empty" in out["error"].lower()
    assert tooling.matched_count() == 0
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM matches")
        assert cur.fetchone()[0] == 0


def test_record_verdict_writes_the_row_and_counts_it(pg, monkeypatch):
    import db
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
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    tooling.set_run_id(db.start_run("24h", pg))
    result = tooling.record_verdict("111", "T", "C", 82, "maybe", "r")
    assert "error" in result
    assert tooling.examined_count() == 0


def test_record_verdict_returns_an_error_rather_than_raising(monkeypatch):
    def boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tooling, "_db_conn", boom)
    tooling.set_run_id(1)
    # One unrecorded verdict must cost one job, never the run.
    assert "error" in tooling.record_verdict("111", "T", "C", 82, "matched", "r")
    assert tooling.examined_count() == 0
