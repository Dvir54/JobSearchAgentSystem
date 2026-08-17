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


def _messy_design():
    """A CV with the awkward shapes real designs have: several contact blocks, a
    two-column split, a section title the labeller did not recognise, and a stray
    block sitting below the headings with nothing above it in its column."""
    def box(top, left, text, width=150.0):
        return {"top": top, "left": left, "width": width, "height": 20.0,
                "regions": [text]}

    elements = {
        "name":   box(40.0, 20.0, "Dana Levi"),
        "title":  box(60.0, 20.0, "Computer Science Student"),
        "phone":  box(75.0, 20.0, "+972-50-0000000"),
        "mail":   box(90.0, 20.0, "dana@example.com"),
        "link":   box(105.0, 20.0, "github.com/danalevi"),
        "sum":    box(150.0, 400.0, "A summary paragraph."),
        "wh":     box(200.0, 400.0, "Work Experience"),
        "t0":     box(220.0, 400.0, "Backend Developer | Acme"),
        "d0":     box(240.0, 400.0, "2024 - now"),
        "b0":     box(260.0, 400.0, "Built REST APIs"),
        "skh":    box(300.0, 20.0, "Skills"),
        "sk":     box(320.0, 20.0, "Python, SQL"),
        "pubh":   box(400.0, 400.0, "Publications"),
        "pub":    box(420.0, 400.0, "A paper about things, 2025"),
        # nothing above it in its own column, and below the first heading
        "stray":  box(500.0, 700.0, "Available on request", width=120.0),
    }
    labels = {"name": "name", "title": "other", "phone": "contact",
              "mail": "contact", "link": "contact",
              "sum": "summary", "wh": "heading",
              "t0": "experience.0.title", "d0": "experience.0.dates",
              "b0": "experience.0.bullets",
              "skh": "heading", "sk": "skills",
              "pubh": "heading", "pub": "other", "stray": "other"}
    return labels, elements


def test_nothing_in_the_design_is_dropped():
    """The contract: base_cv.md holds everything the Canva CV holds, however it
    is laid out. Losing a block is invisible -- you cannot review text that is
    not there -- so this is asserted rather than hoped for."""
    labels, elements = _messy_design()
    rendered = render_base_cv(discover.build_resume(labels, elements))
    assert discover.coverage_gaps(elements, rendered) == []


def test_every_contact_block_survives_not_just_the_first():
    labels, elements = _messy_design()
    rendered = render_base_cv(discover.build_resume(labels, elements))
    for expected in ("+972-50-0000000", "dana@example.com", "github.com/danalevi",
                     "Computer Science Student"):
        assert expected in rendered


def test_an_unrecognised_section_keeps_its_own_name():
    labels, elements = _messy_design()
    parsed = discover.build_resume(labels, elements)
    assert "A paper about things, 2025" in parsed.get("Publications").body


def test_a_block_with_no_owning_heading_is_kept_not_binned():
    labels, elements = _messy_design()
    parsed = discover.build_resume(labels, elements)
    names = [s.name for s in parsed.sections]
    assert "Additional" in names
    assert "Available on request" in parsed.get("Additional").body


def test_coverage_gaps_names_a_block_that_went_missing():
    # The assertion must be able to fail, or it proves nothing.
    _labels, elements = _messy_design()
    assert "mail" in discover.coverage_gaps(elements, "nothing here")


def _real_geometry():
    """A slice of a real two-column design, with measured coordinates.

    Left column runs x 5-292, right column x 309-789. The three blocks that used
    to fall through are all here: a job title in the left column below the right
    column's first heading, and two narrow date boxes in the right column that
    overlap no heading box at all.
    """
    def box(top, left, width, text):
        return {"top": top, "left": left, "width": width, "height": 20.0,
                "regions": [text]}

    elements = {
        "name":   box(37.0, 4.7, 287.1, "DVIR ITSHACOV"),
        "abouth": box(64.2, 337.6, 205.8, "About Me"),
        "title":  box(89.2, 10.3, 281.5, "Computer Science Student"),
        "sum":    box(119.3, 314.8, 470.4, "A summary paragraph"),
        "conth":  box(146.2, 42.5, 105.8, "Contact"),
        "phone":  box(195.1, 42.5, 169.1, "+972-52-3867741"),
        "wexph":  box(229.6, 339.8, 205.8, "Work Experience"),
        "t0":     box(283.6, 315.9, 344.0, "Dev | Acme"),
        "d0":     box(285.3, 666.3, 122.7, "Mar 2026 - present"),
        "b0":     box(298.1, 309.4, 418.3, "Built a pipeline"),
        "skh":    box(360.1, 42.5, 94.2, "Skills"),
        "sk":     box(403.9, 4.7, 178.2, "Java"),
        "eduh":   box(844.0, 342.3, 205.8, "Education"),
        "deg":    box(909.8, 311.8, 231.5, "B.Sc. in Computer Science"),
        "degy":   box(911.5, 681.0, 85.7, "2023 - 2027"),
    }
    labels = {"name": "name", "abouth": "heading", "title": "other",
              "sum": "summary", "conth": "heading", "phone": "contact",
              "wexph": "heading", "t0": "experience.0.title",
              "d0": "experience.0.dates", "b0": "experience.0.bullets",
              "skh": "heading", "sk": "skills",
              "eduh": "heading", "deg": "other", "degy": "other"}
    return labels, elements


