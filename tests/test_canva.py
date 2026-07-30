import pytest

from canva import compute_capacity, parse_elements, validate_map

# Trimmed from a real start-editing-transaction response (design DAHQxzJVWM4).
# Coordinates are the real measured values — the capacity assertions below depend
# on them, so do not "tidy" these numbers.
PAGE = "PB5prZGGYdD17M0v"


def _rt(suffix, top, left, width, height, regions):
    return {"page_index": 1,
            "regions": [{"type": "character", "text": t} for t in regions],
            "containerElement": {"type": "TEXT",
                                 "position": {"top": top, "left": left},
                                 "dimension": {"width": width, "height": height}},
            "element_id": f"{PAGE}-{suffix}"}


def sample_richtexts():
    return [
        _rt("LBrJ8LlFHVgPZm7d", 119.31452880277371, 314.77470420257737, 470.3843897171405, 69.33332,
            ["Final-semester Computer Science student at Ben-Gurion University, "
             "graduating February 2027, with hands-on experience in Python, Java, "
             "and automation through an ",
             "IBM Research internship", ". Seeking a ",
             "junior software engineering", " role to start contributing immediately."]),
        _rt("LB1PgdLj3TKLYW0G", 229.64784880277372, 339.83113704457054, 205.811160900411, 26.46668,
            ["Work Experience"]),
        _rt("LBk2rXZgbWWq75bp", 298.0547458981735, 309.3731157833545, 418.2637740372855, 177.33332,
            ["\n", "Built an automated Python pipeline...\n", "\n", "Mapped vulnerabilities...\n"]),
        _rt("LBy14hl84Yxspf65", 485.38806589817347, 314.77470420257737, 343.99913724117414, 17.06666,
            ["Technical Advisor | Ness Technologies"]),
        _rt("LBzpBGcBgpx9yCWC", 512.9265225196851, 310.09330372114823, 452.193283416416, 69.33332,
            ["Provided technical support to multiple IDF intelligence units...\n"]),
        _rt("LBVXZQmSm0qqbjDp", 589.2346053467181, 337.5662897723453, 205.811160900411, 26.46668,
            ["Projects"]),
        _rt("LBkVtV7y5fKZMm0H", 403.8631782089754, 4.655356610722606, 178.20128833637068, 208.959984,
            ["Java\nPython\nC++\nJavaScript\nSQL\nGit\nMCP\nClaude Code\nReact"]),
        _rt("LBg8GQtPpRxyCqhn", 621.9831512089756, 42.47257495887612, 178.9831125347481, 26.46668,
            [" Volunteering"]),
        # These two ALREADY exceed their capacity in the untouched design - the title
        # and date boxes overlap the bullets block slightly by design. They are here so
        # the scoping test below is grounded in the real layout, not a contrived case.
        _rt("LB6dWjhqhy865bfK", 283.6005963411868, 315.9218369365245, 343.99913724117414, 17.06666,
            ["Software Developer Internship | IBM Research"]),
        _rt("LBm83fB0jYRwNXp0", 285.3339363411868, 666.2945256101174, 122.68472842104507, 15.33332,
            ["Mar 2026 - present"]),
    ]


def test_parse_elements_extracts_geometry_and_regions():
    els = parse_elements(sample_richtexts())
    summary = els[f"{PAGE}-LBrJ8LlFHVgPZm7d"]
    assert summary["top"] == pytest.approx(119.3145, abs=1e-3)
    assert summary["height"] == pytest.approx(69.33332, abs=1e-3)
    assert len(summary["regions"]) == 5
    assert summary["regions"][1] == "IBM Research internship"


def test_parse_elements_skips_non_text_elements():
    rts = sample_richtexts() + [{"page_index": 1, "regions": [],
                                 "containerElement": {"type": "SHAPE",
                                                      "position": {"top": 0, "left": 0},
                                                      "dimension": {"width": 296, "height": 1122}},
                                 "element_id": f"{PAGE}-LBfQHtX4rFXWPVmp"}]
    els = parse_elements(rts)
    assert f"{PAGE}-LBfQHtX4rFXWPVmp" not in els


def test_capacity_matches_measured_slack():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)

    # capacity = top of the next element below in the same column - own top.
    # Slack = capacity - current height. These are the real measured values.
    summary = f"{PAGE}-LBrJ8LlFHVgPZm7d"
    ibm = f"{PAGE}-LBk2rXZgbWWq75bp"
    ness = f"{PAGE}-LBzpBGcBgpx9yCWC"
    skills = f"{PAGE}-LBkVtV7y5fKZMm0H"

    assert caps[summary] - els[summary]["height"] == pytest.approx(41.00, abs=0.05)
    assert caps[ibm] - els[ibm]["height"] == pytest.approx(10.00, abs=0.05)
    assert caps[ness] - els[ness]["height"] == pytest.approx(6.97, abs=0.05)
    assert caps[skills] - els[skills]["height"] == pytest.approx(9.16, abs=0.05)


def test_capacity_ignores_elements_in_the_other_column():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    # Skills is in the left column; the right column's "Projects" header sits
    # below it vertically but must not constrain it.
    skills = f"{PAGE}-LBkVtV7y5fKZMm0H"
    assert caps[skills] == pytest.approx(621.9831512089756 - 403.8631782089754, abs=0.05)


def test_capacity_is_infinite_for_the_lowest_element():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    assert caps[f"{PAGE}-LBg8GQtPpRxyCqhn"] == float("inf")


def test_validate_map_passes_when_all_ids_present():
    els = parse_elements(sample_richtexts())
    element_map = {"summary": f"{PAGE}-LBrJ8LlFHVgPZm7d",
                   "skills": f"{PAGE}-LBkVtV7y5fKZMm0H"}
    assert validate_map(els, element_map, [f"{PAGE}-LB1PgdLj3TKLYW0G"]) == []


def test_validate_map_reports_missing_element_id():
    els = parse_elements(sample_richtexts())
    problems = validate_map(els, {"skills": f"{PAGE}-GONE"}, [])
    assert len(problems) == 1
    assert "GONE" in problems[0] and "skills" in problems[0]


def test_validate_map_reports_missing_validate_only_id():
    els = parse_elements(sample_richtexts())
    problems = validate_map(els, {}, [f"{PAGE}-NOPE"])
    assert len(problems) == 1
    assert "NOPE" in problems[0]
