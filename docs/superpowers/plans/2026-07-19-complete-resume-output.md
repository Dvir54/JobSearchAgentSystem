# Complete Tailored Resume Output — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each qualifying job produce a complete, well-structured, job-tailored resume (all sections) instead of only three tailored fragments.

**Architecture:** The base CV becomes structured markdown (`base_cv.md`). A new `resume.py` parses it into sections and entries; tailored sections (About Me, Skills, Work Experience, Projects) go to Claude, static sections are copied verbatim. Claude returns experience/project entries *by reference* (index + reworded bullets), so factual anchors always come from the file. A new `render.py` reassembles the full resume in original section order.

**Tech Stack:** Python 3.11+, `anthropic`, `pydantic`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-19-complete-resume-output-design.md`

## Global Constraints

- Claude model: **`claude-opus-4-8`** exactly. `max_tokens` **16000**. Never pass `temperature`/`top_p`/`top_k`/`budget_tokens` (they 400 on this model).
- Structured output only via `client.messages.parse(..., output_format=Model)` → `response.parsed_output`.
- Tailoring keeps `thinking={"type": "adaptive"}`; scoring does not stream and is unchanged.
- **Anchors are never sent to Claude for rewriting.** Entry `### ` header lines, Work Experience date lines, and Project tech lines are copied verbatim from `base_cv.md`.
- The tailored section names live in `config.TAILORED_SECTIONS`; classification is by name, no hardcoded literals in logic.
- `base_cv.md` is gitignored and never committed. Tests use `tests/fixtures/sample_cv.md`, never the real CV.
- No network in the test suite; Claude is stubbed with a fake client.
- `jobs.py` and `scoring.py` are not modified.

## File Structure

| File | Responsibility |
|---|---|
| `config.py` | **Modify.** Add the tailored-section name constants; point `BASE_CV_PATH` at `base_cv.md`. |
| `resume.py` | **Create.** The CV-source seam: parse `base_cv.md` into preamble + ordered sections + entries. Owns the markdown layout. |
| `tailoring.py` | **Modify.** New `TailoredCV`/`TailoredEntry` models, entry-aware prompt, `tailor_cv(client, posting, parsed)`, unchanged `find_invented_skills`, new `find_entry_coverage_errors`. |
| `render.py` | **Create.** Assemble the full output file (metadata block + complete resume) from the parsed CV and the tailored content. |
| `main.py` | **Modify.** Parse the CV once, call the new `tailor_cv`, run both guards, write via `render`. |
| `tests/fixtures/sample_cv.md` | **Create.** Small synthetic structured CV for parsing/rendering tests. |

---

## Task 1: The CV source seam (`resume.py`)

**Files:**
- Modify: `config.py`
- Create: `resume.py`, `tests/fixtures/sample_cv.md`
- Test: `tests/test_resume.py`

**Interfaces:**
- Consumes: `config.TAILORED_SECTIONS`.
- Produces:
  - `Entry(anchor: str, bullets: list[str])` — frozen dataclass.
  - `Section(name: str, is_tailored: bool, body: str, entries: list[Entry])` — frozen dataclass.
  - `ParsedResume(preamble: str, sections: list[Section])` with `.get(name) -> Section | None`.
  - `parse_resume(text: str) -> ParsedResume`.

- [ ] **Step 1: Add section constants to `config.py`**

Append to `config.py`:
```python
# Sections rewritten per job. Every other section is copied verbatim.
SUMMARY_SECTION = "About Me"
SKILLS_SECTION = "Skills"
EXPERIENCE_SECTION = "Work Experience"
PROJECTS_SECTION = "Projects"
TAILORED_SECTIONS = (SUMMARY_SECTION, SKILLS_SECTION, EXPERIENCE_SECTION, PROJECTS_SECTION)
```

Change the base CV path line from `base_cv.txt` to:
```python
BASE_CV_PATH = PROJECT_ROOT / "base_cv.md"
```

- [ ] **Step 2: Create the test fixture `tests/fixtures/sample_cv.md`**

