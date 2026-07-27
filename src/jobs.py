"""The only module that knows where jobs come from.

Jobs are fetched through Monid (monid.ai), which routes to the Apify
harvestapi LinkedIn job-search actor. Everything downstream consumes
JobPosting; swapping the source means changing config.MONID_ENDPOINT and
this file's normalization, and nothing else.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None
    location: str | None = None


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
