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


def test_write_index_writes_the_rendered_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    entries = [{"company": "Alignerr", "title": "SW Engineer", "fit_score": 88,
                "match_kind": "direct", "reason": "Good overlap.",
                "apply_url": "https://example.com/j", "pdf_filename": "a.pdf",
                "canva_edit_url": "https://canva.com/d/x", "corrections": []}]
    out = tooling.write_index(entries, window="24h", skipped_count=16,
                              today="2026-07-30")
    written = pathlib.Path(out["written"])
    assert written.name == "index.md"
    body = written.read_text(encoding="utf-8")
    assert "Alignerr" in body and "https://example.com/j" in body and "16" in body
