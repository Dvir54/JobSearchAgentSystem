"""Judgment point two: adapt the CV to one specific posting.

Truthfulness is enforced by construction and by checks. Entry anchors
(titles, dates, project names) never pass through Claude — the model
returns entries by index and reworded bullets only. find_invented_skills()
and repair_entry_coverage() gate the result before it is written.
"""
import re

from pydantic import BaseModel

from config import EXPERIENCE_SECTION, PROJECTS_SECTION
from resume import ParsedResume


class TailoredEntry(BaseModel):
    entry_index: int
    bullets: list[str]


class TailoredCV(BaseModel):
    summary: str
    skills: list[str]
    experience: list[TailoredEntry]
    projects: list[TailoredEntry]


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
            suffix = " with its original bullets" if with_bullets else ""
            notes.append(f"{label}: re-added missing entry [{idx}]{suffix}")
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
