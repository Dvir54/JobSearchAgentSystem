"""Deterministic tooling behind the agent's tools. No claude_agent_sdk import —
this stays unit-testable without the SDK or any agent run.
"""
import json
import re
import sys
from dataclasses import dataclass

import canva
import config
from config import (
    EXPERIENCE_SECTION,
    LOCATION_KEYWORD,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobs import normalize_posting
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
class _Score:
    is_junior_friendly: bool
    fit_score: int
    reason: str
    match_kind: str


def _sanitise_path_component(value):
    """Strip characters that make a value unsafe as a path component. Used on
    company, title, and job_id alike — all three come from a scraper."""
    return re.sub(r'[<>:"/\\|?*]', "", value).strip().replace(" ", "_")


def safe_filename(company, title, job_id=None):
    """Company/title/job_id come from a scraper — never trust them as path
    components. Two distinct postings can share company+title (e.g. two roles
    both titled "Software Engineer" at the same employer), so job_id — when
    present — disambiguates the filename; without it, two such postings would
    silently overwrite each other."""
    c = _sanitise_path_component(company)
    t = _sanitise_path_component(title)
    if job_id:
        j = _sanitise_path_component(str(job_id))
        return f"{c}_{t}_{j}.md"
    return f"{c}_{t}.md"


def _budget_exceeded(new_text, original_text):
    return len(new_text) > len(original_text) * config.LENGTH_BUDGET_RATIO


def prepare_resume(job, score, tailored):
    """The deterministic half of the enforcement boundary.

    Gates on relevance, strips invented skills, repairs entry coverage, and checks
    the length budget, then returns a slot-keyed edit plan AND the ready-to-send
    Canva `operations` built from it (via canva.build_operations), so the agent
    never has to map a slot name to an element_id itself. It writes nothing: the
    PreToolUse hook on perform-editing-operations is what actually holds the line,
    because the agent makes the Canva calls itself.
    """
    def _reject(reason):
        return {"rejected": True, "reason": reason, "corrections": [], "edits": {},
                "operations": []}

    s = _Score(**{k: score[k] for k in ("is_junior_friendly", "fit_score", "reason", "match_kind")})
    if not (s.is_junior_friendly and s.fit_score >= config.FIT_THRESHOLD):
        return _reject(f"below threshold or not junior-friendly (fit {s.fit_score})")

    summary = tailored.get("summary")
    if not isinstance(summary, str):
        return _reject(f"summary must be a single paragraph string, got "
                       f"{type(summary).__name__}")

    skills = tailored.get("skills")
    if isinstance(skills, (list, tuple)):
        skills = list(skills)
    elif isinstance(skills, str):
        skills = [part.strip() for part in skills.split(",") if part.strip()]
    else:
        return _reject(f"skills must be a list or comma-separated string, got "
                       f"{type(skills).__name__}")

    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)
    try:
        tcv = TailoredCV(
            summary=summary,
            skills=skills,
            experience=[TailoredEntry(entry_index=e["entry_index"], bullets=list(e["bullets"]))
                        for e in tailored["experience"]],
            projects=[TailoredEntry(entry_index=p["entry_index"], bullets=list(p["bullets"]))
                      for p in tailored["projects"]],
        )
    except KeyError as exc:
        return _reject(f"experience/project entry missing required key {exc}")

    tcv, removed = strip_invented_skills(tcv, base_cv)
    tcv, notes = repair_entry_coverage(tcv, parsed)
    if removed:
        notes = [f"removed unverified skills: {', '.join(removed)}"] + notes

    # Skills may have been stripped; rebuild the summary regions only if a skill
    # name was removed from them is NOT attempted — the guards own skills, not prose.
    edits = {"summary": summary, "skills": "\n".join(tcv.skills)}

    experience_section = parsed.get(EXPERIENCE_SECTION)
    base_entries = experience_section.entries if experience_section else []
    for entry in tcv.experience:
        slot = f"experience.{entry.entry_index}.bullets"
        if slot not in config.CANVA_ELEMENT_MAP:
            continue                      # entry exists in base_cv but not in the design
        edits[slot] = "\n".join(entry.bullets)
        original = "\n".join(base_entries[entry.entry_index].bullets)
        if _budget_exceeded(edits[slot], original):
            return _reject(
                f"slot {slot!r} exceeds the length budget "
                f"({len(edits[slot])} chars vs {len(original)} original)")

    summary_section = parsed.get(SUMMARY_SECTION)
    if summary_section and _budget_exceeded(summary, summary_section.body.strip()):
        return _reject("slot 'summary' exceeds the length budget")

    operations = canva.build_operations(edits, config.CANVA_ELEMENT_MAP)
    return {"rejected": False, "reason": "", "corrections": notes, "edits": edits,
            "operations": operations}


