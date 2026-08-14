from jobsearch.resume.base_cv import parse_resume
from jobsearch.resume.tailoring import TailoredCV, TailoredEntry, repair_entry_coverage, strip_invented_skills

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

    # Nothing was dropped — both experience entries are referenced (the missing
    # job, index 1, was rebuilt rather than silently lost).
    exp_indices = {e.entry_index for e in repaired.experience}
    assert exp_indices == {0, 1}

    # The rebuilt entry carries its original bullets from the base CV.
    rebuilt = next(e for e in repaired.experience if e.entry_index == 1)
    assert "Wrote scripts." in rebuilt.bullets

    # The bad project index (9) was repaired to reference the one valid project (0).
    proj_indices = {p.entry_index for p in repaired.projects}
    assert 9 not in proj_indices
    assert proj_indices == {0}

    # The invented skill is gone; genuine skills remain.
    assert "Kubernetes" not in repaired.skills
    assert "Python" in repaired.skills

    # ...and the correction is reported for operator review.
    assert any("removed unverified skills: Kubernetes" in note for note in notes)