```markdown
# Test Candidate

**Software Engineer**

test@example.com

---

## About Me

A short summary about the candidate.

---

## Work Experience

### Backend Developer | Acme Corp
*Jan 2024 - present*

- Built services in Python.
- Maintained a Postgres database.

### Intern | Beta Ltd
*Jun 2023 - Dec 2023*

- Wrote automation scripts.

---

## Projects

### Todo App
Python, Flask

### Chat Bot
Python, WebSockets

---

## Education

### B.Sc. Computer Science
Some University | 2020 - 2024

---

## Skills

Python, SQL, Git

---

## Languages

- English - Fluent
```

- [ ] **Step 3: Write the failing tests**

`tests/test_resume.py`:
```python
from pathlib import Path

from resume import Entry, ParsedResume, Section, parse_resume

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_cv.md").read_text(encoding="utf-8")


def _parsed() -> ParsedResume:
    return parse_resume(SAMPLE)


def test_preamble_captured_and_excludes_sections():
    parsed = _parsed()
    assert "Test Candidate" in parsed.preamble
    assert "Software Engineer" in parsed.preamble
    assert "About Me" not in parsed.preamble


def test_sections_are_in_file_order():
    names = [s.name for s in _parsed().sections]
    assert names == [
        "About Me", "Work Experience", "Projects",
        "Education", "Skills", "Languages",
    ]


def test_tailored_flag_matches_config():
    parsed = _parsed()
    assert parsed.get("About Me").is_tailored is True
    assert parsed.get("Skills").is_tailored is True
    assert parsed.get("Work Experience").is_tailored is True
    assert parsed.get("Education").is_tailored is False
    assert parsed.get("Languages").is_tailored is False


def test_work_experience_entries_preserve_two_line_anchor_and_bullets():
    entries = _parsed().get("Work Experience").entries
    assert len(entries) == 2
    first = entries[0]
    assert first.anchor == "### Backend Developer | Acme Corp\n*Jan 2024 - present*"
    assert first.bullets == ["Built services in Python.", "Maintained a Postgres database."]


def test_project_entries_keep_name_and_tech_and_have_no_bullets():
    entries = _parsed().get("Projects").entries
    assert len(entries) == 2
    assert entries[0].anchor == "### Todo App\nPython, Flask"
    assert entries[0].bullets == []


def test_static_section_body_is_kept_verbatim():
    body = _parsed().get("Education").body
    assert "### B.Sc. Computer Science" in body
    assert "Some University | 2020 - 2024" in body


def test_static_section_has_no_parsed_entries():
    assert _parsed().get("Education").entries == []
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resume.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'resume'`.

- [ ] **Step 5: Write `resume.py`**

