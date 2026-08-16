"""Turning any user's Canva design into something the agent can tailor."""
from jobsearch.agent import discover
from jobsearch.resume.base_cv import parse_resume, render_base_cv


def _el(top, left, text):
    return {"top": top, "left": left, "width": 100.0, "height": 20.0,
            "regions": [text]}


def _design():
    return {
        "a": _el(300.0, 40.0, "Built REST APIs"),
        "b": _el(100.0, 40.0, "Dana Levi"),
        "c": _el(200.0, 40.0, "Final-year CS student"),
    }


def test_reading_order_is_top_to_bottom():
    # Canva returns elements in creation order, which bears no relation to how
    # the page reads. Position is the only reliable ordering.
    order = [eid for eid, _ in discover.reading_order(_design())]
    assert order == ["b", "c", "a"]


def test_reading_order_breaks_ties_left_to_right():
    elements = {"right": _el(100.0, 400.0, "x"), "left": _el(100.0, 20.0, "y")}
    assert [eid for eid, _ in discover.reading_order(elements)] == ["left", "right"]


def test_a_complete_labelling_has_no_problems():
    labels = {"b": "name", "c": "summary", "a": "experience.0.bullets",
              "d": "skills"}
    elements = dict(_design(), d=_el(400.0, 40.0, "Python, SQL"))
    assert discover.structural_problems(labels, elements) == []


def test_two_blocks_labelled_skills_are_refused_as_chips():
    """Tailoring skills means reordering and trimming one list. Four separate
    boxes cannot be rewritten as a list."""
    labels = {"c": "summary", "a": "experience.0.bullets",
              "s1": "skills", "s2": "skills"}
    elements = dict(_design(), s1=_el(400.0, 10.0, "Java"),
                    s2=_el(400.0, 80.0, "Python"))
    problems = discover.structural_problems(labels, elements)
    assert any("separate boxes" in p for p in problems)


def test_two_blocks_labelled_the_same_entry_are_refused():
    labels = {"c": "summary", "d": "skills",
              "b1": "experience.0.bullets", "b2": "experience.0.bullets"}
    elements = {"c": _el(200.0, 40.0, "s"), "d": _el(500.0, 40.0, "Python"),
                "b1": _el(300.0, 40.0, "one"), "b2": _el(320.0, 40.0, "two")}
    problems = discover.structural_problems(labels, elements)
    assert any("separate text box" in p for p in problems)


def test_a_missing_summary_is_named():
    labels = {"d": "skills", "a": "experience.0.bullets"}
    elements = dict(_design(), d=_el(400.0, 40.0, "Python"))
    assert any("summary" in p for p in discover.structural_problems(labels, elements))


def test_no_experience_entry_is_named():
    labels = {"c": "summary", "d": "skills"}
    elements = dict(_design(), d=_el(400.0, 40.0, "Python"))
    assert any("experience" in p for p in discover.structural_problems(labels, elements))


def test_a_label_outside_the_vocabulary_is_refused():
    labels = {"c": "summary", "d": "skills", "a": "experience.0.bullets",
              "b": "publications"}
    elements = dict(_design(), d=_el(400.0, 40.0, "Python"))
    assert any("publications" in p
               for p in discover.structural_problems(labels, elements))


def _labelled():
    elements = {
        "name": _el(50.0, 40.0, "Dana Levi"),
        "mail": _el(70.0, 40.0, "dana@example.com"),
        "sum": _el(120.0, 40.0, "Final-year CS student with backend experience."),
        "t0": _el(200.0, 40.0, "Backend Developer | Acme"),
        "d0": _el(220.0, 40.0, "2024 - now"),
        "b0": _el(240.0, 40.0, "Built REST APIs\nCut p95 latency by 40%"),
        "t1": _el(300.0, 40.0, "Intern | Beta"),
        "d1": _el(320.0, 40.0, "2023"),
        "b1": _el(340.0, 40.0, "Wrote automation scripts"),
        "sk": _el(400.0, 40.0, "Python, SQL, Docker"),
        "vol": _el(500.0, 40.0, "Volunteering"),
    }
    labels = {"name": "name", "mail": "contact", "sum": "summary",
              "t0": "experience.0.title", "d0": "experience.0.dates",
              "b0": "experience.0.bullets",
              "t1": "experience.1.title", "d1": "experience.1.dates",
              "b1": "experience.1.bullets",
              "sk": "skills", "vol": "other"}
    return labels, elements


def test_build_profile_maps_only_editable_roles():
    labels, elements = _labelled()
    built = discover.build_profile(labels, elements, "DAG1", "PAGE", "My CV")
    assert set(built["slots"]) == {"summary", "skills",
                                   "experience.0.bullets", "experience.1.bullets"}
    assert built["slots"]["summary"] == "sum"
    assert built["design_id"] == "DAG1"


def test_everything_not_edited_is_locked():
    labels, elements = _labelled()
    built = discover.build_profile(labels, elements, "DAG1", "PAGE", "My CV")
    # Titles and dates are needed to write the CV but must never be rewritten.
    for element_id in ("name", "mail", "t0", "d0", "t1", "d1", "vol"):
        assert element_id in built["locked"]
    for element_id in built["slots"].values():
        assert element_id not in built["locked"]


def test_build_resume_produces_a_parseable_cv():
    labels, elements = _labelled()
    reparsed = parse_resume(render_base_cv(discover.build_resume(labels, elements)))
    assert reparsed.get("About Me").body.startswith("Final-year CS student")
    assert reparsed.get("Skills").body == "Python, SQL, Docker"
    entries = reparsed.get("Work Experience").entries
    assert len(entries) == 2
    assert entries[0].anchor.startswith("### Backend Developer | Acme")
    assert entries[1].bullets == ["Wrote automation scripts"]


def test_bullets_split_on_newlines_within_one_box():
    """One Canva text box holds all of a job's bullets, separated by line breaks.
    base_cv.md needs them as separate bullets so the one-to-one rewording rule
    has something to count."""
    labels, elements = _labelled()
    parsed = discover.build_resume(labels, elements)
    assert parsed.get("Work Experience").entries[0].bullets == [
        "Built REST APIs", "Cut p95 latency by 40%"]
