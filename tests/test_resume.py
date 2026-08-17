from pathlib import Path

from jobsearch.resume.base_cv import (
    Entry,
    ParsedResume,
    Section,
    parse_resume,
    render_base_cv,
)

_SAMPLE = """# Dana Levi

dana@example.com

## About Me

Final-year CS student with backend experience.

## Work Experience

### Backend Developer | Acme
*2024 - now*

- Built REST APIs in Python
- Cut p95 latency by 40%

### Intern | Beta
*2023*

- Wrote automation scripts

## Skills

Python, SQL, Docker
"""


def test_rendering_a_parsed_cv_reproduces_it():
    """The round-trip invariant. `jobs init` generates this file and the guards
    read it back, so writer and parser must agree exactly — otherwise a user's
    honesty checks run against text they never wrote."""
    parsed = parse_resume(_SAMPLE)
    assert parse_resume(render_base_cv(parsed)) == parsed


def test_rendered_output_uses_canonical_headings():
    rendered = render_base_cv(parse_resume(_SAMPLE))
    assert "## About Me" in rendered
    assert "## Work Experience" in rendered
    assert "## Skills" in rendered


def test_entries_keep_their_anchor_and_bullets():
    rendered = render_base_cv(parse_resume(_SAMPLE))
    assert "### Backend Developer | Acme" in rendered
    assert "*2024 - now*" in rendered
    assert "- Cut p95 latency by 40%" in rendered

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


def test_last_entry_anchor_excludes_the_section_separator():
    # The --- separator before the next section must not leak into the last
    # bullet-less entry's anchor.
    entries = _parsed().get("Projects").entries
    assert entries[1].anchor == "### Chat Bot\nPython, WebSockets"


def test_static_section_body_is_kept_verbatim():
    body = _parsed().get("Education").body
    assert "### B.Sc. Computer Science" in body
    assert "Some University | 2020 - 2024" in body


def test_static_section_has_no_parsed_entries():
    assert _parsed().get("Education").entries == []
