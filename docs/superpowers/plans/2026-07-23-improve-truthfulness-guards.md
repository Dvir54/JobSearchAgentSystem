# Repair-Not-Drop Truthfulness Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the two truthfulness guards from "detect a problem → drop the whole résumé" to "repair the one bad part → keep the résumé → log what changed," and make the invented-skills check alias- and word-boundary-aware so it stops both false positives (JS vs JavaScript) and false negatives (React inside reactive).

**Architecture:** `tailoring.py` gains alias-aware skill matching plus two new functions — `strip_invented_skills` (removes only the untrue skills) and `repair_entry_coverage` (dedupes/drops-invalid/re-adds-missing entries from the base CV). `render.py` grows an optional notes line so corrections are visible in the résumé. `main.py` replaces its drop-and-continue with strip + repair + write, logging every correction. Scoring, jobs, resume, and config are untouched.

**Tech Stack:** Python 3.11+, `pydantic` (v2 `model_copy`), `re`, `pytest`. No new dependencies.

**Design source:** the guard discussion in this conversation (alias-check → delete only the invented skill; reconstruct a missing job from the base CV; keep the résumé; log it).

## Global Constraints

- Python interpreter: `.venv/Scripts/python.exe`. Tests import flat module names (`pyproject.toml` sets `pythonpath = ["src"]`).
- **The guards must never discard a résumé.** Both always produce a writable `TailoredCV`; corrections are logged, never fatal.
- Skill presence is **alias-aware and word-boundary-aware**: a tailored skill counts as "in the CV" if its canonical form (after resolving shortcuts like JS→JavaScript, applied to both sides) appears as a whole word/phrase in the CV. Word boundaries stop `React` matching inside `reactive`; symbol-heavy skills (C++, C#, .NET, node.js) fall back to a left-anchored match so they still match.
- **Reconstructed entries use the base CV's original bullets** (Claude never reworded them). Projects carry no bullets.
- Correction notes appear **in the résumé's metadata block, above the `---`**, and on the run's stdout log.
- No network in tests; every new function is pure and tested directly. Claude is not called by any of this work — `tailor_cv` and its prompt/model/params are unchanged.
- Do not modify `scoring.py`, `jobs.py`, `monid.py`, `resume.py`, or `config.py`.
- Commit directly on branch `improve-guards`; no worktrees.

## File Structure

| File | Change |
|---|---|
| `src/tailoring.py` | Add `SKILL_ALIASES`, `_canonicalize`, `_skill_in_cv`; make `find_invented_skills` alias/boundary-aware; add `strip_invented_skills`; replace `find_entry_coverage_errors` with `repair_entry_coverage` (+ `_repair_section`). |
| `src/render.py` | `render_output` gains an optional `notes` parameter → a review line in the metadata block. |
| `src/main.py` | Loop: strip + repair + write (no drop); `write_cv` passes notes through; imports updated. |
| `tests/test_tailoring.py` | Update invented-skill tests (alias/boundary), add `strip_invented_skills` tests, replace coverage-error tests with `repair_entry_coverage` tests. |
| `tests/test_render.py` | Add tests for the notes line (present and absent). |
| `tests/test_guards.py` | **New.** End-to-end: a deliberately dirty `TailoredCV` → strip → repair → render, asserting a complete, truthful résumé with a review note. |

---

## Task 1: Alias-aware invented-skill detection + strip (not drop)

**Files:**
- Modify: `src/tailoring.py`
- Test: `tests/test_tailoring.py`

**Interfaces:**
- Produces:
  - `find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]` (now alias/boundary-aware).
  - `strip_invented_skills(tailored: TailoredCV, base_cv: str) -> tuple[TailoredCV, list[str]]` — returns the cleaned CV and the removed skills.

- [ ] **Step 1: Replace the invented-skill tests in `tests/test_tailoring.py`**

Update the import line to add `strip_invented_skills` (leave `find_entry_coverage_errors` for now — Task 2 removes it):

```python
from tailoring import (
    TailoredCV,
    TailoredEntry,
    build_tailoring_prompt,
    find_entry_coverage_errors,
    find_invented_skills,
    strip_invented_skills,
    tailor_cv,
)
```

Replace the two existing `test_find_invented_skills_*` tests with these (keep everything else in the file):

```python
def _cv(skills_line: str) -> str:
    return f"# C\n\n## Skills\n\n{skills_line}\n"


def test_find_invented_skills_flags_absent_technology():
    tailored = TailoredCV(summary="s", skills=["Python", "Kubernetes"],
                          experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("Python, SQL, Docker")) == ["Kubernetes"]


def test_find_invented_skills_accepts_present_skills_case_insensitively():
    tailored = TailoredCV(summary="s", skills=["python", "DOCKER"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("Python, SQL, Docker")) == []


def test_find_invented_skills_accepts_alias_shortcuts():
    # "JS" is not literally in the CV, but it is JavaScript, which is.
    tailored = TailoredCV(summary="s", skills=["JS", "Postgres"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("JavaScript, PostgreSQL")) == []


def test_find_invented_skills_uses_word_boundaries():
    # "React" must NOT be accepted just because the CV says "reactive".
    tailored = TailoredCV(summary="s", skills=["React"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("reactive programming")) == ["React"]


def test_find_invented_skills_matches_symbol_skills():
    tailored = TailoredCV(summary="s", skills=["C++"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("C++, Python")) == []


def test_strip_invented_skills_removes_only_invented_and_reports_them():
    tailored = TailoredCV(summary="s", skills=["Python", "Kubernetes", "Docker"],
                          experience=[], projects=[])
    cleaned, removed = strip_invented_skills(tailored, _cv("Python, Docker"))
    assert cleaned.skills == ["Python", "Docker"]
    assert removed == ["Kubernetes"]


def test_strip_invented_skills_noop_when_all_present():
    tailored = TailoredCV(summary="s", skills=["Python"], experience=[], projects=[])
    cleaned, removed = strip_invented_skills(tailored, _cv("Python"))
    assert removed == []
    assert cleaned.skills == ["Python"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: FAIL — `ImportError` for `strip_invented_skills`, and the alias/boundary tests fail against the old substring matcher.

- [ ] **Step 3: Update `find_invented_skills` and add `strip_invented_skills` in `src/tailoring.py`**

Add `import re` at the top (after the module docstring, with the other imports). Add the alias map and helpers, then replace `find_invented_skills`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: all pass (the coverage tests still pass — `find_entry_coverage_errors` is untouched here).

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass. (`main.py` still imports the old functions and is untouched.)

- [ ] **Step 6: Commit**

```bash
git add src/tailoring.py tests/test_tailoring.py
git commit -m "feat: alias-aware invented-skill detection + strip instead of drop"
```

---

## Task 2: Entry-coverage repair (not drop)

Replaces `find_entry_coverage_errors` with `repair_entry_coverage`. After this task `main.py` is temporarily broken (it still imports `find_entry_coverage_errors`) — expected; fixed in Task 4. No test imports `main.py`, so the suite stays green.

**Files:**
- Modify: `src/tailoring.py`
- Test: `tests/test_tailoring.py`

**Interfaces:**
- Produces: `repair_entry_coverage(tailored: TailoredCV, parsed: ParsedResume) -> tuple[TailoredCV, list[str]]` — a coverage-valid CV plus human-readable repair notes.
- Removes: `find_entry_coverage_errors`.

- [ ] **Step 1: Swap the coverage tests in `tests/test_tailoring.py`**

Change the import: remove `find_entry_coverage_errors`, add `repair_entry_coverage`:

```python
from tailoring import (
    TailoredCV,
    TailoredEntry,
    build_tailoring_prompt,
    find_invented_skills,
    repair_entry_coverage,
    strip_invented_skills,
    tailor_cv,
)
```

Delete the three `test_entry_coverage_*` tests and add these (the `BASE_MD`/`PARSED` fixture at the top of the file already has two Work Experience entries — [0] Acme with bullet "Built APIs in Python.", [1] Beta with bullet "Wrote scripts." — and one Project [0] Todo App):

```python
def test_repair_leaves_valid_coverage_untouched():
    valid = _valid_tailored()
    repaired, notes = repair_entry_coverage(valid, PARSED)
    assert notes == []
    assert repaired == valid


def test_repair_readds_missing_experience_entry_with_original_bullets():
    # Claude returned only entry [0]; entry [1] (Intern | Beta) was dropped.
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["reworded"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    repaired, notes = repair_entry_coverage(tailored, PARSED)
    indices = [e.entry_index for e in repaired.experience]
    assert sorted(indices) == [0, 1]
    readded = next(e for e in repaired.experience if e.entry_index == 1)
    assert readded.bullets == ["Wrote scripts."]  # original base bullets, not invented
    assert any("re-added" in n and "[1]" in n for n in notes)


def test_repair_drops_duplicate_and_out_of_range():
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["a"]),
                                      TailoredEntry(entry_index=0, bullets=["dup"])],
                          projects=[TailoredEntry(entry_index=5, bullets=[])])
    repaired, notes = repair_entry_coverage(tailored, PARSED)
    exp_indices = [e.entry_index for e in repaired.experience]
    assert exp_indices == [0, 1]                       # dup removed, missing [1] re-added
    assert repaired.experience[0].bullets == ["a"]     # first occurrence kept
    proj_indices = [e.entry_index for e in repaired.projects]
    assert proj_indices == [0]                          # out-of-range [5] removed, [0] re-added
    assert any("duplicate" in n for n in notes)
    assert any("out-of-range" in n for n in notes)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: FAIL — `ImportError` for `repair_entry_coverage`.

