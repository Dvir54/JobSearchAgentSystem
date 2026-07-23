"""Judgment point two: adapt the CV to one specific posting.

Truthfulness is enforced by construction and by checks. Entry anchors
(titles, dates, project names) never pass through Claude — the model
returns entries by index and reworded bullets only. find_invented_skills()
and repair_entry_coverage() gate the result before it is written.
"""
import re

from pydantic import BaseModel

from config import (
    CLAUDE_MODEL,
    EXPERIENCE_SECTION,
    MAX_TOKENS,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobs import JobPosting
from resume import ParsedResume


class TailoredEntry(BaseModel):
    entry_index: int
    bullets: list[str]


class TailoredCV(BaseModel):
    summary: str
    skills: list[str]
    experience: list[TailoredEntry]
    projects: list[TailoredEntry]


SYSTEM_PROMPT = """You are a CV editor. You adapt one candidate's existing CV to one
specific job posting. You are given the candidate's summary, skills, and their
Work Experience and Project entries, each entry labelled with an index like [0], [1].

Work through this before writing anything:
1. Extract the posting's real requirements — the 5-8 skills and technologies that
   actually matter, and the role's core focus. Ignore boilerplate.
2. Build an evidence matrix. For each requirement, find the candidate's proof in the
   CV: what proves it outright, what partially proves it, and what is missing entirely.
3. Rewrite only what the evidence supports. Leave the gaps as gaps.

Produce:
- summary: 2-3 sentences positioning the candidate for this specific role, built only
  from evidence in the CV.
- skills: the candidate's skills, ordered so the ones this posting names come first.
  Include only skills already in the CV.
- experience: every Work Experience entry, referenced by its index, reordered so the
  most relevant entry comes first. For each entry, reword the bullets it already has to
  surface the skills this posting cares about. Return exactly the same number of bullets
  the entry already has, in the same one-to-one correspondence — reword each, but never
  add a new bullet, never split one bullet into two, never merge two into one. Reference
  every index exactly once — never drop, add, or duplicate an entry.
- projects: every Project entry, referenced by its index, reordered so the most
  relevant comes first. Projects have no bullets to rewrite; return an empty bullet
  list for each. Reference every index exactly once.

Hard constraints on truth:
- Every claim must be one the candidate could defend in an interview.
- Never add a technology, tool, employer, project, or metric that is not already in
  the CV. Do not imply, hint, or use adjacent phrasing to suggest one.
- Never invent numbers. Reuse the candidate's real metrics, or omit metrics entirely.
- Never add a bullet point. A bullet that is not a rewording of an existing bullet is
  invented experience, even when it sounds plausible. Keep each entry's bullet count.

Hard constraints on sounding natural — these matter as much as accuracy:
- Do not copy the posting's phrasing verbatim. Use the ordinary vocabulary of the field.
- Never force a keyword into a bullet where the underlying work did not involve it.
- No hype or buzzwords: no "leverage", "synergy", "spearheaded", "passionate",
  "results-driven", "cutting-edge", "ninja", "rockstar".
- Keep the candidate's own voice. The result must read like they rewrote it themselves."""


def _format_entries(section) -> str:
    if section is None:
        return ""
    blocks: list[str] = []
    for i, entry in enumerate(section.entries):
        lines = [f"[{i}] {entry.anchor}"]
        lines += [f"    - {bullet}" for bullet in entry.bullets]
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def build_tailoring_prompt(parsed: ParsedResume, posting: JobPosting) -> str:
    summary = parsed.get(SUMMARY_SECTION)
    skills = parsed.get(SKILLS_SECTION)
    return f"""Candidate summary:
{summary.body if summary else ""}

Candidate skills:
{skills.body if skills else ""}

Work Experience entries (reorder by relevance; rewrite each entry's bullets):
{_format_entries(parsed.get(EXPERIENCE_SECTION))}

Project entries (reorder by relevance; keep name and tech as-is, no bullets):
{_format_entries(parsed.get(PROJECTS_SECTION))}

Target job posting:
Title: {posting.title}
Company: {posting.company}
Description:
{posting.description}"""


def tailor_cv(client, posting: JobPosting, parsed: ParsedResume) -> TailoredCV:
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_tailoring_prompt(parsed, posting)}],
        output_format=TailoredCV,
    )
    return response.parsed_output


