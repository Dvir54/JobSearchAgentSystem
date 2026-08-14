from jobsearch.resume.base_cv import parse_resume
from jobsearch.resume.tailoring import (
    TailoredCV,
    TailoredEntry,
    find_invented_skills,
    repair_entry_coverage,
    strip_invented_skills,
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


def _valid_tailored() -> TailoredCV:
    return TailoredCV(
        summary="s",
        skills=["Python", "Docker"],
        experience=[TailoredEntry(entry_index=1, bullets=["b"]),
                    TailoredEntry(entry_index=0, bullets=["b"])],
        projects=[TailoredEntry(entry_index=0, bullets=[])],
    )


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


def test_find_invented_skills_accepts_node_js_symbol_alias():
    tailored = TailoredCV(summary="s", skills=["Node.js"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("Node, Python")) == []


def test_find_invented_skills_accepts_reverse_alias_direction():
    # CV uses the shorthand; the tailored skill uses the canonical form.
    tailored = TailoredCV(summary="s", skills=["JavaScript"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("JS, Python")) == []


def test_find_invented_skills_matches_dotnet_and_csharp():
    tailored = TailoredCV(summary="s", skills=["C#", ".NET"], experience=[], projects=[])
    assert find_invented_skills(tailored, _cv("C#, .NET, Python")) == []


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


def test_repair_rebuilds_all_missing_experience():
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[], projects=[TailoredEntry(entry_index=0, bullets=[])])
    repaired, notes = repair_entry_coverage(tailored, PARSED)
    assert [e.entry_index for e in repaired.experience] == [0, 1]
    assert repaired.experience[0].bullets == ["Built APIs in Python."]
    assert repaired.experience[1].bullets == ["Wrote scripts."]
    assert sum("re-added" in n for n in notes) >= 2


def test_repair_collapses_all_duplicates():
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["keep"]),
                                      TailoredEntry(entry_index=0, bullets=["dup"]),
                                      TailoredEntry(entry_index=0, bullets=["dup2"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    repaired, notes = repair_entry_coverage(tailored, PARSED)
    assert [e.entry_index for e in repaired.experience] == [0, 1]
    assert repaired.experience[0].bullets == ["keep"]            # first occurrence kept
    assert repaired.experience[1].bullets == ["Wrote scripts."]  # [1] rebuilt from base
    assert any("duplicate" in n for n in notes)


def test_repair_handles_empty_base_section():
    # A CV whose Work Experience section has no entries: a tailored experience
    # entry is out-of-range and dropped, and nothing is re-added.
    empty_exp_md = "# C\n\n## Work Experience\n\n## Projects\n\n### P\nTech\n\n## Skills\n\nPython\n"
    parsed = parse_resume(empty_exp_md)
    tailored = TailoredCV(summary="s", skills=["Python"],
                          experience=[TailoredEntry(entry_index=0, bullets=["x"])],
                          projects=[TailoredEntry(entry_index=0, bullets=[])])
    repaired, notes = repair_entry_coverage(tailored, parsed)
    assert repaired.experience == []
    assert any("out-of-range" in n for n in notes)
