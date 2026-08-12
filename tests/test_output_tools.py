import pathlib

import tooling


def test_run_dir_is_dated_and_created(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    d = tooling.run_dir(today="2026-07-30")
    assert d == tmp_path / "2026-07-30"
    assert d.is_dir()


def test_save_pdf_writes_the_downloaded_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"%PDF-1.7 fake")

    out = tooling.save_pdf("https://example.com/x.pdf", "Alignerr",
                           "Software Engineer", "4446167840", today="2026-07-30")

    saved = pathlib.Path(out["saved"])
    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-1.7 fake"
    assert saved.suffix == ".pdf"
    assert "4446167840" in saved.name
    assert out["error"] == ""


def test_save_pdf_disambiguates_same_company_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"x")
    a = tooling.save_pdf("u", "Alignerr", "SW Engineer", "111", today="2026-07-30")
    b = tooling.save_pdf("u", "Alignerr", "SW Engineer", "222", today="2026-07-30")
    assert a["saved"] != b["saved"]
    assert len(list((tmp_path / "2026-07-30").iterdir())) == 2


def test_save_pdf_reports_a_download_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)

    def boom(url):
        raise OSError("connection reset")

    monkeypatch.setattr(tooling, "_fetch_bytes", boom)
    out = tooling.save_pdf("u", "Acme", "Dev", "1", today="2026-07-30")
    assert out["saved"] is None
    assert "connection reset" in out["error"]


def test_save_pdf_rejects_an_empty_download(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"")
    out = tooling.save_pdf("u", "Acme", "Dev", "1", today="2026-07-30")
    assert out["saved"] is None
    assert "empty" in out["error"].lower()
    # save_pdf's empty-payload branch returns before run_dir() is called, so the
    # run directory itself may not exist yet — either way, nothing was written.
    run_directory = tmp_path / "2026-07-30"
    assert not run_directory.exists() or not any(run_directory.iterdir())


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