```python
"""The CV source seam: parses base_cv.md into sections and entries.

Only this module knows the base CV's markdown layout. It never rewrites
content — parsing preserves anchors (entry headers, date lines, project
tech lines) verbatim so downstream tailoring cannot alter facts.
"""
import re
from dataclasses import dataclass

from config import TAILORED_SECTIONS

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_ENTRY_RE = re.compile(r"^###\s+")
_BULLET_RE = re.compile(r"^-\s+")


@dataclass(frozen=True)
class Entry:
    anchor: str          # verbatim: the ### line plus any non-bullet lines under it
    bullets: list[str]   # each bullet without its leading "- "


@dataclass(frozen=True)
class Section:
    name: str
    is_tailored: bool
    body: str            # cleaned text; used for static sections and About Me/Skills
    entries: list[Entry]  # populated only for tailored sections that contain ### entries


@dataclass(frozen=True)
class ParsedResume:
    preamble: str            # verbatim block above the first ## section
    sections: list[Section]  # in file order

    def get(self, name: str) -> "Section | None":
        for section in self.sections:
            if section.name == name:
                return section
        return None


def _clean(lines: list[str]) -> str:
    """Drop standalone --- rules and trim surrounding blank lines."""
    kept = [ln for ln in lines if ln.strip() != "---"]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(ln.rstrip() for ln in kept)


def _build_entry(lines: list[str]) -> Entry:
    anchor_lines: list[str] = []
    bullets: list[str] = []
    in_bullets = False
    for ln in lines:
        if _BULLET_RE.match(ln):
            in_bullets = True
            bullets.append(_BULLET_RE.sub("", ln).strip())
        elif not in_bullets and ln.strip():
            anchor_lines.append(ln.rstrip())
    return Entry(anchor="\n".join(anchor_lines), bullets=bullets)


def _parse_entries(lines: list[str]) -> list[Entry]:
    entries: list[Entry] = []
    current: "list[str] | None" = None
    for ln in lines:
        if _ENTRY_RE.match(ln):
            if current is not None:
                entries.append(_build_entry(current))
            current = [ln]
        elif current is not None:
            current.append(ln)
    if current is not None:
        entries.append(_build_entry(current))
    return entries


def parse_resume(text: str) -> ParsedResume:
    lines = text.splitlines()

    idx = 0
    while idx < len(lines) and not _SECTION_RE.match(lines[idx]):
        idx += 1
    preamble = _clean(lines[:idx])

    sections: list[Section] = []
    while idx < len(lines):
        name = _SECTION_RE.match(lines[idx]).group(1)
        idx += 1
        body_lines: list[str] = []
        while idx < len(lines) and not _SECTION_RE.match(lines[idx]):
            body_lines.append(lines[idx])
            idx += 1
        is_tailored = name in TAILORED_SECTIONS
        has_entries = any(_ENTRY_RE.match(ln) for ln in body_lines)
        entries = _parse_entries(body_lines) if (is_tailored and has_entries) else []
        sections.append(
            Section(name=name, is_tailored=is_tailored, body=_clean(body_lines), entries=entries)
        )
    return ParsedResume(preamble=preamble, sections=sections)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resume.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add config.py resume.py tests/test_resume.py tests/fixtures/sample_cv.md
git commit -m "feat: parse base_cv.md into sections and entries"
```

---

## Task 2: Entry-aware tailoring (`tailoring.py`)

**Files:**
- Modify: `tailoring.py`
- Test: `tests/test_tailoring.py` (replace existing contents)

**Interfaces:**
- Consumes: `resume.ParsedResume`, `jobs.JobPosting`, the `config` section constants.
- Produces:
  - `TailoredEntry(entry_index: int, bullets: list[str])` — pydantic model.
  - `TailoredCV(summary: str, skills: list[str], experience: list[TailoredEntry], projects: list[TailoredEntry])` — pydantic model.
  - `tailor_cv(client, posting: JobPosting, parsed: ParsedResume) -> TailoredCV`.
  - `find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]` (unchanged behavior).
  - `find_entry_coverage_errors(tailored: TailoredCV, parsed: ParsedResume) -> list[str]`.

- [ ] **Step 1: Replace `tests/test_tailoring.py` with the new failing tests**

