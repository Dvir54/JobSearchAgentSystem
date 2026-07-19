from jobs import JobPosting
from resume import parse_resume
from tailoring import (
    TailoredCV,
    TailoredEntry,
    build_tailoring_prompt,
    find_entry_coverage_errors,
    find_invented_skills,
    tailor_cv,
)

BASE_MD = """# Cand

test@example.com

## About Me

I build things.

## Work Experience

### Backend Developer | Acme
*2024 - now*

- Built APIs in Python.

### Intern | Beta
*2023*

- Wrote scripts.

## Projects

### Todo App
Python, Flask

## Skills

Python, SQL, Docker
"""

PARSED = parse_resume(BASE_MD)


class FakeMessages:
    def __init__(self, parsed_output):
        self._parsed_output = parsed_output
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return type("Response", (), {"parsed_output": self._parsed_output})()


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _posting() -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description="Python and Docker.", url="https://example.com", posted_date=None,
    )


def _valid_tailored() -> TailoredCV:
    return TailoredCV(
        summary="s",
        skills=["Python", "Docker"],
        experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                    TailoredEntry(entry_index=0, bullets=["b"])],
        projects=[TailoredEntry(entry_index=0, bullets=[])],
    )


def test_prompt_lists_indexed_entries_and_job():
    prompt = build_tailoring_prompt(PARSED, _posting())
    assert "[0]" in prompt and "[1]" in prompt
    assert "Backend Developer | Acme" in prompt
    assert "Todo App" in prompt
    assert "Python and Docker." in prompt


def test_tailor_cv_returns_parsed_output():
    expected = _valid_tailored()
    client = FakeClient(expected)
    assert tailor_cv(client, _posting(), PARSED) == expected


def test_tailor_cv_uses_right_model_and_no_bad_params():
    client = FakeClient(_valid_tailored())
    tailor_cv(client, _posting(), PARSED)
    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is TailoredCV
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_find_invented_skills_flags_absent_technology():
    tailored = TailoredCV(summary="s", skills=["Python", "Kubernetes"],
                          experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                                      TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    assert find_invented_skills(tailored, BASE_MD) == ["Kubernetes"]


def test_find_invented_skills_accepts_present_skills_case_insensitively():
    tailored = TailoredCV(summary="s", skills=["python", "DOCKER"],
                          experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                                      TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    assert find_invented_skills(tailored, BASE_MD) == []


def test_entry_coverage_accepts_full_reordered_coverage():
    assert find_entry_coverage_errors(_valid_tailored(), PARSED) == []


def test_entry_coverage_flags_missing_experience_entry():
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    errors = find_entry_coverage_errors(tailored, PARSED)
    assert any("experience" in e for e in errors)


def test_entry_coverage_flags_duplicate_and_out_of_range():
    dup = TailoredCV(summary="s", skills=["Python"],
                     experience=[TailoredEntry(entry_index=0, bullets=["b"]),
                                 TailoredEntry(entry_index=0, bullets=["b"])],
                     projects=[TailoredEntry(entry_index=5, bullets=[])])
    errors = find_entry_coverage_errors(dup, PARSED)
    assert any("experience" in e for e in errors)
    assert any("projects" in e for e in errors)
