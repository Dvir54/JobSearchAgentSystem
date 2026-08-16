"""The per-user Canva profile: which text box is which."""
import json

import pytest

from jobsearch import config
from jobsearch.resume import profile


def _good():
    return {"design_id": "DAG1", "page_id": "PAGE", "design_title": "My CV",
            "slots": {"summary": "PAGE-a", "skills": "PAGE-b",
                      "experience.0.bullets": "PAGE-c"},
            "locked": ["PAGE-d"]}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILE_PATH", tmp_path / "profile.json")
    profile.reset_cache()
    yield
    profile.reset_cache()


def test_save_then_load_round_trips():
    profile.save(_good())
    assert profile.load() == _good()


def test_load_without_a_profile_is_an_error_naming_the_command():
    with pytest.raises(FileNotFoundError) as excinfo:
        profile.load()
    assert "jobs init" in str(excinfo.value)


def test_a_complete_profile_has_no_problems():
    assert profile.problems(_good()) == []


def test_a_missing_required_slot_is_reported_by_name():
    data = _good()
    del data["slots"]["skills"]
    assert any("skills" in p for p in profile.problems(data))


def test_at_least_one_experience_entry_is_required():
    data = _good()
    del data["slots"]["experience.0.bullets"]
    assert any("experience" in p for p in profile.problems(data))


def test_an_unknown_slot_name_is_rejected():
    # Only the three editable roles may appear. A stray key means someone hand-edited
    # the file into something the guards have no rule for.
    data = _good()
    data["slots"]["projects.0.bullets"] = "PAGE-x"
    assert any("projects.0.bullets" in p for p in profile.problems(data))


def test_accessors_read_the_file():
    profile.save(_good())
    assert profile.design_id() == "DAG1"
    assert profile.page_id() == "PAGE"
    assert profile.slots()["summary"] == "PAGE-a"
    assert profile.locked() == ["PAGE-d"]


def test_config_carries_no_canva_identity():
    """Whose CV this is belongs in the profile, not the source. Also why the
    published repository contains no personal design ids."""
    for gone in ("CANVA_ELEMENT_MAP", "CANVA_VALIDATE_ONLY_IDS",
                 "CANVA_TEMPLATE_DESIGN_ID", "CANVA_PAGE_ID"):
        assert not hasattr(config, gone), f"{gone} should have moved to the profile"


def test_the_file_is_cached_but_reset_is_honoured():
    profile.save(_good())
    assert profile.design_id() == "DAG1"
    changed = _good()
    changed["design_id"] = "DAG2"
    config.PROFILE_PATH.write_text(json.dumps(changed), encoding="utf-8")
    assert profile.design_id() == "DAG1"        # still cached
    profile.reset_cache()
    assert profile.design_id() == "DAG2"
