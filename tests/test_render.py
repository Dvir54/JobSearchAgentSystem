from jobsearch.resume.render import pdf_filename



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







