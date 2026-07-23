import json
from pathlib import Path

import pytest

from jobs import JobPosting, build_harvestapi_input, fetch_jobs, normalize_posting

FIXTURE = Path(__file__).parent / "fixtures" / "harvestapi_response.json"


def _items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_build_harvestapi_input_includes_filters_and_all_queries():
    body = build_harvestapi_input(["backend developer", "ai engineer"])["body"]
    assert body["jobTitles"] == ["backend developer", "ai engineer"]
    assert body["locations"] == ["Israel"]
    assert body["experienceLevel"] == ["internship", "entry", "associate"]
    assert body["maxItems"] == 25
    assert body["postedLimit"] == "week"
    assert body["sortBy"] == "date"


def test_normalize_posting_produces_stable_shape():
    posting = normalize_posting(_items()[0])
    assert isinstance(posting, JobPosting)
    assert posting.id
    assert posting.title
    assert posting.company
    assert posting.url.startswith("http")


def test_normalize_posting_reads_nested_company_name():
    raw = _items()[0]
    assert normalize_posting(raw).company == raw["company"]["name"]


def test_normalize_posting_tolerates_missing_posted_date():
    raw = dict(_items()[0])
    raw.pop("postedDate", None)
    assert normalize_posting(raw).posted_date is None


def test_fetch_jobs_normalizes_via_run_callable():
    captured = {}

    def fake_run(provider, endpoint, run_input):
        captured["args"] = (provider, endpoint, run_input)
        return _items()

    postings = fetch_jobs(fake_run, ["backend developer"])
    assert len(postings) == len(_items())
    assert all(isinstance(p, JobPosting) for p in postings)
    assert captured["args"][2]["body"]["jobTitles"] == ["backend developer"]


def test_fetch_jobs_raises_on_zero_results():
    with pytest.raises(RuntimeError):
        fetch_jobs(lambda provider, endpoint, run_input: [], ["backend developer"])