- [ ] **Step 3: Replace `find_entry_coverage_errors` with `repair_entry_coverage` in `src/tailoring.py`**

Delete `find_entry_coverage_errors` entirely and add:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: all pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass. (`main.py` now references a removed name but no test imports it; Task 4 repairs it.)

- [ ] **Step 6: Commit**

```bash
git add src/tailoring.py tests/test_tailoring.py
git commit -m "feat: repair entry coverage from base CV instead of dropping"
```

---

## Task 3: Render the correction notes

**Files:**
- Modify: `src/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Changes: `render_output(posting, score, parsed, tailored, notes=None) -> str` — when `notes` is non-empty, a review line is added to the metadata block above the `---`.

- [ ] **Step 1: Add failing tests to `tests/test_render.py`**

Append (the file already builds a `FakePosting`, `FakeScore`, `PARSED`, and `_tailored()` — reuse them):

```python
def test_notes_appear_in_metadata_above_the_rule():
    out = render_output(FakePosting(), FakeScore(), PARSED, _tailored(),
                        notes=["removed unverified skills: Kubernetes"])
    assert "removed unverified skills: Kubernetes" in out
    assert out.index("removed unverified skills") < out.index("---")


def test_no_notes_line_when_notes_empty():
    out = render_output(FakePosting(), FakeScore(), PARSED, _tailored())
    assert "Review" not in out and "Auto-corrected" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: FAIL — `render_output` takes no `notes` argument.

