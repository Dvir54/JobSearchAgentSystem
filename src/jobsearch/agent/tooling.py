"""Deterministic tooling behind the agent's tools. No claude_agent_sdk import —
this stays unit-testable without the SDK or any agent run.
"""
import json
import sys
from dataclasses import dataclass

from jobsearch.resume import canva
from jobsearch import config
from jobsearch.config import (
    EXPERIENCE_SECTION,
    LOCATION_KEYWORD,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobsearch.agent.jobs import normalize_posting
from jobsearch.resume.base_cv import parse_resume
from jobsearch.resume.render import pdf_filename
from jobsearch.resume.tailoring import (
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


# Full postings, held in process so descriptions never have to fit in a tool result.
# Written by reduce_run_payload, read by get_job. Same pattern as the Canva hook's
# retained geometry: the data the agent needs stays here, and it asks for the one
# piece it is working on.
_JOBS_BY_ID = {}

# Run-scoped state. The run id is set by cli.run before the session starts and is
# deliberately NOT a tool argument: the model has no reason to know it, and a
# wrong value would file a CV under someone else's run.
_RUN_ID = None
_EXAMINED = 0
_MATCHED = 0

VERDICTS = ("matched", "rejected")


def set_run_id(run_id):
    global _RUN_ID, _EXAMINED, _MATCHED
    _RUN_ID = run_id
    _EXAMINED = 0
    _MATCHED = 0


def current_run_id():
    return _RUN_ID


def examined_count():
    return _EXAMINED


def matched_count():
    return _MATCHED


def _db_conn():
    """Isolated so tests can substitute a connection or make it fail."""
    from jobsearch import db
    return db.session()


def record_verdict(job_id, title, company, fit_score, verdict, reason):
    """Remember that this job was judged, kept or not.

    This is what makes tomorrow skip it. Returns an error rather than raising:
    one unrecorded verdict must cost one job, never the run.
    """
    global _EXAMINED
    if verdict not in VERDICTS:
        return {"error": f"verdict must be one of {VERDICTS}, got {verdict!r}"}
    try:
        from jobsearch import db
        db.record_verdict(job_id, _RUN_ID, title, company, fit_score, verdict,
                          reason, conn=_db_conn())
    except Exception as exc:                  # noqa: BLE001 - report, never abort
        print(f"[record_verdict] {job_id}: FAILED ({exc!r}) — this job will be "
              f"re-scored tomorrow", file=sys.stderr)
        return {"error": str(exc)}
    _EXAMINED += 1
    return {"recorded": True}


def _manifest_entry(job):
    """The only fields that cross into a tool result for every job at once."""
    return {"id": job["id"], "title": job["title"], "company": job["company"]}


def get_job(job_id):
    """One posting in full, by id — the descriptions the manifest deliberately omits.

    Returns an `error` rather than raising: an unknown id must cost one job, not
    the run. The description is capped so that no single posting can approach the
    32KB inline ceiling either.
    """
    job = _JOBS_BY_ID.get(str(job_id))
    if job is None:
        known = len(_JOBS_BY_ID)
        return {"error": f"no job with id {job_id!r} in this run ({known} known). "
                         f"Use an id exactly as it appeared in the manifest."}
    description = job.get("description") or ""
    if len(description) > config.MAX_JOB_DESCRIPTION_CHARS:
        keep = config.MAX_JOB_DESCRIPTION_CHARS
        print(f"[get_job] {job_id}: description {len(description)} chars, truncated "
              f"to {keep}", file=sys.stderr)
        description = description[:keep] + "\n[description truncated]"
    return {**job, "description": description, "error": ""}


@dataclass
class _Score:
    is_junior_friendly: bool
    fit_score: int
    reason: str
    match_kind: str


def _budget_exceeded(new_text, original_text):
    return len(new_text) > len(original_text) * config.LENGTH_BUDGET_RATIO


def _summary_limit(original_summary):
    return int(len(original_summary) * config.SUMMARY_LENGTH_BUDGET_RATIO)


def summary_char_limit():
    """The summary budget in characters, for telling the drafter up front.

    Discovering this limit by being rejected costs a tool call per job. Stating it
    in the instructions costs nothing.
    """
    section = parse_resume(config.BASE_CV_PATH.read_text(encoding="utf-8")).get(
        SUMMARY_SECTION)
    return _summary_limit(section.body.strip()) if section else 0


def _strip_trailing_period(text):
    """Drop a trailing full stop from both sides of a find/replace pair.

    base_cv.md and the Canva design do not agree on trailing punctuation — the
    Ness bullet ends with '.' in the design and without one in the markdown. Since
    find_and_replace_text matches a substring, the design's own trailing character
    is left in place and completes the replacement, so each entry keeps whatever
    punctuation style the template already used. Stripping both sides is what makes
    the find text match in the first place, and stops a bullet that already ends in
    '.' from rendering as '..'.
    """
    return text[:-1] if text.endswith(".") else text


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
        base_bullets = base_entries[entry.entry_index].bullets
        if len(entry.bullets) != len(base_bullets):
            return _reject(
                f"slot {slot!r} has {len(entry.bullets)} bullet(s) but base_cv.md has "
                f"{len(base_bullets)}. Bullets must be reworded one-to-one, never "
                f"added, dropped, split, or merged.")
        joined = "\n".join(entry.bullets)
        original = "\n".join(base_bullets)
        if _budget_exceeded(joined, original):
            return _reject(
                f"slot {slot!r} exceeds the length budget "
                f"({len(joined)} chars vs {len(original)} original)")
        # find/replace pairs, not one wholesale string: see canva.build_operations
        # for why the bullet blocks cannot take a replace_text.
        edits[slot] = [{"find": _strip_trailing_period(old),
                        "replace": _strip_trailing_period(new)}
                       for old, new in zip(base_bullets, entry.bullets)]

    summary_section = parsed.get(SUMMARY_SECTION)
    if summary_section:
        original_summary = summary_section.body.strip()
        if len(summary) > _summary_limit(original_summary):
            # Numbers, not just a verdict: without them the only way back is to
            # bisect, which costs a tool call per guess on an unattended run.
            return _reject(
                f"slot 'summary' exceeds the length budget: {len(summary)} chars, "
                f"limit {_summary_limit(original_summary)} (the base summary is "
                f"{len(original_summary)}). Shorten it and call prepare_resume again.")

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


# Filled by reduce_run_payload, read by cli.run to close the run row. The counts
# come from the reducer rather than from the agent's summary: the model can be
# wrong about what it did, this cannot.
_RUN_STATS = {"fetched": 0, "kept": 0, "dropped_duplicate": 0,
              "dropped_non_israel": 0, "dropped_seen": 0}


def last_run_stats():
    return dict(_RUN_STATS)


def _query_unseen_ids(job_ids):
    """Isolated so tests can make the database fail without one running."""
    from jobsearch import db
    return db.filter_unseen(job_ids)


def _unseen_ids(job_ids):
    """Job ids never examined on a previous run.

    Degrades to "everything is new" when the database cannot be reached. Losing
    dedup costs a day of re-scoring; failing here would cost the whole day's
    postings, and the 24h window means they never come back.
    """
    try:
        return _query_unseen_ids(job_ids)
    except Exception as exc:                  # noqa: BLE001 - degrade, never abort
        print(f"[reduce] WARNING: cross-run dedup unavailable ({exc!r}) — every "
              f"job will be re-scored and re-tailored this run", file=sys.stderr)
        return {str(job_id) for job_id in job_ids}


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
        israeli = clean_jobs(items)
        fetched = len(items)
        unique = len({str(i.get("id")) for i in items if isinstance(i, dict)})
        dropped_duplicate = fetched - unique
        dropped_non_israel = unique - len(israeli)

        # Cross-run dedup. Runs here, in code, before any posting reaches the
        # model — so a job examined yesterday costs neither tokens nor judgement.
        unseen = _unseen_ids([job["id"] for job in israeli])
        jobs = [job for job in israeli if str(job["id"]) in unseen]
        dropped_seen = len(israeli) - len(jobs)
        kept = len(jobs)

        # Stays keyed on `items`, not on the post-Israel list: the alarm that
        # matters is "we fetched postings and kept none", whatever dropped them.
        # Narrowing it to the Israeli subset would silence exactly the case it
        # was written for — an upstream schema change in the location field.
        if items and not jobs:
            print(f"[reduce] WARNING: all {fetched} fetched postings were filtered "
                  f"out (kept=0, dropped_non_israel={dropped_non_israel}, "
                  f"dropped_seen={dropped_seen}) — likely causes: upstream schema "
                  f"change in the 'location' field, or every posting already seen",
                  file=sys.stderr)

        # Descriptions stay here; only the manifest crosses into the tool result.
        _JOBS_BY_ID.clear()
        for job in jobs:
            _JOBS_BY_ID[str(job["id"])] = job

        envelope = {
            "status": "COMPLETED",
            "runId": run.get("runId"),
            "window": _window(run),
            "fetched": fetched,
            "kept": kept,
            "dropped_duplicate": dropped_duplicate,
            "dropped_non_israel": dropped_non_israel,
            "dropped_seen": dropped_seen,
            "jobs": [_manifest_entry(job) for job in jobs],
            "note": ("`jobs` lists every kept posting by id/title/company only. "
                     "Call `get_job` with an id for that posting's description, "
                     "url and location."),
        }
        _RUN_STATS.update(fetched=fetched, kept=kept,
                          dropped_duplicate=dropped_duplicate,
                          dropped_non_israel=dropped_non_israel,
                          dropped_seen=dropped_seen)
        text = json.dumps(envelope, ensure_ascii=False)

        # Should be unreachable — the manifest is ~97 bytes a job, so this needs
        # ~250 postings. If it ever fires, the run degrades visibly (fewer jobs,
        # counts still honest) instead of being silently cut to a 2KB preview,
        # which is exactly how the 2026-08-11 run failed.
        if len(text) > config.SAFE_ENVELOPE_BYTES:
            room = config.SAFE_ENVELOPE_BYTES - (len(text) - len(json.dumps(
                envelope["jobs"], ensure_ascii=False)))
            fits = max(1, room // 120)
            print(f"[reduce] WARNING: manifest for {kept} jobs is {len(text)} bytes, "
                  f"over the {config.SAFE_ENVELOPE_BYTES} budget (CLI drops anything "
                  f"past {config.INLINE_RESULT_LIMIT_BYTES} to a 2KB preview). "
                  f"Listing the first {fits}; the rest are unreachable this run.",
                  file=sys.stderr)
            envelope["jobs"] = envelope["jobs"][:fits]
            envelope["manifest_truncated_to"] = fits
            text = json.dumps(envelope, ensure_ascii=False)
    except Exception as exc:                      # noqa: BLE001 - degrade, never crash the run
        print(f"[reduce] reduction failed ({exc!r}); passing raw output through",
              file=sys.stderr)
        return None

    print(f"[reduce] run={envelope['runId']} window={envelope['window']} "
          f"fetched={fetched} kept={kept} "
          f"dropped_duplicate={dropped_duplicate} "
          f"dropped_non_israel={dropped_non_israel} "
          f"dropped_seen={dropped_seen} "
          f"envelope={len(text)}B/{config.INLINE_RESULT_LIMIT_BYTES}B "
          f"descriptions_held={len(_JOBS_BY_ID)}", file=sys.stderr)
    return text


def _fetch_bytes(url):
    """Isolated so tests can substitute it without touching the network."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()



def save_pdf(export_url, job_id, canva_design_id, canva_url):
    """Download an exported Canva PDF and store it in the database.

    The agent has no Bash/Read/Write/WebFetch - those are denied so a failure
    cannot degrade into hand-parsing - so downloading has to happen here.

    Takes the job by id, not by company/title: the full posting is already held
    in `_JOBS_BY_ID`, so location, apply URL and posted date come from what the
    source actually said rather than from the model retyping it.

    Returns rather than raises: one job's failed download must not end the run.
    """
    global _MATCHED
    job = _JOBS_BY_ID.get(str(job_id))
    if job is None:
        message = (f"no job with id {job_id!r} in this run; cannot store a CV "
                   f"for a posting that was never listed")
        print(f"[save_pdf] {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": ""}

    filename = pdf_filename(job["company"], job["title"], job["id"])
    try:
        payload = _fetch_bytes(export_url)
    except Exception as exc:                     # noqa: BLE001 - report, never abort the run
        print(f"[save_pdf] {filename}: download failed ({exc})", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    if not payload:
        message = "download was empty; nothing stored"
        print(f"[save_pdf] {filename}: {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": filename}

    try:
        from jobsearch import db
        db.insert_match(job["id"], _RUN_ID, title=job["title"],
                        company=job["company"], location=job.get("location"),
                        apply_url=job.get("url"),
                        posted_date=job.get("posted_date"),
                        canva_design_id=canva_design_id, canva_url=canva_url,
                        pdf=payload, pdf_filename=filename, conn=_db_conn())
    except Exception as exc:                     # noqa: BLE001 - report, never abort the run
        print(f"[save_pdf] {filename}: STORE FAILED ({exc!r}) — the Canva design "
              f"exists but this CV is not in the database", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    _MATCHED += 1
    print(f"[save_pdf] stored {filename} ({len(payload):,} bytes)",
          file=sys.stderr)
    return {"saved": filename, "error": "", "filename": filename}


