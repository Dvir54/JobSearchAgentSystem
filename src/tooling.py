"""Deterministic tooling behind the agent's tools. No claude_agent_sdk import —
this stays unit-testable without the SDK or any agent run.
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import config
from config import (
    EXPERIENCE_SECTION,
    LOCATION_KEYWORD,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobs import normalize_posting
from render import render_output
from resume import parse_resume
from tailoring import (
    TailoredCV,
    TailoredEntry,
    repair_entry_coverage,
    strip_invented_skills,
)


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


@dataclass
class _Posting:
    company: str
    title: str
    url: str


@dataclass
class _Score:
    is_junior_friendly: bool
    fit_score: int
    reason: str
    match_kind: str


def safe_filename(company, title):
    """Company/title come from a scraper — never trust them as path components."""
    c = re.sub(r'[<>:"/\\|?*]', "", company).strip().replace(" ", "_")
    t = re.sub(r'[<>:"/\\|?*]', "", title).strip().replace(" ", "_")
    return f"{c}_{t}.md"


def write_tailored_resume(job, score, tailored, out_dir=None):
    """The enforcement boundary. Gates on relevance, strips invented skills, repairs
    entry coverage, renders, and writes. Returns what it wrote or why it refused.
    The agent cannot write a résumé any other way."""
    out_dir = Path(out_dir) if out_dir else config.OUTPUT_DIR
    s = _Score(**{k: score[k] for k in ("is_junior_friendly", "fit_score", "reason", "match_kind")})

    if not (s.is_junior_friendly and s.fit_score >= config.FIT_THRESHOLD):
        return {"written": None, "rejected": True,
                "reason": f"below threshold or not junior-friendly (fit {s.fit_score})",
                "corrections": []}

    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)
    tcv = TailoredCV(
        summary=tailored["summary"],
        skills=list(tailored["skills"]),
        experience=[TailoredEntry(entry_index=e["entry_index"], bullets=list(e["bullets"]))
                    for e in tailored["experience"]],
        projects=[TailoredEntry(entry_index=p["entry_index"], bullets=list(p["bullets"]))
                  for p in tailored["projects"]],
    )

    tcv, removed = strip_invented_skills(tcv, base_cv)
    tcv, notes = repair_entry_coverage(tcv, parsed)
    if removed:
        notes = [f"removed unverified skills: {', '.join(removed)}"] + notes

    posting = _Posting(company=job["company"], title=job["title"], url=job["url"])
    content = render_output(posting, s, parsed, tcv, notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(job["company"], job["title"])
    path.write_text(content, encoding="utf-8")
    return {"written": str(path), "rejected": False, "reason": "", "corrections": notes}


def _window(run):
    """The posting-age window Monid echoes back from the input that actually ran."""
    body = (run.get("input") or {}).get("body") or {}
    return body.get("postedLimit")


def reduce_run_payload(tool_response):
    """Reduce a `monid_get_run` payload to the jobs the agent actually needs.

    Returns the reduced envelope as JSON text, or None meaning "pass the original
    through untouched". None is returned for every case this cannot fully vouch
    for: a non-COMPLETED run (the agent is still polling, or needs to see a real
    error), an unparseable or unexpected shape, or a reducer failure. A failed
    optimisation must degrade to today's behaviour, never to a silent empty result.
    """
    if isinstance(tool_response, dict):
        run = tool_response
    else:
        try:
            run = json.loads(tool_response)
        except (TypeError, ValueError):
            return None

    if not isinstance(run, dict) or run.get("status") != "COMPLETED":
        return None

    items = run.get("output")
    if not isinstance(items, list):
        return None

    # Everything below — the reducer, the window lookup, and serialisation —
    # must degrade to pass-through on ANY failure, never escape to the model.
    try:
        jobs = clean_jobs(items)
        fetched = len(items)
        unique = len({str(i.get("id")) for i in items if isinstance(i, dict)})
        kept = len(jobs)
        dropped_duplicate = fetched - unique
        dropped_non_israel = unique - kept

        if items and not jobs:
            print(f"[reduce] WARNING: all {fetched} fetched postings were filtered "
                  f"out (kept=0, dropped_non_israel={dropped_non_israel}) — likely "
                  f"cause: upstream schema change in the 'location' field", file=sys.stderr)

        envelope = {
            "status": "COMPLETED",
            "runId": run.get("runId"),
            "window": _window(run),
            "fetched": fetched,
            "kept": kept,
            "dropped_duplicate": dropped_duplicate,
            "dropped_non_israel": dropped_non_israel,
            "jobs": jobs,
        }
        text = json.dumps(envelope, ensure_ascii=False)
    except Exception as exc:                      # noqa: BLE001 - degrade, never crash the run
        print(f"[reduce] reduction failed ({exc!r}); passing raw output through",
              file=sys.stderr)
        return None

    print(f"[reduce] run={envelope['runId']} window={envelope['window']} "
          f"fetched={fetched} kept={kept} "
          f"dropped_duplicate={dropped_duplicate} "
          f"dropped_non_israel={dropped_non_israel}", file=sys.stderr)
    return text
