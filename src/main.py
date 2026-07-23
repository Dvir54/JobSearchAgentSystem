"""Entry point. Owns the sequence; every decision needing judgment is a call
into scoring.py or tailoring.py. Jobs come from Monid (see jobs.py / monid.py).
"""
import argparse
import json
import os
import re
import sys

import anthropic
import requests
from dotenv import load_dotenv

import config
import monid
from jobs import JobPosting, build_harvestapi_input, fetch_jobs
from render import render_output
from resume import parse_resume
from scoring import score_job
from tailoring import repair_entry_coverage, strip_invented_skills, tailor_cv


def safe_filename(posting: JobPosting) -> str:
    """Company names come from a scraper — never trust them as path components."""
    company = re.sub(r'[<>:"/\\|?*]', "", posting.company).strip().replace(" ", "_")
    title = re.sub(r'[<>:"/\\|?*]', "", posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.md"


def write_cv(posting, score, tailored, parsed, out_dir, notes=None):
    """Write one complete tailored resume: metadata block (with any correction
    notes) above the resume."""
    content = render_output(posting, score, parsed, tailored, notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find junior jobs in Israel and tailor CVs.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the harvestapi search input and projected cost, then exit.",
    )
    args = parser.parse_args()

    # Windows consoles default to cp1252, which cannot encode the output below.
    sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv()

    projected = len(config.ROLE_QUERIES) * config.MAX_ITEMS_PER_QUERY
    cost = projected * 0.0015 + len(config.ROLE_QUERIES) * 0.001
    print(f"Queries: {len(config.ROLE_QUERIES)} x {config.MAX_ITEMS_PER_QUERY} results")
    print(f"Projected: ~{projected} harvestapi results ≈ ${cost:.2f} via Monid")

    if args.dry_run:
        print(json.dumps(build_harvestapi_input(config.ROLE_QUERIES), indent=2))
        return 0

    if not config.BASE_CV_PATH.exists():
        print(f"error: {config.BASE_CV_PATH} not found. It is the source of truth for CV content.")
        return 1
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {os.environ['MONID_API_KEY']}"
    claude = anthropic.Anthropic()

    def run(provider, endpoint, run_input):
        return monid.run_and_wait(session, provider, endpoint, run_input)

    # 1. Search. Raises if the source returned nothing — abort before Claude spend.
    postings = fetch_jobs(run, config.ROLE_QUERIES)
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
        tailored = tailor_cv(claude, posting, parsed)

        # 4. Repair, don't drop: strip invented skills and fix entry coverage,
        #    keeping the résumé and logging every correction.
        tailored, removed_skills = strip_invented_skills(tailored, base_cv)
        tailored, notes = repair_entry_coverage(tailored, parsed)
        if removed_skills:
            notes = [f"removed unverified skills: {', '.join(removed_skills)}"] + notes

        # 5. Write.
        path = write_cv(posting, score, tailored, parsed, config.OUTPUT_DIR, notes)
        written += 1
        tag = "  (auto-corrected)" if notes else ""
        print(f"  WRITE [{score.fit_score:3}] ({score.match_kind:7}) {path.name}{tag}")
        for note in notes:
            print(f"          - {note}")

    print(f"\n{written} tailored CVs in {config.OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
