import json
from pathlib import Path

from jobs import JobPosting, build_search_url, normalize_posting

FIXTURE = Path(__file__).parent / "fixtures" / "actor_response.json"


def test_build_search_url_targets_israel_entry_level():
    url = build_search_url("backend developer")
    assert "location=Israel" in url
    assert "f_E=2" in url
    assert "keywords=backend%20developer" in url


def test_build_search_url_omits_junior_keyword():
    # Seniority is handled by the f_E=2 filter, not by keyword-stuffing.
    assert "junior" not in build_search_url("software engineer").lower()


def test_normalize_posting_produces_stable_shape():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    posting = normalize_posting(raw)
    assert isinstance(posting, JobPosting)
    assert posting.id
    assert posting.title
    assert posting.company
    assert posting.url.startswith("http")


def test_normalize_posting_tolerates_missing_posted_date():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    raw.pop("postedAt", None)
    assert normalize_posting(raw).posted_date is None
