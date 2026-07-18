"""Entry point. Owns the sequence; every decision needing judgment is a call
into scoring.py or tailoring.py.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import anthropic
from apify_client import ApifyClient
from dotenv import load_dotenv

import config
from jobs import JobPosting, build_search_url, fetch_jobs
from scoring import JobScore, score_job
from tailoring import TailoredCV, find_invented_skills, tailor_cv


def safe_filename(posting: JobPosting) -> str:
    """Company names come from a scraper — never trust them as path components."""
    company = re.sub(r'[<>:"/\\|?*]', "", posting.company).strip().replace(" ", "_")
    title = re.sub(r'[<>:"/\\|?*]', "", posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.md"


def write_cv(posting: JobPosting, score: JobScore, tailored: TailoredCV, out_dir: Path) -> Path:
    """Write one tailored CV. The job URL is in the header: a CV with no link
    back to its posting is unusable."""
    lines = [
        f"# {posting.company} — {posting.title}",
        "",
        f"- **Fit:** {score.fit_score}/100 — {score.reason}",
        f"- **Apply at:** {posting.url}",
        "",
        "## Summary",
        "",
        tailored.summary,
        "",
        "## Experience",
        "",
        *[f"- {bullet}" for bullet in tailored.bullets],
        "",
        "## Skills",
        "",
        " · ".join(tailored.skills),
        "",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find junior jobs in Israel and tailor CVs.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the search URLs and projected cost, then exit without spending.",
    )
    args = parser.parse_args()

    # Windows consoles default to cp1252, which cannot encode the output below.
    sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv()

    projected = len(config.ROLE_QUERIES) * config.COUNT_PER_QUERY
    print(f"Queries: {len(config.ROLE_QUERIES)} x {config.COUNT_PER_QUERY} results")
    print(f"Projected: ~{projected} Apify results ≈ ${projected / 1000:.2f}")
    print("Apify's free plan hard-stops at $5/month and blocks until the next cycle.")

    if args.dry_run:
        for keyword in config.ROLE_QUERIES:
            print(f"  {build_search_url(keyword)}")
        return 0

    if not config.BASE_CV_PATH.exists():
        print(f"error: {config.BASE_CV_PATH} not found. It is the source of truth for CV content.")
        return 1
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")

    apify = ApifyClient(os.environ["APIFY_API_TOKEN"])
    claude = anthropic.Anthropic()

    # 1. Search. Raises if the scraper returned nothing — abort before Claude spend.
    postings = fetch_jobs(apify, config.ROLE_QUERIES, config.COUNT_PER_QUERY)
    print(f"Fetched {len(postings)} postings.\n")

    written = 0
    for posting in postings:
        # 2. Score.
        score = score_job(claude, posting, base_cv)
        relevant = score.is_junior_friendly and score.fit_score >= config.FIT_THRESHOLD
        if not relevant:
            print(f"  skip  [{score.fit_score:3}] {posting.company} — {posting.title}")
            continue

        # 3. Tailor.
        tailored = tailor_cv(claude, posting, base_cv)

        invented = find_invented_skills(tailored, base_cv)
        if invented:
            print(f"  DROP  {posting.company}: invented skills {', '.join(invented)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, config.OUTPUT_DIR)
        written += 1
        print(f"  WRITE [{score.fit_score:3}] {path.name}")

    print(f"\n{written} tailored CVs in {config.OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
