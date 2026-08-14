import json
from pathlib import Path

from jobsearch.agent.jobs import JobPosting, normalize_posting

FIXTURE = Path(__file__).parent / "fixtures" / "harvestapi_response.json"


def _items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


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
