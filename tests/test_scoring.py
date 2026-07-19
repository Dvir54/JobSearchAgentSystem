from jobs import JobPosting
from scoring import JobScore, build_scoring_prompt, score_job


class FakeMessages:
    """Stands in for client.messages — records the call, returns a canned parse."""

    def __init__(self, parsed_output):
        self._parsed_output = parsed_output
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return type("Response", (), {"parsed_output": self._parsed_output})()


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _posting(description: str = "...") -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description=description, url="https://example.com", posted_date=None,
    )


def test_build_scoring_prompt_includes_description_and_cv():
    prompt = build_scoring_prompt(_posting("Django and Postgres"), "I know Python.")
    assert "Django and Postgres" in prompt
    assert "I know Python." in prompt


def test_score_job_returns_the_parsed_score():
    expected = JobScore(is_junior_friendly=True, fit_score=80, match_kind="direct", reason="Entry level.")
    client = FakeClient(expected)
    assert score_job(client, _posting(), "I know Python.") == expected


def test_score_carries_match_kind():
    expected = JobScore(is_junior_friendly=True, fit_score=75, match_kind="stretch", reason="Learnable.")
    client = FakeClient(expected)
    assert score_job(client, _posting(), "cv").match_kind == "stretch"


def test_score_job_requests_structured_output_from_the_right_model():
    client = FakeClient(
        JobScore(is_junior_friendly=False, fit_score=0, match_kind="direct", reason="Senior.")
    )
    score_job(client, _posting(), "cv")

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is JobScore
    # These parameters are rejected with a 400 on claude-opus-4-8.
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
