"""Filename rules for exported CVs.

These moved here from test_tooling.py in R6, along with the functions themselves:
they are filename behaviour, and keeping them in the agent's tooling was what
made `resume` and `agent` import each other.
"""
from jobsearch.resume.render import pdf_filename


def test_pdf_filename_uses_job_id_and_pdf_extension():
    name = pdf_filename("Alignerr", "Software Engineer", "4446167840")
    assert name == "Alignerr_Software_Engineer_4446167840.pdf"


def test_pdf_filename_disambiguates_same_company_and_title():
    # Two roles both titled "Software Engineer" at one employer are common; without
    # the id the second export would silently overwrite the first.
    a = pdf_filename("Alignerr", "Software Engineer", "111")
    b = pdf_filename("Alignerr", "Software Engineer", "222")
    assert a != b


def test_pdf_filename_falls_back_without_a_job_id():
    assert pdf_filename("Acme", "Backend Developer") == "Acme_Backend_Developer.pdf"
    assert pdf_filename("Acme", "Backend Developer", None) == "Acme_Backend_Developer.pdf"
    assert pdf_filename("Acme", "Backend Developer", "") == "Acme_Backend_Developer.pdf"


def test_pdf_filename_strips_path_characters():
    assert "/" not in pdf_filename("A/B", "C:D", "../../evil")
    assert "\\" not in pdf_filename("A\\B", "C", "1")


def test_the_job_id_is_sanitised_like_company_and_title():
    # Same stripping as the other two components, so a hostile id cannot escape
    # the directory it is joined onto.
    name = pdf_filename("Acme", "Backend Developer", "../../evil")
    assert "/" not in name and "\\" not in name
    assert name == "Acme_Backend_Developer_....evil.pdf"


def test_no_markdown_extension_survives():
    """Until R6 this built an ".md" name that pdf_filename then stripped — a
    leftover from the deleted markdown-résumé output. Nothing produces .md now."""
    assert ".md" not in pdf_filename("Acme", "Dev", "1")
