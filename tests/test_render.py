from render import pdf_filename, render_index


def _entry(**overrides):
    entry = {"company": "Alignerr", "title": "Software Engineer (AI Training)",
             "fit_score": 88, "match_kind": "direct",
             "reason": "Python and LLM tooling line up with the internship.",
             "apply_url": "https://www.linkedin.com/jobs/view/4446167840",
             "pdf_filename": "Alignerr_Software_Engineer_(AI_Training)_4446167840.pdf",
             "canva_design_id": "DAHR-XiHc_U",
             "canva_edit_url": "https://www.canva.com/d/abc123",
             "corrections": []}
    entry.update(overrides)
    return entry


def test_pdf_filename_uses_job_id_and_pdf_extension():
    name = pdf_filename("Alignerr", "Software Engineer", "4446167840")
    assert name.endswith(".pdf")
    assert "4446167840" in name


def test_pdf_filename_disambiguates_same_company_and_title():
    a = pdf_filename("Alignerr", "Software Engineer", "111")
    b = pdf_filename("Alignerr", "Software Engineer", "222")
    assert a != b


def test_pdf_filename_strips_path_characters():
    assert "/" not in pdf_filename("A/B", "C:D", "../../evil")
    assert "\\" not in pdf_filename("A\\B", "C", "1")


def test_index_lists_every_entry_with_its_apply_url_and_pdf():
    out = render_index([_entry(), _entry(company="Fives", title="Junior QA", fit_score=90,
                                        pdf_filename="Fives_Junior_QA_1.pdf",
                                        apply_url="https://example.com/j/1")],
                       window="24h", skipped_count=16)
    assert "Alignerr" in out and "Fives" in out
    assert "https://www.linkedin.com/jobs/view/4446167840" in out
    assert "Fives_Junior_QA_1.pdf" in out
    assert "88" in out and "90" in out


def test_index_reports_the_window_and_the_skipped_count():
    out = render_index([_entry()], window="24h", skipped_count=16)
    assert "24h" in out
    assert "16" in out


def test_index_surfaces_guard_corrections():
    out = render_index([_entry(corrections=["removed unverified skills: Kubernetes"])],
                       window="24h", skipped_count=0)
    assert "Kubernetes" in out


def test_index_handles_an_empty_run():
    out = render_index([], window="24h", skipped_count=9)
    assert "9" in out
    assert "No résumés" in out or "no résumés" in out


def test_index_links_canva_by_permanent_design_id_not_the_rotating_token():
    """copy-design returns a /d/<token> URL and those rotate; the design id does
    not. Every copy also inherits the template's title, so a dead link would leave
    no way to tell the designs apart."""
    out = render_index([_entry()], window="24h", skipped_count=0)
    assert "https://www.canva.com/design/DAHR-XiHc_U" in out
    assert "abc123" not in out


def test_index_falls_back_to_the_recorded_url_when_no_design_id():
    entry = _entry()
    del entry["canva_design_id"]
    out = render_index([entry], window="24h", skipped_count=0)
    assert "https://www.canva.com/d/abc123" in out
