"""Extraction against CV layouts that are nothing like the author's.

Placement is deterministic, so it can be pinned against as many shapes as we can
think of. What these cannot prove is the labelling step — whether a model reading
an unfamiliar CV picks the right blocks. Only running it does that.

Each design below asserts the same two things: the editable slots land on the
right boxes, and NOT ONE block is dropped.
"""
from jobsearch.agent import discover
from jobsearch.resume.base_cv import parse_resume, render_base_cv


def _box(top, left, width, text):
    return {"top": top, "left": left, "width": width, "height": 20.0,
            "regions": [text]}


def _check(labels, elements, expect_slots, expect_sections=()):
    """Every design must place every block, and map the editable slots correctly."""
    profile = discover.build_profile(labels, elements, "D", "P", "T")
    assert profile["slots"] == expect_slots

    parsed = discover.build_resume(labels, elements)
    rendered = render_base_cv(parsed)
    assert discover.coverage_gaps(elements, rendered, labels) == [], (
        "a block was dropped")

    reparsed = parse_resume(rendered)
    for name in expect_sections:
        assert reparsed.get(name) is not None, f"missing section {name}"
    return reparsed


def test_single_column_cv():
    """One column, everything stacked. No sidebar at all."""
    elements = {
        "n":   _box(30.0, 50.0, 500.0, "Sam Okonkwo"),
        "c":   _box(55.0, 50.0, 500.0, "sam@example.com | +44 7700 900000"),
        "ah":  _box(100.0, 50.0, 500.0, "Profile"),
        "sum": _box(125.0, 50.0, 500.0, "Graduate developer seeking backend work."),
        "eh":  _box(200.0, 50.0, 500.0, "Employment History"),
        "t0":  _box(230.0, 50.0, 500.0, "Junior Developer, Northwind"),
        "d0":  _box(250.0, 50.0, 500.0, "2024 to date"),
        "b0":  _box(270.0, 50.0, 500.0, "Shipped a billing service\nCut build times"),
        "sh":  _box(400.0, 50.0, 500.0, "Technical Skills"),
        "sk":  _box(425.0, 50.0, 500.0, "Go, Postgres, Kubernetes"),
    }
    labels = {"n": "name", "c": "contact", "ah": "heading", "sum": "summary",
              "eh": "heading", "t0": "experience.0.title",
              "d0": "experience.0.dates", "b0": "experience.0.bullets",
              "sh": "heading", "sk": "skills"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"},
                    ("About Me", "Work Experience", "Skills"))
    assert parsed.get("Work Experience").entries[0].bullets == [
        "Shipped a billing service", "Cut build times"]


def test_headings_named_nothing_like_the_canonical_ones():
    """"Profile", "Career History", "Core Competencies" — the generated file uses
    canonical names regardless, so the parser and the guards keep one contract."""
    elements = {
        "ah":  _box(100.0, 50.0, 400.0, "Who I Am"),
        "sum": _box(120.0, 50.0, 400.0, "A summary."),
        "eh":  _box(200.0, 50.0, 400.0, "Career History"),
        "t0":  _box(220.0, 50.0, 400.0, "Engineer, Acme"),
        "b0":  _box(240.0, 50.0, 400.0, "Did the work"),
        "sh":  _box(300.0, 50.0, 400.0, "Core Competencies"),
        "sk":  _box(320.0, 50.0, 400.0, "Rust, SQL"),
    }
    labels = {"ah": "heading", "sum": "summary", "eh": "heading",
              "t0": "experience.0.title", "b0": "experience.0.bullets",
              "sh": "heading", "sk": "skills"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"},
                    ("About Me", "Work Experience", "Skills"))
    # the original wording is not what the guards read
    assert parsed.get("Who I Am") is None


