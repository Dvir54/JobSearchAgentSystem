# Complete Tailored Resume Output — Design

**Date**: 2026-07-19
**Status**: Draft, awaiting review
**Builds on**: `2026-07-17-job-search-agent-design.md` (Phase 1)

## Problem

Phase 1 writes only the three sections it tailors — summary, experience, skills — and
silently drops the rest of the candidate's resume (contact, education, languages,
military service, volunteering, and projects as a distinct section). The output is a
fragment, not a resume the candidate can send. The goal is a **complete, well-structured,
job-tailored resume** as the output of each qualifying job.

## What changes, at a glance

- The base CV becomes structured markdown (`base_cv.md`), replacing the flat `base_cv.txt`.
- A new `resume.py` module parses that file into sections and entries.
- Sections split into **tailored** (rewritten per job) and **static** (copied verbatim).
- `tailoring.py` grows to cover all four tailored sections and returns experience/project
  entries *by reference*, so factual anchors come from the file, never from Claude.
- `main.py` reassembles a full resume in the CV's original section order.
- `jobs.py` and `scoring.py` are unchanged.

## Input format: `base_cv.md`

Markdown with explicit structure the parser relies on:

- **Preamble** — everything above the first `##` (name, title, contact lines) is the
  static header block, copied verbatim.
- **`## Section Name`** starts a section.
- Inside a **tailored** section, **`### Entry`** starts an entry. Work Experience anchors
  span the `###` line plus the immediately following non-bullet line(s) (e.g. the italic
  `*dates*` line); `-` lines are the tailorable bullets. Project entries are `### Name`
  plus the tech-stack line right after (both verbatim); projects carry no bullets, so
  tailoring a project means reordering only.
- `---` horizontal rules between sections are cosmetic and ignored by the parser.
- `###` inside a **static** section (e.g. Education) is irrelevant — static sections are
  copied whole and never split into entries.

The real resume lives only on the candidate's machine; `base_cv.md` is gitignored.

## Section classification (config-driven)

`config.py` gains one tuple:

```python
TAILORED_SECTIONS = ("About Me", "Skills", "Work Experience", "Projects")
```

Any parsed section whose name is in this tuple is tailored; every other section, and the
preamble, is static and copied verbatim. Changing behavior is editing this tuple — no code
change. `BASE_CV_PATH` changes from `base_cv.txt` to `base_cv.md`.

## New module: `resume.py`

The "CV source seam," mirroring `jobs.py`. It owns the `base_cv.md` format so nothing else
knows the layout. It produces:

- `Section(name: str, body: str, is_tailored: bool)` — one per `##` section, in file order.
- `preamble: str` — the verbatim header block above the first `##`.
- `Entry(anchor: str, bullets: list[str])` — parsed from tailored Work Experience and
  Projects sections. `anchor` is verbatim (never sent to Claude for rewriting).
- A parse entry point returning the ordered sections, the preamble, and the parsed entries
  for the tailored sections.

## Tailoring changes: `tailoring.py`

Still **one Claude call per qualifying job**. The output model covers all four tailored
sections; experience and projects are returned by reference:

```python
class TailoredEntry(BaseModel):
    entry_index: int      # which base entry, by original position
    bullets: list[str]    # reworded bullets, in the tailored wording

class TailoredCV(BaseModel):
    summary: str
    skills: list[str]
    experience: list[TailoredEntry]   # in tailored (relevance) order
    projects: list[TailoredEntry]     # in tailored order; bullets empty for bullet-less projects
```

The prompt keeps the Phase 1 rules (evidence matrix, defensible-in-an-interview, no
invented tech/metrics, no verbatim mirroring, no hype). Claude reorders entries and rewords
bullets; it never emits anchors. The pipeline pairs each returned `entry_index` with the
original anchor from `resume.py` when writing.

## Truthfulness guards

Two mechanical guards; a CV failing either is dropped and logged, never written.

1. **Invented skills** (unchanged) — any tailored skill absent from `base_cv.md` drops the CV.
2. **Entry coverage** (new) — every Work Experience and Project entry must be referenced by
   exactly one `entry_index`, with no duplicates and no out-of-range indices. This stops a
   real job or project silently disappearing from, or being duplicated in, the resume.

Honest limit: reworded *bullet prose* is still governed by the prompt plus human review, as
in Phase 1. The guards check structure and the skills list, not the truth of free text.

## Output: `main.py` `write_cv`

A complete resume written to `output/{Company}_{Role}.md`:

- A small **metadata block** at the very top — fit score, reason, and apply URL — then a
  `---` rule. Everything below the rule is a clean, sendable resume.
- The **preamble** (name, contact) verbatim.
- Every section in the **original file order**: static sections copied verbatim; tailored
  sections rendered from the `TailoredCV` — `## About Me` from `summary`, `## Skills` from
  the reordered `skills`, `## Work Experience` and `## Projects` as each entry's verbatim
  anchor followed by its tailored bullets, in Claude's chosen order.

## Testing

- `resume.py`: parse a fixture `base_cv.md` — correct section split, preamble captured,
  tailored/static classification, Work Experience anchors (two-line) and Project anchors
  parsed, static sections left whole. No network.
- `tailoring.py`: stubbed Claude returns a `TailoredCV`; assert reassembly pairs each
  `entry_index` with the right anchor and preserves order; assert the coverage guard flags
  a dropped entry, a duplicate, and an out-of-range index; existing invented-skills tests
  keep passing; model/params assertions unchanged.
- `main.py` `write_cv`: given a parsed resume + a `TailoredCV`, the output contains every
  section in order, static text verbatim, anchors verbatim, and the metadata block on top.

## Out of scope (unchanged from Phase 1)

No dedup, ranking, PDF/Canva, auto-apply. Same two-call structure, same model IDs, same
cost profile. Tailoring the project **tech line** (reordering technologies within it) is
not done — the tech line stays verbatim.
