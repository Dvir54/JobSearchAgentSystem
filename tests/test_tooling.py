import config
from tooling import build_resume_view, clean_jobs, prepare_resume, safe_filename

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


def _job(job_id="j-1"):
    return {"id": job_id, "company": "Acme", "title": "Backend Developer", "url": "https://example.com/j"}


def _score(fit=82, junior=True):
    return {"is_junior_friendly": junior, "fit_score": fit,
            "reason": "Strong match.", "match_kind": "direct"}


def test_safe_filename_includes_job_id():
    assert safe_filename("Acme", "Backend Developer", "abc123") == "Acme_Backend_Developer_abc123.md"


def test_safe_filename_falls_back_without_job_id():
    assert safe_filename("Acme", "Backend Developer") == "Acme_Backend_Developer.md"
    assert safe_filename("Acme", "Backend Developer", None) == "Acme_Backend_Developer.md"
    assert safe_filename("Acme", "Backend Developer", "") == "Acme_Backend_Developer.md"


def test_safe_filename_sanitises_job_id_like_company_and_title():
    # Same character-stripping as company/title: path separators are removed,
    # so the sanitised id cannot escape out_dir when joined onto it.
    name = safe_filename("Acme", "Backend Developer", "../../evil")
    assert "/" not in name and "\\" not in name
    assert name == "Acme_Backend_Developer_....evil.md"


def _tailored_ok():
    return {"summary": "Final-semester CS student with backend Python experience. "
                       "Seeking a junior backend role.",
            "skills": ["Python", "SQL"],
            "experience": [{"entry_index": 0, "bullets": ["Reworded."]}],
            "projects": [{"entry_index": 0, "bullets": []}]}


def test_prepare_rejects_below_threshold():
    out = prepare_resume(_job(), _score(fit=40), _tailored_ok())
    assert out["rejected"] is True
    assert out["edits"] == {}


def test_prepare_returns_slot_keyed_edits():
    out = prepare_resume(_job(), _score(), _tailored_ok())
    assert out["rejected"] is False
    assert out["edits"]["summary"] == _tailored_ok()["summary"]
    assert "\n" in out["edits"]["skills"]
    assert "experience.0.bullets" in out["edits"]


def test_prepare_rejects_a_summary_that_is_not_a_string():
    tailored = _tailored_ok()
    tailored["summary"] = ["not", "a", "paragraph"]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert "string" in out["reason"].lower()


def test_prepare_still_strips_invented_skills():
    tailored = _tailored_ok()
    tailored["skills"] = ["Python", "Kubernetes"]      # Kubernetes is not in base_cv
    out = prepare_resume(_job(), _score(), tailored)
    assert "Kubernetes" not in out["edits"]["skills"]
    assert any("Kubernetes" in c for c in out["corrections"])


def test_prepare_repairs_out_of_range_entry_index():
    # Real base_cv (used by the guards) has exactly 2 experience entries; index 9
    # doesn't exist, so repair_entry_coverage must drop it and re-add entries
    # [0] and [1] with their original bullets, and both must reach edits.
    tailored = _tailored_ok()
    tailored["experience"] = [{"entry_index": 9, "bullets": ["Reworded."]}]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is False
    assert any("out-of-range" in c for c in out["corrections"])
    assert "experience.0.bullets" in out["edits"]
    assert "experience.1.bullets" in out["edits"]


def test_prepare_accepts_skills_as_comma_separated_string():
    # get_resume's own view returns skills as a string; prepare_resume must accept
    # the exact format its sibling tool emits, not iterate it char-by-char.
    tailored = _tailored_ok()
    tailored["skills"] = "Python, Kubernetes"
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is False
    kubernetes_notes = [c for c in out["corrections"] if "Kubernetes" in c]
    assert kubernetes_notes and "P, y, t" not in kubernetes_notes[0]
    assert "removed unverified skills: Kubernetes" in kubernetes_notes[0]
    assert out["edits"]["skills"] == "Python"


def test_prepare_rejects_skills_of_unsupported_type():
    tailored = _tailored_ok()
    tailored["skills"] = 42
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert out["edits"] == {}
    assert out["corrections"] == []
    assert "int" in out["reason"]


def test_prepare_rejects_entry_missing_entry_index_key():
    tailored = _tailored_ok()
    tailored["experience"] = [{"index": 0, "bullets": ["Reworded."]}]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert "entry_index" in out["reason"]
    assert out["edits"] == {}
    assert out["corrections"] == []


def test_prepare_rejects_text_over_the_length_budget():
    tailored = _tailored_ok()
    tailored["experience"] = [{"entry_index": 0, "bullets": ["x" * 5000]}]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert "length" in out["reason"].lower() or "budget" in out["reason"].lower()


def test_prepare_writes_no_file():
    before = set(config.OUTPUT_DIR.iterdir()) if config.OUTPUT_DIR.exists() else set()
    prepare_resume(_job(), _score(), _tailored_ok())
    after = set(config.OUTPUT_DIR.iterdir()) if config.OUTPUT_DIR.exists() else set()
    assert before == after