- [ ] **Step 3: Update `render_output` in `src/render.py`**

Replace `render_output` with:

```python
def render_output(posting, score, parsed: ParsedResume, tailored: TailoredCV, notes=None) -> str:
    lines = [
        f"- **Fit:** {score.fit_score}/100 — {score.reason}",
        f"- **Match:** {_MATCH_LABEL.get(score.match_kind, score.match_kind)}",
        f"- **Apply at:** {posting.url}",
    ]
    if notes:
        lines.append(f"- **⚠️ Auto-corrected:** {'; '.join(notes)} — review before sending.")
    lines += ["", "---", ""]
    return "\n".join(lines) + _render_resume(parsed, tailored) + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: all pass (existing render tests unaffected — `notes` defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/render.py tests/test_render.py
git commit -m "feat: surface guard corrections in the resume metadata block"
```

---

## Task 4: Wire the pipeline — repair, don't drop

**Files:**
- Modify: `src/main.py`
- Test: none new; full suite + dry run verify it.

- [ ] **Step 1: Update the tailoring import in `main.py`**

Replace:
```python
from tailoring import find_entry_coverage_errors, find_invented_skills, tailor_cv
```
with:
```python
from tailoring import repair_entry_coverage, strip_invented_skills, tailor_cv
```

- [ ] **Step 2: Update `write_cv` in `main.py` to pass notes**

Replace the `write_cv` function with:
```python
def write_cv(posting, score, tailored, parsed, out_dir, notes=None):
    """Write one complete tailored resume: metadata block (with any correction
    notes) above the resume."""
    content = render_output(posting, score, parsed, tailored, notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text(content, encoding="utf-8")
    return path
```

- [ ] **Step 3: Replace the tailor/guard/write block in `main()`**

Replace this block:
```python
        # 3. Tailor.
        tailored = tailor_cv(claude, posting, parsed)

        problems = find_invented_skills(tailored, base_cv) + find_entry_coverage_errors(tailored, parsed)
        if problems:
            print(f"  DROP  {posting.company}: {'; '.join(problems)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, parsed, config.OUTPUT_DIR)
        written += 1
        print(f"  WRITE [{score.fit_score:3}] ({score.match_kind:7}) {path.name}")
```
with:
```python
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
```