```python
from jobs import JobPosting
from resume import parse_resume
from tailoring import (
    TailoredCV,
    TailoredEntry,
    build_tailoring_prompt,
    find_entry_coverage_errors,
    find_invented_skills,
    tailor_cv,
)

BASE_MD = """# Cand

test@example.com

## About Me

I build things.

## Work Experience

### Backend Developer | Acme
*2024 - now*

- Built APIs in Python.

### Intern | Beta
*2023*

- Wrote scripts.

## Projects

### Todo App
Python, Flask

## Skills

Python, SQL, Docker
"""

PARSED = parse_resume(BASE_MD)


class FakeMessages:
    def __init__(self, parsed_output):
        self._parsed_output = parsed_output
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return type("Response", (), {"parsed_output": self._parsed_output})()


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _posting() -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description="Python and Docker.", url="https://example.com", posted_date=None,
    )


def _valid_tailored() -> TailoredCV:
    return TailoredCV(
        summary="s",
        skills=["Python", "Docker"],
        experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                    TailoredEntry(entry_index=0, bullets=["b"])],
        projects=[TailoredEntry(entry_index=0, bullets=[])],
    )


def test_prompt_lists_indexed_entries_and_job():
    prompt = build_tailoring_prompt(PARSED, _posting())
    assert "[0]" in prompt and "[1]" in prompt
    assert "Backend Developer | Acme" in prompt
    assert "Todo App" in prompt
    assert "Python and Docker." in prompt


def test_tailor_cv_returns_parsed_output():
    expected = _valid_tailored()
    client = FakeClient(expected)
    assert tailor_cv(client, _posting(), PARSED) == expected


def test_tailor_cv_uses_right_model_and_no_bad_params():
    client = FakeClient(_valid_tailored())
    tailor_cv(client, _posting(), PARSED)
    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is TailoredCV
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_find_invented_skills_flags_absent_technology():
    tailored = TailoredCV(summary="s", skills=["Python", "Kubernetes"],
                          experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                                      TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    assert find_invented_skills(tailored, BASE_MD) == ["Kubernetes"]


def test_find_invented_skills_accepts_present_skills_case_insensitively():
    tailored = TailoredCV(summary="s", skills=["python", "DOCKER"],
                          experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                                      TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    assert find_invented_skills(tailored, BASE_MD) == []


def test_entry_coverage_accepts_full_reordered_coverage():
    assert find_entry_coverage_errors(_valid_tailored(), PARSED) == []


def test_entry_coverage_flags_missing_experience_entry():
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["b"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    errors = find_entry_coverage_errors(tailored, PARSED)
    assert any("experience" in e for e in errors)


def test_entry_coverage_flags_duplicate_and_out_of_range():
    dup = TailoredCV(summary="s", skills=["Python"],
                     experience=[TailoredEntry(entry_index=0, bullets=["b"]),
                                 TailoredEntry(entry_index=0, bullets=["b"])],
                     projects=[TailoredEntry(entry_index=5, bullets=[])])
    errors = find_entry_coverage_errors(dup, PARSED)
    assert any("experience" in e for e in errors)
    assert any("projects" in e for e in errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: FAIL — `ImportError` for `TailoredEntry` / `build_tailoring_prompt` / `find_entry_coverage_errors`.

- [ ] **Step 3: Rewrite `tailoring.py`**

Replace the whole file with:
```python
"""Judgment point two: adapt the CV to one specific posting.

Truthfulness is enforced by construction and by checks. Entry anchors
(titles, dates, project names) never pass through Claude — the model
returns entries by index and reworded bullets only. find_invented_skills()
and find_entry_coverage_errors() gate the result before it is written.
"""
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
  most relevant entry comes first. For each, rewrite its bullets to surface the skills
  this posting cares about. Reference every index exactly once — never drop, add, or
  duplicate an entry.
- projects: every Project entry, referenced by its index, reordered so the most
  relevant comes first. Projects have no bullets to rewrite; return an empty bullet
  list for each. Reference every index exactly once.

Hard constraints on truth:
- Every claim must be one the candidate could defend in an interview.
- Never add a technology, tool, employer, project, or metric that is not already in
  the CV. Do not imply, hint, or use adjacent phrasing to suggest one.
- Never invent numbers. Reuse the candidate's real metrics, or omit metrics entirely.

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


