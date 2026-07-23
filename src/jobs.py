"""The only module that knows where jobs come from.

Jobs are fetched through Monid (monid.ai), which routes to the Apify
harvestapi LinkedIn job-search actor. Everything downstream consumes
JobPosting; swapping the source means changing config.MONID_ENDPOINT and
this file's input/normalization, and nothing else.
"""
import sys
from dataclasses import dataclass

from config import (
    EXPERIENCE_LEVELS,
    LOCATION,
    LOCATION_KEYWORD,
    MAX_ITEMS_PER_QUERY,
    MONID_ENDPOINT,
    MONID_PROVIDER,
    POSTED_LIMIT,
)


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None
    location: str | None = None


def build_harvestapi_input(queries):
    """Build the harvestapi request body. All queries go in one call; the
    actor runs each jobTitle server-side. Filters come from config."""
    return {
        "body": {
            "jobTitles": list(queries),
            "locations": [LOCATION],
            "experienceLevel": list(EXPERIENCE_LEVELS),
            "maxItems": MAX_ITEMS_PER_QUERY,
            "postedLimit": POSTED_LIMIT,
            "sortBy": "date",
        }
    }


def normalize_posting(raw):
    """Map one harvestapi item to JobPosting. Field names verified against a
    live harvestapi response (company is nested under 'company')."""
    company = raw.get("company") or {}
    raw_location = raw.get("location")
    if isinstance(raw_location, dict):
        location = raw_location.get("linkedinText")
    elif isinstance(raw_location, str):
        location = raw_location
    else:
        location = None
    return JobPosting(
        id=str(raw["id"]),
        title=raw["title"],
        company=company.get("name", ""),
        description=raw.get("descriptionText", ""),
        url=raw["linkedinUrl"],
        posted_date=raw.get("postedDate"),
        location=location,
    )


def fetch_jobs(run, queries):
    """Fetch, dedup, and Israel-filter postings via a Monid run callable.

    `run` is monid.run_and_wait bound to a session, called as
    run(provider, endpoint, run_input) -> list[dict].

    Overlapping role queries return the same job id multiple times, so we dedup
    by id (keeping the first) before scoring. The source's location filter leaks
    non-Israel remote roles (tagged EMEA/MENA), so we keep only postings whose
    location contains LOCATION_KEYWORD.

    Raises RuntimeError only when the raw response is empty — that means the
    source is broken or blocked, not that Israel has no jobs today. An empty
    result after filtering is returned as an empty list.
    """
    items = run(MONID_PROVIDER, MONID_ENDPOINT, build_harvestapi_input(queries))
    if not items:
        raise RuntimeError(
            "Monid harvestapi returned zero results. The source is likely "
            "broken or blocked — check it before spending on Claude."
        )
    seen = set()
    unique = []
    for item in items:
        posting = normalize_posting(item)
        if posting.id in seen:
            continue
        seen.add(posting.id)
        unique.append(posting)
    filtered = [
        p for p in unique
        if p.location and LOCATION_KEYWORD in p.location.lower()
    ]
    if items and not filtered:
        print(
            f"warning: fetched {len(items)} postings but none passed "
            "dedup/Israel filter — check the source's location shape",
            file=sys.stderr,
        )
    return filtered