# Common skill shortcuts → canonical form. Applied to BOTH the tailored skill and
# the CV text, so "JS" matches "JavaScript" written either way.
SKILL_ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "postgres": "postgresql",
    "psql": "postgresql",
    "k8s": "kubernetes",
    "gha": "github actions",
    "gcp": "google cloud",
    "node": "node.js",
    "nodejs": "node.js",
}


def _alias_forms(skill: str) -> set[str]:
    """All acceptable spellings of a skill: the skill itself, its canonical alias,
    and any shortcuts that canonicalize to it. We compare against the raw CV text,
    so the CV is never mutated (and never corrupted)."""
    forms = {skill}
    if skill in SKILL_ALIASES:
        forms.add(SKILL_ALIASES[skill])
    for alias, canon in SKILL_ALIASES.items():
        if canon == skill:
            forms.add(alias)
    return forms


def _present(form: str, cv_lower: str) -> bool:
    """True if `form` appears as a whole word/phrase in the lowercased CV. Word
    boundaries stop 'react' matching inside 'reactive'; symbol edges (c++, .net)
    drop the boundary on that side so they still match."""
    if not form:
        return False
    left = r"\b" if form[0].isalnum() else ""
    right = r"\b" if form[-1].isalnum() else ""
    return re.search(left + re.escape(form) + right, cv_lower) is not None


def find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]:
    """Return tailored skills whose canonical form is absent from the base CV.
    Alias-aware (JS == JavaScript, either direction) and word-boundary-aware
    (React != reactive)."""
    cv_lower = base_cv.lower()
    invented: list[str] = []
    for skill in tailored.skills:
        forms = _alias_forms(skill.lower().strip())
        if not any(_present(form, cv_lower) for form in forms):
            invented.append(skill)
    return invented


def strip_invented_skills(tailored: TailoredCV, base_cv: str) -> tuple[TailoredCV, list[str]]:
    """Remove skills absent from the base CV, keeping the résumé. Returns the
    cleaned CV and the list of removed skills (for logging)."""
    invented = find_invented_skills(tailored, base_cv)
    if not invented:
        return tailored, []
    invented_set = set(invented)
    kept = [s for s in tailored.skills if s not in invented_set]
    return tailored.model_copy(update={"skills": kept}), invented


def _repair_section(tailored_entries, base_entries, label, with_bullets):
    """Return (valid_entries, notes): drop out-of-range and duplicate indices,
    then append any missing base entry (in original order) using its own bullets."""
    count = len(base_entries)
    notes: list[str] = []
    seen: set[int] = set()
    result: list[TailoredEntry] = []
    for entry in tailored_entries:
        idx = entry.entry_index
        if idx < 0 or idx >= count:
            notes.append(f"{label}: removed out-of-range index [{idx}]")
            continue
        if idx in seen:
            notes.append(f"{label}: removed duplicate index [{idx}]")
            continue
        seen.add(idx)
        result.append(entry)
    for idx in range(count):
        if idx not in seen:
            bullets = list(base_entries[idx].bullets) if with_bullets else []
            result.append(TailoredEntry(entry_index=idx, bullets=bullets))
            notes.append(f"{label}: re-added missing entry [{idx}] with its original bullets")
    return result, notes


def repair_entry_coverage(tailored: TailoredCV, parsed: ParsedResume) -> tuple[TailoredCV, list[str]]:
    """Make experience/projects reference each base entry exactly once, keeping the
    résumé. Drops out-of-range and duplicate indices; re-adds any missing base entry
    at the end with its original bullets. Returns the repaired CV and repair notes."""
    exp_section = parsed.get(EXPERIENCE_SECTION)
    proj_section = parsed.get(PROJECTS_SECTION)
    exp_entries = exp_section.entries if exp_section else []
    proj_entries = proj_section.entries if proj_section else []

    repaired_exp, exp_notes = _repair_section(tailored.experience, exp_entries, "experience", True)
    repaired_proj, proj_notes = _repair_section(tailored.projects, proj_entries, "projects", False)

    if not exp_notes and not proj_notes:
        return tailored, []
    repaired = tailored.model_copy(update={"experience": repaired_exp, "projects": repaired_proj})
    return repaired, exp_notes + proj_notes