def find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]:
    """Return skills present in the tailored output but absent from the base CV."""
    haystack = base_cv.lower()
    return [skill for skill in tailored.skills if skill.lower() not in haystack]


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tailoring.py tests/test_tailoring.py
git commit -m "feat: entry-aware tailoring with coverage guard"
```

---

## Task 3: Full-resume assembly (`render.py`)

**Files:**
- Create: `render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `resume.ParsedResume`, `tailoring.TailoredCV`, the `config` section constants; a `posting` (uses `.url`) and a `score` (uses `.fit_score`, `.reason`).
- Produces: `render_output(posting, score, parsed: ParsedResume, tailored: TailoredCV) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_render.py`:
```python
from pathlib import Path

from render import render_output
from resume import parse_resume
from tailoring import TailoredCV, TailoredEntry

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_cv.md").read_text(encoding="utf-8")
PARSED = parse_resume(SAMPLE)


class FakePosting:
    url = "https://example.com/job"


class FakeScore:
    fit_score = 82
    reason = "Strong match."


def _tailored() -> TailoredCV:
    return TailoredCV(
        summary="Tailored summary here.",
        skills=["SQL", "Python", "Git"],
        experience=[TailoredEntry(entry_index=1, bullets=["Reworded intern bullet."]),
                    TailoredEntry(entry_index=0, bullets=["Reworded acme bullet."])],
        projects=[TailoredEntry(entry_index=1, bullets=[]),
                  TailoredEntry(entry_index=0, bullets=[])],
    )


def _out() -> str:
    return render_output(FakePosting(), FakeScore(), PARSED, _tailored())


def test_metadata_block_is_above_the_resume():
    out = _out()
    assert out.index("**Fit:** 82/100") < out.index("---") < out.index("# Test Candidate")
    assert "https://example.com/job" in out


def test_all_sections_present_in_original_order():
    out = _out()
    positions = [out.index(f"## {name}") for name in
                 ["About Me", "Work Experience", "Projects", "Education", "Skills", "Languages"]]
    assert positions == sorted(positions)


def test_static_sections_are_verbatim():
    out = _out()
    assert "### B.Sc. Computer Science" in out
    assert "English - Fluent" in out


def test_tailored_summary_and_skills_are_used():
    out = _out()
    assert "Tailored summary here." in out
    assert "SQL, Python, Git" in out


def test_experience_anchor_is_verbatim_with_reworded_bullets_in_new_order():
    out = _out()
    intern = out.index("### Intern | Beta Ltd")
    acme = out.index("### Backend Developer | Acme Corp")
    assert intern < acme  # reordered: entry_index 1 first
    assert "*Jan 2024 - present*" in out
    assert "Reworded acme bullet." in out


def test_projects_keep_tech_line_and_have_no_bullets():
    out = _out()
    assert "### Chat Bot\nPython, WebSockets" in out
    assert "### Todo App\nPython, Flask" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'render'`.

- [ ] **Step 3: Write `render.py`**

```python
"""Assemble the complete output file: a metadata block above a full,
job-tailored resume. Static sections are copied verbatim; tailored sections
are rendered from the model; entry anchors always come from the base CV.
"""
from config import EXPERIENCE_SECTION, PROJECTS_SECTION, SKILLS_SECTION, SUMMARY_SECTION
from resume import ParsedResume
from tailoring import TailoredCV


def _render_entries(tailored_entries, base_entries) -> str:
    blocks: list[str] = []
    for tailored in tailored_entries:
        anchor = base_entries[tailored.entry_index].anchor
        lines = [anchor]
        if tailored.bullets:
            lines.append("")
            lines += [f"- {bullet}" for bullet in tailored.bullets]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_resume(parsed: ParsedResume, tailored: TailoredCV) -> str:
    experience = parsed.get(EXPERIENCE_SECTION)
    projects = parsed.get(PROJECTS_SECTION)
    exp_entries = experience.entries if experience else []
    proj_entries = projects.entries if projects else []

    parts = [parsed.preamble]
    for section in parsed.sections:
        if section.name == SUMMARY_SECTION:
            parts.append(f"## {section.name}\n\n{tailored.summary}")
        elif section.name == SKILLS_SECTION:
            parts.append(f"## {section.name}\n\n{', '.join(tailored.skills)}")
        elif section.name == EXPERIENCE_SECTION:
            parts.append(f"## {section.name}\n\n{_render_entries(tailored.experience, exp_entries)}")
        elif section.name == PROJECTS_SECTION:
            parts.append(f"## {section.name}\n\n{_render_entries(tailored.projects, proj_entries)}")
        else:
            parts.append(f"## {section.name}\n\n{section.body}")
    return "\n\n".join(parts)


def render_output(posting, score, parsed: ParsedResume, tailored: TailoredCV) -> str:
    metadata = "\n".join([
        f"- **Fit:** {score.fit_score}/100 — {score.reason}",
        f"- **Apply at:** {posting.url}",
        "",
        "---",
        "",
    ])
    return metadata + _render_resume(parsed, tailored) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add render.py tests/test_render.py
git commit -m "feat: assemble complete tailored resume output"
```

