"""Judgment point two: adapt the CV to one specific posting.

Truthfulness is enforced by construction and by checks. Entry anchors
(titles, dates, project names) never pass through Claude — the model
returns entries by index and reworded bullets only. find_invented_skills()
and find_entry_coverage_errors() gate the result before it is written.
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


def _canonicalize(text: str) -> str:
    """Lowercase and replace known skill shortcuts with their canonical form,
    matching whole words only."""
    text = text.lower()
    for alias, canon in SKILL_ALIASES.items():
        text = re.sub(rf"\b{re.escape(alias)}\b", canon, text)
    return text


def _skill_in_cv(skill: str, canon_cv: str) -> bool:
    """True if the alias-resolved skill appears as a whole word/phrase in the
    already-alias-resolved CV text. Word boundaries stop 'React' matching inside
    'reactive'; symbol-edged skills (c++, .net) drop the boundary on that edge."""
    canon_skill = _canonicalize(skill).strip()
    if not canon_skill:
        return False
    left = r"\b" if canon_skill[0].isalnum() else ""
    right = r"\b" if canon_skill[-1].isalnum() else ""
    return re.search(left + re.escape(canon_skill) + right, canon_cv) is not None


def find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]:
    """Return tailored skills whose canonical form does not appear in the base CV.
    Alias-aware (JS == JavaScript) and word-boundary-aware (React != reactive)."""
    canon_cv = _canonicalize(base_cv)
    return [skill for skill in tailored.skills if not _skill_in_cv(skill, canon_cv)]


def strip_invented_skills(tailored: TailoredCV, base_cv: str) -> tuple[TailoredCV, list[str]]:
    """Remove skills absent from the base CV, keeping the résumé. Returns the
    cleaned CV and the list of removed skills (for logging)."""
    invented = find_invented_skills(tailored, base_cv)
    if not invented:
        return tailored, []
    invented_set = set(invented)
    kept = [s for s in tailored.skills if s not in invented_set]
    return tailored.model_copy(update={"skills": kept}), invented


def find_entry_coverage_errors(tailored: TailoredCV, parsed: ParsedResume) -> list[str]:
    """Return problems if the tailored entries do not reference each base entry
    exactly once. Catches dropped, duplicated, and out-of-range indices."""
    experience = parsed.get(EXPERIENCE_SECTION)
    projects = parsed.get(PROJECTS_SECTION)
    checks = [
        ("experience", tailored.experience, len(experience.entries) if experience else 0),
        ("projects", tailored.projects, len(projects.entries) if projects else 0),
    ]
    errors: list[str] = []
    for label, tailored_entries, count in checks:
        got = sorted(entry.entry_index for entry in tailored_entries)
        if got != list(range(count)):
            errors.append(f"{label}: expected each of {list(range(count))} once, got {got}")
    return errors
