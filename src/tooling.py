"""Deterministic tooling behind the agent's tools. No claude_agent_sdk import —
this stays unit-testable without the SDK or any agent run.
"""
from config import (
    EXPERIENCE_SECTION,
    LOCATION_KEYWORD,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobs import normalize_posting
from resume import parse_resume


def _entries(parsed, section_name):
    section = parsed.get(section_name)
    if not section:
        return []
    return [{"index": i, "anchor": e.anchor, "bullets": list(e.bullets)}
            for i, e in enumerate(section.entries)]


def build_resume_view(base_cv_text):
    """Return the résumé as the agent needs it: summary, skills, and Work
    Experience / Project entries labelled by their original index."""
    parsed = parse_resume(base_cv_text)
    summary = parsed.get(SUMMARY_SECTION)
    skills = parsed.get(SKILLS_SECTION)
    return {
        "summary": summary.body if summary else "",
        "skills": skills.body if skills else "",
        "experience": _entries(parsed, EXPERIENCE_SECTION),
        "projects": _entries(parsed, PROJECTS_SECTION),
    }


def clean_jobs(raw_items):
    """Normalize raw Monid/harvestapi items, dedupe by id (first wins), and keep
    only Israel-located postings. Mirrors the old jobs.fetch_jobs post-processing."""
    seen = set()
    jobs = []
    for item in raw_items:
        posting = normalize_posting(item)
        if posting.id in seen:
            continue
        seen.add(posting.id)
        if posting.location and LOCATION_KEYWORD in posting.location.lower():
            jobs.append({
                "id": posting.id, "title": posting.title, "company": posting.company,
                "description": posting.description, "url": posting.url,
                "posted_date": posting.posted_date, "location": posting.location,
            })
    return jobs
