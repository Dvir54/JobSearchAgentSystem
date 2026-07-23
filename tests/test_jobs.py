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
    assert postings  # non-empty
    assert all(isinstance(p, JobPosting) for p in postings)
    ids = [p.id for p in postings]
    assert len(ids) == len(set(ids))  # deduped
    assert all(p.location and "israel" in p.location.lower() for p in postings)
    assert captured["args"][2]["body"]["jobTitles"] == ["backend developer"]


def test_fetch_jobs_raises_on_zero_results():
    with pytest.raises(RuntimeError):
        fetch_jobs(lambda provider, endpoint, run_input: [], ["backend developer"])


def _raw(job_id, location, company="Acme"):
    return {
        "id": job_id, "title": "Developer",
        "company": {"name": company}, "descriptionText": "desc",
        "linkedinUrl": "https://example.com/job", "postedDate": None,
        "location": {"linkedinText": location},
    }


def test_normalize_posting_reads_location_text():
    assert normalize_posting(_raw("1", "Tel Aviv-Yafo, Tel Aviv District, Israel")).location \
        == "Tel Aviv-Yafo, Tel Aviv District, Israel"


def test_normalize_posting_tolerates_missing_location():
    raw = _raw("1", "Israel")
    raw.pop("location", None)
    assert normalize_posting(raw).location is None


def test_fetch_jobs_dedups_repeated_ids():
    postings = fetch_jobs(lambda p, e, i: [_raw("1", "Israel"), _raw("1", "Israel")], ["x"])
    assert [p.id for p in postings] == ["1"]


def test_fetch_jobs_keeps_israel_drops_emea_and_mena():
    raws = [_raw("1", "Tel Aviv, Israel"), _raw("2", "EMEA"), _raw("3", "MENA")]
    assert [p.id for p in fetch_jobs(lambda p, e, i: raws, ["x"])] == ["1"]


def test_fetch_jobs_drops_missing_location():
    raw = _raw("9", "Israel")
    raw.pop("location", None)
    assert fetch_jobs(lambda p, e, i: [raw], ["x"]) == []


def test_fetch_jobs_raises_only_on_empty_raw_response():
    import pytest
    with pytest.raises(RuntimeError):
        fetch_jobs(lambda p, e, i: [], ["x"])
