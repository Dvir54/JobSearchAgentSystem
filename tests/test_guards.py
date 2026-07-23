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

    # The banner (above the '---') records corrections; the résumé body is below it.
    metadata, _, body = out.partition("---")

    # Nothing was dropped — the résumé body is complete.
    assert "### Backend Developer | Acme" in body
    assert "### Intern | Beta" in body          # the dropped job was rebuilt
    assert "Wrote scripts." in body             # with its original bullets
    assert "### Todo App" in body               # the bad project index was repaired
    assert "Python" in body

    # The invented skill is gone from the résumé body...
    assert "Kubernetes" not in body
    # ...but the correction is surfaced in the banner above the résumé for review.
    assert "Auto-corrected" in metadata
    assert "removed unverified skills: Kubernetes" in metadata
    assert out.index("Auto-corrected") < out.index("# Cand")