def test_non_english_headings():
    elements = {
        "ah":  _box(100.0, 50.0, 400.0, "Perfil"),
        "sum": _box(120.0, 50.0, 400.0, "Desarrollador junior."),
        "eh":  _box(200.0, 50.0, 400.0, "Experiencia Laboral"),
        "t0":  _box(220.0, 50.0, 400.0, "Programador, Acme"),
        "b0":  _box(240.0, 50.0, 400.0, "Construí una API"),
        "sh":  _box(300.0, 50.0, 400.0, "Habilidades"),
        "sk":  _box(320.0, 50.0, 400.0, "Python, SQL"),
        "ih":  _box(380.0, 50.0, 400.0, "Idiomas"),
        "idi": _box(400.0, 50.0, 400.0, "Español - Nativo"),
    }
    labels = {"ah": "heading", "sum": "summary", "eh": "heading",
              "t0": "experience.0.title", "b0": "experience.0.bullets",
              "sh": "heading", "sk": "skills", "ih": "heading", "idi": "other"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"})
    # an unrecognised section keeps its own name, whatever the language
    assert "Español - Nativo" in parsed.get("Idiomas").body


def test_sections_in_an_unusual_order():
    """Skills first, summary last. Order in the design does not decide anything."""
    elements = {
        "sh":  _box(60.0, 50.0, 400.0, "Skills"),
        "sk":  _box(80.0, 50.0, 400.0, "Java"),
        "eh":  _box(140.0, 50.0, 400.0, "Experience"),
        "t0":  _box(160.0, 50.0, 400.0, "Dev, Acme"),
        "b0":  _box(180.0, 50.0, 400.0, "Built things"),
        "ah":  _box(300.0, 50.0, 400.0, "About"),
        "sum": _box(320.0, 50.0, 400.0, "A summary."),
    }
    labels = {"sh": "heading", "sk": "skills", "eh": "heading",
              "t0": "experience.0.title", "b0": "experience.0.bullets",
              "ah": "heading", "sum": "summary"}
    _check(labels, elements,
           {"summary": "sum", "skills": "sk", "experience.0.bullets": "b0"},
           ("About Me", "Work Experience", "Skills"))


def test_four_jobs_not_two():
    elements = {"ah": _box(60.0, 50.0, 400.0, "Summary"),
                "sum": _box(80.0, 50.0, 400.0, "A summary."),
                "eh": _box(140.0, 50.0, 400.0, "Experience"),
                "sh": _box(600.0, 50.0, 400.0, "Skills"),
                "sk": _box(620.0, 50.0, 400.0, "Java")}
    labels = {"ah": "heading", "sum": "summary", "eh": "heading",
              "sh": "heading", "sk": "skills"}
    for index in range(4):
        top = 160.0 + index * 100
        elements[f"t{index}"] = _box(top, 50.0, 400.0, f"Role {index}, Acme")
        elements[f"b{index}"] = _box(top + 20, 50.0, 400.0, f"Did thing {index}")
        labels[f"t{index}"] = f"experience.{index}.title"
        labels[f"b{index}"] = f"experience.{index}.bullets"

    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0", "experience.1.bullets": "b1",
                     "experience.2.bullets": "b2", "experience.3.bullets": "b3"})
    assert len(parsed.get("Work Experience").entries) == 4


def test_sections_the_agent_has_never_heard_of():
    """Publications, Certifications, Awards, Referees. No code knows these exist;
    they are placed by geometry and kept under their own names."""
    elements = {
        "ah":  _box(60.0, 50.0, 400.0, "Summary"),
        "sum": _box(80.0, 50.0, 400.0, "A summary."),
        "eh":  _box(140.0, 50.0, 400.0, "Experience"),
        "t0":  _box(160.0, 50.0, 400.0, "Dev, Acme"),
        "b0":  _box(180.0, 50.0, 400.0, "Built things"),
        "sh":  _box(240.0, 50.0, 400.0, "Skills"),
        "sk":  _box(260.0, 50.0, 400.0, "Java"),
        "ph":  _box(320.0, 50.0, 400.0, "Publications"),
        "pub": _box(340.0, 50.0, 400.0, "A paper, 2025"),
        "ch":  _box(400.0, 50.0, 400.0, "Certifications"),
        "cer": _box(420.0, 50.0, 400.0, "AWS Solutions Architect"),
        "rh":  _box(480.0, 50.0, 400.0, "Referees"),
        "ref": _box(500.0, 50.0, 400.0, "Available on request"),
    }
    labels = {"ah": "heading", "sum": "summary", "eh": "heading",
              "t0": "experience.0.title", "b0": "experience.0.bullets",
              "sh": "heading", "sk": "skills", "ph": "heading", "pub": "other",
              "ch": "heading", "cer": "other", "rh": "heading", "ref": "other"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"},
                    ("Publications", "Certifications", "Referees"))
    assert "AWS Solutions Architect" in parsed.get("Certifications").body


