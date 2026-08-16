"""Turning any user's Canva design into something the agent can tailor."""
import json

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


def test_the_prompt_lists_every_block_with_its_text_and_position():
    _, elements = _labelled()
    prompt = discover.labelling_prompt(elements)
    assert "Dana Levi" in prompt and "Built REST APIs" in prompt
    # Position is evidence: a block under a heading is likely part of that section.
    assert "top" in prompt.lower()
    for element_id in elements:
        assert element_id in prompt


def test_parse_labelling_reads_a_json_reply():
    _, elements = _labelled()
    reply = json.dumps({"sum": "summary", "sk": "skills",
                        "b0": "experience.0.bullets"})
    labels = discover.parse_labelling(reply, elements)
    assert labels["sum"] == "summary"
    # Anything the reply omits is locked rather than guessed at.
    assert labels["vol"] == "other"


def test_parse_labelling_survives_prose_around_the_json():
    _, elements = _labelled()
    reply = 'Here is the mapping:\n```json\n{"sum": "summary"}\n```\nHope that helps.'
    assert discover.parse_labelling(reply, elements)["sum"] == "summary"


def test_parse_labelling_ignores_ids_that_are_not_in_the_design():
    _, elements = _labelled()
    reply = json.dumps({"sum": "summary", "ghost": "skills"})
    assert "ghost" not in discover.parse_labelling(reply, elements)


def test_parse_labelling_degrades_to_other_on_unusable_output():
    """The failure mode of this step has to be "did not tailor", never
    "tailored the wrong box"."""
    _, elements = _labelled()
    for reply in ("", "no json here", "{not valid json}"):
        labels = discover.parse_labelling(reply, elements)
        assert set(labels.values()) == {"other"}


def _labelled_with_extras():
    labels, elements = _labelled()
    elements.update({
        "ph": _el(480.0, 40.0, "Projects"),
        "p0": _el(500.0, 40.0, "Crypto Advisor"),
        "p0t": _el(510.0, 40.0, "Python, FastAPI, React"),
        "eh": _el(600.0, 40.0, "Education"),
        "e0": _el(620.0, 40.0, "B.Sc. Computer Science, 2023 - 2027"),
        "wh": _el(190.0, 40.0, "Work Experience"),
    })
    labels.update({"ph": "heading", "p0": "other", "p0t": "other",
                   "eh": "heading", "e0": "other", "wh": "heading"})
    del labels["vol"]
    labels["vol"] = "other"
    return labels, elements


def test_heading_is_part_of_the_vocabulary():
    labels, elements = _labelled_with_extras()
    assert discover.structural_problems(labels, elements) == []


def test_sections_the_agent_never_edits_are_kept():
    """The generated CV is what the agent reads when drafting, so dropping a
    candidate's projects would quietly cost them context they actually have."""
    labels, elements = _labelled_with_extras()
    parsed = discover.build_resume(labels, elements)
    names = [s.name for s in parsed.sections]
    assert "Projects" in names and "Education" in names
    projects = parsed.get("Projects")
    assert "Crypto Advisor" in projects.body
    assert "Python, FastAPI, React" in projects.body


def test_the_three_canonical_sections_come_first_and_are_not_duplicated():
    labels, elements = _labelled_with_extras()
    names = [s.name for s in discover.build_resume(labels, elements).sections]
    assert names[:3] == ["About Me", "Work Experience", "Skills"]
    # "Work Experience" is a heading in the design too; it must not appear twice
    assert names.count("Work Experience") == 1


def test_the_generated_cv_is_stable_once_written():
    """Not equality with the built object — parse_resume fills in a section body
    even where entries carry the content, which build_resume leaves empty. What
    matters is that writing and re-reading the file settles: the guards read this
    back every run and must see the same CV each time."""
    labels, elements = _labelled_with_extras()
    once = parse_resume(render_base_cv(discover.build_resume(labels, elements)))
    twice = parse_resume(render_base_cv(once))
    assert once == twice
    assert "Crypto Advisor" in once.get("Projects").body
    assert once.get("Work Experience").entries[0].bullets == [
        "Built REST APIs", "Cut p95 latency by 40%"]


def test_a_heading_does_not_claim_blocks_from_another_column():
    """Two-column CVs are common: a sidebar of Skills and Languages beside a main
    column of Experience and Projects. Ordering by vertical position alone
    interleaves them, so a sidebar heading would swallow the main column's
    content and file a candidate's projects under Volunteering."""
    def col(top, left, text):
        return {"top": top, "left": left, "width": 150.0, "height": 20.0,
                "regions": [text]}

    elements = {
        "sum": col(100.0, 400.0, "A summary"),
        "sk": col(150.0, 20.0, "Python"),
        "b0": col(200.0, 400.0, "Did a thing"),
        # left column heading, sitting ABOVE a right column block
        "lh": col(300.0, 20.0, "Languages"),
        "lang": col(340.0, 20.0, "Hebrew - Native"),
        # right column heading and its content, lower down the page
        "ph": col(320.0, 400.0, "Projects"),
        "proj": col(360.0, 400.0, "Crypto Advisor"),
    }
    labels = {"sum": "summary", "sk": "skills", "b0": "experience.0.bullets",
              "lh": "heading", "lang": "other", "ph": "heading", "proj": "other"}

    parsed = discover.build_resume(labels, elements)
    assert "Crypto Advisor" in parsed.get("Projects").body
    assert "Crypto Advisor" not in parsed.get("Languages").body
    assert parsed.get("Languages").body == "Hebrew - Native"