def _window(run):
    """The posting-age window Monid echoes back from the input that actually ran."""
    body = (run.get("input") or {}).get("body") or {}
    return body.get("postedLimit")


# Monid's own terminal set, per the monid_get_run tool description: "Poll every few
# seconds until status is terminal (COMPLETED, FAILED, BLOCKED, TIMED_OUT)."
TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "BLOCKED", "TIMED_OUT",
                               "STOPPED", "CANCELLED"})


def is_run_in_progress(tool_response):
    """True when this payload is a Monid run that has NOT reached a terminal status —
    i.e. the agent is mid-poll and will immediately call `monid_get_run` again.

    Used only to pace that poll loop. A wrong answer here costs a few seconds of
    wall time, never correctness, so this stays deliberately tolerant and never
    raises. It parses independently of `reduce_run_payload` on purpose: that
    function is verified working and is not worth refactoring for six lines.
    """
    try:
        run = tool_response if isinstance(tool_response, dict) else json.loads(tool_response)
    except (TypeError, ValueError):
        return False
    if not isinstance(run, dict):
        return False
    status = run.get("status")
    return status is not None and status not in TERMINAL_STATUSES


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
            preview = tool_response[:120] if isinstance(tool_response, str) else repr(tool_response)[:120]
            print(f"[reduce] DECLINED: tool_response is not JSON — reduction did NOT run. "
                  f"If this says 'exceeds maximum allowed tokens', raise "
                  f"config.MAX_MCP_OUTPUT_TOKENS. Received: {preview!r}", file=sys.stderr)
            return None

    if not isinstance(run, dict):
        print(f"[reduce] DECLINED: tool_response parsed to a non-dict "
              f"({type(run).__name__}) — reduction did NOT run.", file=sys.stderr)
        return None

    status = run.get("status")
    if status != "COMPLETED":
        if status == "RUNNING":
            print(f"[reduce] INFO: run status=RUNNING — still polling, nothing to "
                  f"reduce yet; passing through.", file=sys.stderr)
        else:
            print(f"[reduce] DECLINED: run status={status!r} (not COMPLETED) — "
                  f"reduction did NOT run; passing through.", file=sys.stderr)
        return None

    items = run.get("output")
    if not isinstance(items, list):
        print(f"[reduce] DECLINED: run['output'] is not a list "
              f"({type(items).__name__}) — reduction did NOT run; passing "
              f"through.", file=sys.stderr)
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


def _fetch_bytes(url):
    """Isolated so tests can substitute it without touching the network."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def run_dir(today=None):
    """output/<YYYY-MM-DD>/ for this run, created on demand."""
    from datetime import date
    stamp = today or date.today().isoformat()
    path = config.OUTPUT_DIR / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_pdf(export_url, company, title, job_id, today=None):
    """Download an exported Canva PDF into this run's output directory.

    The agent has no Bash/Read/Write/WebFetch - those are denied so a failure
    cannot degrade into hand-parsing - so downloading has to happen here.
    Returns rather than raises: one job's failed download must not end the run.
    """
    from render import pdf_filename
    filename = pdf_filename(company, title, job_id)
    try:
        payload = _fetch_bytes(export_url)
    except Exception as exc:                     # noqa: BLE001 - report, never abort the run
        print(f"[save_pdf] {filename}: download failed ({exc})", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    if not payload:
        message = "download was empty; nothing written"
        print(f"[save_pdf] {filename}: {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": filename}

    path = run_dir(today) / filename
    path.write_bytes(payload)
    print(f"[save_pdf] wrote {path} ({len(payload):,} bytes)", file=sys.stderr)
    return {"saved": str(path), "error": "", "filename": filename}


def write_index(entries, window, skipped_count, today=None):
    """Write this run's index.md - the operator view that cannot live inside a CV."""
    from render import render_index
    path = run_dir(today) / "index.md"
    path.write_text(render_index(entries, window, skipped_count), encoding="utf-8")
    print(f"[write_index] wrote {path} ({len(entries)} entries, "
          f"{skipped_count} skipped)", file=sys.stderr)
    return {"written": str(path)}