def test_a_narrow_date_box_joins_its_column_s_section():
    """The date sits at x 681-767 while the Education heading spans only 342-548,
    so they share no overlap — but both are in the right-hand column. Ownership
    is decided by column, not by overlapping the heading's own box."""
    labels, elements = _real_geometry()
    parsed = discover.build_resume(labels, elements)
    assert "2023 - 2027" in parsed.get("Education").body


def test_header_material_in_the_other_column_reaches_the_preamble():
    """The job title sits below the right column's first heading but above every
    heading in its own column, which makes it header material."""
    labels, elements = _real_geometry()
    parsed = discover.build_resume(labels, elements)
    assert "Computer Science Student" in parsed.preamble


def test_the_real_layout_needs_no_additional_bin():
    labels, elements = _real_geometry()
    parsed = discover.build_resume(labels, elements)
    assert parsed.get("Additional") is None
    rendered = render_base_cv(parsed)
    assert discover.coverage_gaps(elements, rendered) == []


def test_columns_are_found_by_transitive_overlap():
    labels, elements = _real_geometry()
    columns = discover.columns(elements)
    assert columns["name"] == columns["title"] == columns["sk"]
    assert columns["sum"] == columns["degy"] == columns["eduh"]
    assert columns["name"] != columns["sum"]


def _with_projects():
    labels, elements = _labelled()
    elements.update({
        "ph":  _el(600.0, 40.0, "Projects"),
        "p0":  _el(620.0, 40.0, "Crypto Advisor"),
        "p0t": _el(640.0, 40.0, "Python, FastAPI, PostgreSQL"),
        "p1":  _el(660.0, 40.0, "Robotic Vacuum Simulation"),
        "p1t": _el(680.0, 40.0, "Java, Multi-threading"),
    })
    labels.update({"ph": "heading",
                   "p0": "project.0.title", "p0t": "project.0.tech",
                   "p1": "project.1.title", "p1t": "project.1.tech"})
    return labels, elements


def test_projects_become_entries_not_loose_paragraphs():
    labels, elements = _with_projects()
    parsed = discover.build_resume(labels, elements)
    entries = parsed.get("Projects").entries
    assert len(entries) == 2
    assert entries[0].anchor.startswith("### Crypto Advisor")
    assert "Python, FastAPI, PostgreSQL" in entries[0].anchor
    assert entries[1].anchor.startswith("### Robotic Vacuum Simulation")


def test_the_agent_actually_sees_the_projects():
    """The point of the exercise. Loose paragraphs under a Projects heading are
    in the file but invisible to the model, which drafts without knowing what the
    candidate has built."""
    from jobsearch.agent.tooling import build_resume_view

    labels, elements = _with_projects()
    view = build_resume_view(render_base_cv(discover.build_resume(labels, elements)))
    assert len(view["projects"]) == 2
    assert "Crypto Advisor" in view["projects"][0]["anchor"]


def test_projects_are_still_captured_in_full():
    labels, elements = _with_projects()
    rendered = render_base_cv(discover.build_resume(labels, elements))
    assert discover.coverage_gaps(elements, rendered) == []


def test_no_projects_section_when_a_cv_has_none():
    labels, elements = _labelled()
    assert discover.build_resume(labels, elements).get("Projects") is None


def test_project_labels_are_part_of_the_vocabulary():
    labels, elements = _with_projects()
    assert discover.structural_problems(labels, elements) == []
