from jobs import JobPosting
from tailoring import TailoredCV, find_invented_skills, tailor_cv

BASE_CV = """Dvir — Software Engineer
Skills: Python, Django, PostgreSQL, Docker, Git
Experience: Built a REST API in Django serving 10k requests/day.
"""


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
        description="Looking for Python and Docker experience.",
        url="https://example.com", posted_date=None,
    )


def test_find_invented_skills_accepts_skills_present_in_base_cv():
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["Python", "Docker"])
    assert find_invented_skills(tailored, BASE_CV) == []


def test_find_invented_skills_is_case_insensitive():
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["python", "DOCKER"])
    assert find_invented_skills(tailored, BASE_CV) == []


def test_find_invented_skills_flags_technology_absent_from_base_cv():
    # This is the constraint the whole system rests on: no invented experience.
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["Python", "Kubernetes"])
    assert find_invented_skills(tailored, BASE_CV) == ["Kubernetes"]


def test_tailor_cv_returns_parsed_output():
    expected = TailoredCV(summary="Backend dev", bullets=["Built an API"], skills=["Python"])
    client = FakeClient(expected)
    assert tailor_cv(client, _posting(), BASE_CV) == expected


def test_tailor_cv_requests_structured_output_from_the_right_model():
    client = FakeClient(TailoredCV(summary="s", bullets=["b"], skills=["Python"]))
    tailor_cv(client, _posting(), BASE_CV)

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is TailoredCV
    assert "temperature" not in kwargs