---

## Task 4: Wire the pipeline (`main.py`)

**Files:**
- Modify: `main.py`
- Test: none new — full suite plus a dry run verify it.

**Interfaces:**
- Consumes: `resume.parse_resume`, the new `tailoring.tailor_cv`/`find_entry_coverage_errors`, `render.render_output`.

- [ ] **Step 1: Update imports in `main.py`**

Replace the three project-import lines:
```python
import config
from jobs import JobPosting, build_search_url, fetch_jobs
from scoring import JobScore, score_job
from tailoring import TailoredCV, find_invented_skills, tailor_cv
```
with:
```python
import config
from jobs import JobPosting, build_search_url, fetch_jobs
from render import render_output
from resume import parse_resume
from scoring import score_job
from tailoring import find_entry_coverage_errors, find_invented_skills, tailor_cv
```

- [ ] **Step 2: Replace `write_cv` in `main.py`**

Replace the whole `write_cv` function with:
```python
def write_cv(posting, score, tailored, parsed, out_dir):
    """Write one complete tailored resume: metadata block above the resume."""
    content = render_output(posting, score, parsed, tailored)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text(content, encoding="utf-8")
    return path
```

(Keep `safe_filename` unchanged.)

- [ ] **Step 3: Parse the CV once in `main()`**

Find:
```python
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
```
and add the parse right after it:
```python
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)
```

- [ ] **Step 4: Update the tailor + guard + write block in `main()`**

Replace the tailor/guard/write portion of the loop:
```python
        # 3. Tailor.
        tailored = tailor_cv(claude, posting, base_cv)

        invented = find_invented_skills(tailored, base_cv)
        if invented:
            print(f"  DROP  {posting.company}: invented skills {', '.join(invented)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, config.OUTPUT_DIR)
```
with:
```python
        # 3. Tailor.
        tailored = tailor_cv(claude, posting, parsed)

        problems = find_invented_skills(tailored, base_cv) + find_entry_coverage_errors(tailored, parsed)
        if problems:
            print(f"  DROP  {posting.company}: {'; '.join(problems)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, parsed, config.OUTPUT_DIR)
```

- [ ] **Step 5: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all tests pass across jobs, scoring, resume, tailoring, render.

- [ ] **Step 6: Dry run (no spend)**

Run: `.venv/Scripts/python.exe main.py --dry-run`
Expected: prints the six URLs and the ~$0.15 projection, spends nothing, no import errors.

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: pipeline emits complete tailored resumes"
```

---

## Task 5: First live run of the new output

No new code. **Requires `base_cv.md` at the project root.** This run spends money (Apify + one Claude scoring call per posting, plus tailoring for each job clearing the threshold).

- [ ] **Step 1: Run it (optionally lower `COUNT_PER_QUERY` first for a cheaper run; the actor floor is 10)**

Run: `.venv/Scripts/python.exe main.py`
Expected: writes `output/{Company}_{Role}.md` for each job scoring ≥ 70.

- [ ] **Step 2: Read a generated resume end-to-end**

For a written file, confirm: the metadata block sits above a `---`, then the full resume follows starting with the name/contact preamble. Every section is present in the original order. Static sections (Education, Languages, Military Service, Volunteering) are verbatim. Work Experience anchors (titles + dates) and Project tech lines are exactly as in `base_cv.md`. Only bullets and the summary/skills are reworded, and every claim traces to `base_cv.md`.

- [ ] **Step 3: Confirm the guards behaved**

Check the run log for any `DROP` lines. An invented-skill or coverage drop is the guard working; if a job you expected was dropped for coverage, inspect whether the CV has an unusual entry the prompt mishandled.

- [ ] **Step 4: Commit what you learned**

```bash
git commit --allow-empty -m "chore: first live run of complete-resume output — <N> resumes; <what you observed>"
```

**The complete-resume output is now working end-to-end.**
