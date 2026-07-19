from pathlib import Path

from render import render_output
from resume import parse_resume
from tailoring import TailoredCV, TailoredEntry

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_cv.md").read_text(encoding="utf-8")
PARSED = parse_resume(SAMPLE)


class FakePosting:
    url = "https://example.com/job"


class FakeScore:
    fit_score = 82
    reason = "Strong match."


def _tailored() -> TailoredCV:
    return TailoredCV(
        summary="Tailored summary here.",
        skills=["SQL", "Python", "Git"],
        experience=[TailoredEntry(entry_index=1, bullets=["Reworded intern bullet."]),
                    TailoredEntry(entry_index=0, bullets=["Reworded acme bullet."])],
        projects=[TailoredEntry(entry_index=1, bullets=[]),
                  TailoredEntry(entry_index=0, bullets=[])],
    )


def _out() -> str:
    return render_output(FakePosting(), FakeScore(), PARSED, _tailored())


def test_metadata_block_is_above_the_resume():
    out = _out()
    assert out.index("**Fit:** 82/100") < out.index("---") < out.index("# Test Candidate")
    assert "https://example.com/job" in out


def test_all_sections_present_in_original_order():
    out = _out()
    positions = [out.index(f"## {name}") for name in
                 ["About Me", "Work Experience", "Projects", "Education", "Skills", "Languages"]]
    assert positions == sorted(positions)


def test_static_sections_are_verbatim():
    out = _out()
    assert "### B.Sc. Computer Science" in out
    assert "English - Fluent" in out


def test_tailored_summary_and_skills_are_used():
    out = _out()
    assert "Tailored summary here." in out
    assert "SQL, Python, Git" in out


def test_experience_anchor_is_verbatim_with_reworded_bullets_in_new_order():
    out = _out()
    intern = out.index("### Intern | Beta Ltd")
    acme = out.index("### Backend Developer | Acme Corp")
    assert intern < acme  # reordered: entry_index 1 first
    assert "*Jan 2024 - present*" in out
    assert "Reworded acme bullet." in out


def test_projects_keep_tech_line_and_have_no_bullets():
    out = _out()
    assert "### Chat Bot\nPython, WebSockets" in out
    assert "### Todo App\nPython, Flask" in out
    # No section separator should leak into a project entry.
    assert "WebSockets\n---" not in out
