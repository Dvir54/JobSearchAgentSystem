from pathlib import Path

from resume import Entry, ParsedResume, Section, parse_resume

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_cv.md").read_text(encoding="utf-8")


def _parsed() -> ParsedResume:
    return parse_resume(SAMPLE)


def test_preamble_captured_and_excludes_sections():
    parsed = _parsed()
    assert "Test Candidate" in parsed.preamble
    assert "Software Engineer" in parsed.preamble
    assert "About Me" not in parsed.preamble


def test_sections_are_in_file_order():
    names = [s.name for s in _parsed().sections]
    assert names == [
        "About Me", "Work Experience", "Projects",
        "Education", "Skills", "Languages",
    ]


def test_tailored_flag_matches_config():
    parsed = _parsed()
    assert parsed.get("About Me").is_tailored is True
    assert parsed.get("Skills").is_tailored is True
    assert parsed.get("Work Experience").is_tailored is True
    assert parsed.get("Education").is_tailored is False
    assert parsed.get("Languages").is_tailored is False


def test_work_experience_entries_preserve_two_line_anchor_and_bullets():
    entries = _parsed().get("Work Experience").entries
    assert len(entries) == 2
    first = entries[0]
    assert first.anchor == "### Backend Developer | Acme Corp\n*Jan 2024 - present*"
    assert first.bullets == ["Built services in Python.", "Maintained a Postgres database."]


def test_project_entries_keep_name_and_tech_and_have_no_bullets():
    entries = _parsed().get("Projects").entries
    assert len(entries) == 2
    assert entries[0].anchor == "### Todo App\nPython, Flask"
    assert entries[0].bullets == []


def test_static_section_body_is_kept_verbatim():
    body = _parsed().get("Education").body
    assert "### B.Sc. Computer Science" in body
    assert "Some University | 2020 - 2024" in body


def test_static_section_has_no_parsed_entries():
    assert _parsed().get("Education").entries == []
