import json
from pathlib import Path

from tooling import build_resume_view, clean_jobs, write_tailored_resume

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


def _raw(job_id, location, title="Developer"):
    return {"id": job_id, "title": title, "company": {"name": "Acme"},
            "descriptionText": "desc", "linkedinUrl": "https://x", "postedDate": None,
            "location": {"linkedinText": location}}


def test_build_resume_view_exposes_indexed_entries():
    view = build_resume_view(BASE_MD)
    assert view["skills"].strip() == "Python, SQL, Docker"
    exp = view["experience"]
    assert [e["index"] for e in exp] == [0, 1]
    assert exp[0]["anchor"].startswith("### Backend Developer | Acme")
    assert exp[0]["bullets"] == ["Built APIs in Python."]
    assert [p["index"] for p in view["projects"]] == [0]


def test_clean_jobs_normalizes_dedups_and_filters_israel():
    raws = [_raw("1", "Tel Aviv, Israel"), _raw("1", "Tel Aviv, Israel"), _raw("2", "EMEA")]
    jobs = clean_jobs(raws)
    assert [j["id"] for j in jobs] == ["1"]
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["url"].startswith("http")
    assert "israel" in jobs[0]["location"].lower()


def test_clean_jobs_empty_input_returns_empty():
    assert clean_jobs([]) == []


def _job():
    return {"company": "Acme", "title": "Backend Developer", "url": "https://example.com/j"}


def _score(fit=82, junior=True):
    return {"is_junior_friendly": junior, "fit_score": fit,
            "reason": "Strong match.", "match_kind": "direct"}


def _tailored():
    return {"summary": "Backend dev.", "skills": ["Python", "Kubernetes"],
            "experience": [{"entry_index": 0, "bullets": ["Reworded."]}],
            "projects": [{"entry_index": 9, "bullets": []}]}


def test_write_rejects_below_threshold(tmp_path):
    out = write_tailored_resume(_job(), _score(fit=40), _tailored(), out_dir=tmp_path)
    assert out["rejected"] is True and out["written"] is None
    assert list(tmp_path.iterdir()) == []


def test_write_gates_guards_and_reports_corrections(tmp_path):
    # base_cv used by the guards is the real config.BASE_CV_PATH; this test asserts
    # the enforcement path runs. Kubernetes is invented; entry [1] and the bad
    # project index must be repaired.
    out = write_tailored_resume(_job(), _score(), _tailored(), out_dir=tmp_path)
    assert out["rejected"] is False
    path = Path(out["written"])
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "Kubernetes" not in body.split("---", 1)[1]   # stripped from the résumé body
    assert any("Kubernetes" in c for c in out["corrections"])
    assert "Auto-corrected" in body                       # surfaced in the banner