- [ ] **Step 4: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass across jobs, monid, scoring, resume, tailoring, render.

- [ ] **Step 5: Dry run (no spend)**

Run: `.venv/Scripts/python.exe src/main.py --dry-run`
Expected: prints the projected cost and harvestapi input JSON, no import errors, spends nothing. (This confirms `main.py` imports resolve after the guard-function swap.)

- [ ] **Step 6: Commit**

```bash
git add src/main.py
git commit -m "feat: pipeline repairs resumes instead of dropping them"
```

---

## Task 5: End-to-end guard integration test (no spend)

The guards fire rarely on real runs (zero times in the last live run), so a deterministic dirty-input test is the right verification — no Claude, no Monid.

**Files:**
- Create: `tests/test_guards.py`

- [ ] **Step 1: Write the integration test**

`tests/test_guards.py`:
```python
from render import render_output
from resume import parse_resume
from tailoring import TailoredCV, TailoredEntry, repair_entry_coverage, strip_invented_skills

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


class FakePosting:
    url = "https://example.com/job"


class FakeScore:
    fit_score = 78
    reason = "Good fit."
    match_kind = "stretch"


def test_dirty_cv_is_repaired_kept_and_annotated():
    # Invented skill (Kubernetes), a dropped job (entry [1]), a bad project index.
    dirty = TailoredCV(
        summary="Backend developer.",
        skills=["Python", "Kubernetes"],
        experience=[TailoredEntry(entry_index=0, bullets=["Reworded Acme bullet."])],
        projects=[TailoredEntry(entry_index=9, bullets=[])],
    )

    cleaned, removed = strip_invented_skills(dirty, BASE_MD)
    repaired, coverage_notes = repair_entry_coverage(cleaned, PARSED)
    notes = ([f"removed unverified skills: {', '.join(removed)}"] if removed else []) + coverage_notes

    out = render_output(FakePosting(), FakeScore(), PARSED, repaired, notes)

    # Nothing was dropped — the résumé exists and is complete.
    assert "### Backend Developer | Acme" in out
    assert "### Intern | Beta" in out          # the dropped job was rebuilt
    assert "Wrote scripts." in out             # with its original bullets
    assert "### Todo App" in out               # the bad project index was repaired
    # The invented skill is gone from the skills list.
    assert "Kubernetes" not in out
    assert "Python" in out
    # The correction is surfaced for review, above the résumé.
    assert "Auto-corrected" in out
    assert out.index("Auto-corrected") < out.index("# Cand")
    assert "removed unverified skills: Kubernetes" in out
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_guards.py -v`
Expected: 1 passed.

- [ ] **Step 3: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_guards.py
git commit -m "test: end-to-end guard repair keeps and annotates a dirty resume"
```

**The guards now repair and keep résumés instead of dropping them, and every correction is visible in the output and the run log.**

---

## Self-Review

- **Spec coverage:** alias-aware + word-boundary matching (Task 1, `_canonicalize`/`_skill_in_cv`, with alias, boundary, and symbol tests) ✓; strip-not-drop for invented skills (Task 1, `strip_invented_skills`) ✓; repair-from-base for coverage incl. dropped/duplicate/out-of-range (Task 2, `repair_entry_coverage`/`_repair_section`) ✓; corrections visible in the résumé (Task 3) and the run log (Task 4) ✓; no résumé is ever dropped (Task 4 removes the `continue`) ✓; deterministic end-to-end verification without spend (Task 5) ✓.
- **Placeholder scan:** every step has concrete code/commands; no TBD/TODO/"handle errors" placeholders.
- **Type consistency:** `strip_invented_skills(tailored, base_cv) -> (TailoredCV, list[str])` and `repair_entry_coverage(tailored, parsed) -> (TailoredCV, list[str])` are used identically in `main.py`, `tests/test_tailoring.py`, and `tests/test_guards.py`; `render_output(..., notes=None)` matches its call in `write_cv`; `TailoredEntry(entry_index, bullets)` and `TailoredCV.model_copy(update=...)` match the pydantic models in `tailoring.py`; `find_entry_coverage_errors` is removed in Task 2 and its last consumer (`main.py`) is updated in Task 4.
