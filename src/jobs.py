"""The only module that knows where jobs come from.

Everything downstream consumes JobPosting. Swapping to JSearch means
rewriting this file and nothing else.
"""
from dataclasses import dataclass
from urllib.parse import quote

from config import ACTOR_ID


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None


def build_search_url(keyword: str) -> str:
    """Build a LinkedIn job-search URL. f_E=2 is LinkedIn's entry-level filter."""
    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote(keyword)}&location=Israel&f_E=2"
    )


def normalize_posting(raw: dict) -> JobPosting:
    """Map one raw actor item to JobPosting. Field names verified in Spike A."""
    return JobPosting(
        id=str(raw["id"]),
        title=raw["title"],
        company=raw["companyName"],
        description=raw.get("descriptionText", ""),
        url=raw["link"],
        posted_date=raw.get("postedAt"),
    )


def fetch_jobs(client, keywords: list[str], count: int) -> list[JobPosting]:
    """Run the actor once per keyword and return normalized postings.

    Raises RuntimeError on zero results across all queries: that means the
    community-maintained scraper broke, not that Israel has no jobs today.
    """
    postings: list[JobPosting] = []
    for keyword in keywords:
        run = client.actor(ACTOR_ID).call(
            run_input={"urls": [build_search_url(keyword)], "count": count}
        )
        items = list(client.dataset(run.default_dataset_id).iterate_items())
        postings.extend(normalize_posting(item) for item in items)

    if not postings:
        raise RuntimeError(
            "Apify actor returned zero results across all queries. "
            "The scraper is likely broken or blocked — check it before spending on Claude."
        )
    return postings