def test_three_column_layout():
    """A narrow left rail, a main column and a right rail of dates."""
    elements = {
        "sh":  _box(100.0, 10.0, 120.0, "Skills"),
        "sk":  _box(120.0, 10.0, 120.0, "Java"),
        "lh":  _box(300.0, 10.0, 120.0, "Languages"),
        "lang": _box(320.0, 10.0, 120.0, "English"),
        "ah":  _box(60.0, 200.0, 300.0, "Summary"),
        "sum": _box(80.0, 200.0, 300.0, "A summary."),
        "eh":  _box(160.0, 200.0, 300.0, "Experience"),
        "t0":  _box(180.0, 200.0, 300.0, "Dev, Acme"),
        "b0":  _box(200.0, 200.0, 300.0, "Built things"),
        "d0":  _box(180.0, 560.0, 100.0, "2024 - now"),
    }
    labels = {"sh": "heading", "sk": "skills", "lh": "heading", "lang": "other",
              "ah": "heading", "sum": "summary", "eh": "heading",
              "t0": "experience.0.title", "b0": "experience.0.bullets",
              "d0": "experience.0.dates"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"})
    assert "2024 - now" in parsed.get("Work Experience").entries[0].anchor
    assert "English" in parsed.get("Languages").body


def test_a_full_width_banner_does_not_lose_anything():
    """A block spanning both columns merges them into one, so placement gets less
    precise. It must still lose nothing — that is the contract."""
    elements = {
        "banner": _box(20.0, 0.0, 800.0, "SAM OKONKWO — SOFTWARE ENGINEER"),
        "ah":  _box(80.0, 400.0, 380.0, "Summary"),
        "sum": _box(100.0, 400.0, 380.0, "A summary."),
        "eh":  _box(160.0, 400.0, 380.0, "Experience"),
        "b0":  _box(180.0, 400.0, 380.0, "Built things"),
        "sh":  _box(300.0, 20.0, 200.0, "Skills"),
        "sk":  _box(320.0, 20.0, 200.0, "Java"),
    }
    labels = {"banner": "name", "ah": "heading", "sum": "summary",
              "eh": "heading", "b0": "experience.0.bullets",
              "sh": "heading", "sk": "skills"}
    _check(labels, elements,
           {"summary": "sum", "skills": "sk", "experience.0.bullets": "b0"})


def test_a_cv_with_no_extra_sections_at_all():
    """The minimum a CV can be and still be tailorable."""
    elements = {
        "ah":  _box(60.0, 50.0, 400.0, "Summary"),
        "sum": _box(80.0, 50.0, 400.0, "A summary."),
        "eh":  _box(140.0, 50.0, 400.0, "Experience"),
        "b0":  _box(160.0, 50.0, 400.0, "Built things"),
        "sh":  _box(220.0, 50.0, 400.0, "Skills"),
        "sk":  _box(240.0, 50.0, 400.0, "Java"),
    }
    labels = {"ah": "heading", "sum": "summary", "eh": "heading",
              "b0": "experience.0.bullets", "sh": "heading", "sk": "skills"}
    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"})
    assert parsed.get("Additional") is None


def test_side_by_side_sections_keep_their_own_headings():
    """A footer row of three boxes side by side — Volunteering, Military Service,
    Languages — under a CV whose body blocks span the full page width.

    Those wide blocks bridge all three groups into one column, so "the nearest
    heading above" resolves to whichever is rightmost and all three sections
    collapse into it. A heading that horizontally overlaps the block wins over
    one that merely sits above it.
    """
    elements = {
        # full-width body above, which is what merges the columns
        "sum":  _box(150.0, 63.0, 667.0, "A summary paragraph"),
        "eh":   _box(280.0, 63.0, 208.0, "Work Experience"),
        "b0":   _box(320.0, 63.0, 694.0, "Did the work"),
        "skh":  _box(890.0, 63.0, 208.0, "Key Skills"),
        "sk":   _box(920.0, 63.0, 490.0, "Java, Python"),
        # the footer row: three headings at the same height, three bodies below
        "volh": _box(993.0, 63.0, 130.0, "Volunteering"),
        "milh": _box(993.0, 369.0, 130.0, "Military service"),
        "langh": _box(993.0, 631.0, 130.0, "Languages"),
        "vol":  _box(1027.0, 63.0, 228.0, "Israel Fire and Rescue Services"),
        "mil":  _box(1027.0, 356.0, 228.0, "IDF Marine Trooper"),
        "lang": _box(1027.0, 633.0, 124.0, "Hebrew - Native"),
    }
    labels = {"sum": "summary", "eh": "heading", "b0": "experience.0.bullets",
              "skh": "heading", "sk": "skills",
              "volh": "heading", "milh": "heading", "langh": "heading",
              "vol": "other", "mil": "other", "lang": "other"}

    parsed = _check(labels, elements,
                    {"summary": "sum", "skills": "sk",
                     "experience.0.bullets": "b0"},
                    ("Volunteering", "Military service", "Languages"))
    assert "Israel Fire and Rescue" in parsed.get("Volunteering").body
    assert "IDF Marine Trooper" in parsed.get("Military service").body
    assert parsed.get("Languages").body == "Hebrew - Native"
