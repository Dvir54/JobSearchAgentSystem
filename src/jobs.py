"""The only module that knows where jobs come from.

Jobs are fetched through Monid (monid.ai), which routes to the Apify
harvestapi LinkedIn job-search actor. Everything downstream consumes
JobPosting; swapping the source means changing config.MONID_ENDPOINT and
this file's input/normalization, and nothing else.
"""
from dataclasses import dataclass

from config import (
    EXPERIENCE_LEVELS,
    LOCATION,
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
    return JobPosting(
        id=str(raw["id"]),
        title=raw["title"],
        company=company.get("name", ""),
        description=raw.get("descriptionText", ""),
        url=raw["linkedinUrl"],
        posted_date=raw.get("postedDate"),
    )


def fetch_jobs(run, queries):
    """Fetch and normalize postings via a Monid run callable.

    `run` is monid.run_and_wait bound to a session, called as
    run(provider, endpoint, run_input) -> list[dict].

    Raises RuntimeError on zero results: an empty run means the source is
    broken or blocked, not that Israel has no jobs today — abort before any
    Claude spend.
    """
    items = run(MONID_PROVIDER, MONID_ENDPOINT, build_harvestapi_input(queries))
    postings = [normalize_posting(item) for item in items]
    if not postings:
        raise RuntimeError(
            "Monid harvestapi returned zero results. The source is likely "
            "broken or blocked — check it before spending on Claude."
        )
    return postings
