import asyncio
import json

import pytest

import config
import hooks
from tooling import strip_invented_skills  # noqa: F401  (proves the guard path is shared)


@pytest.fixture(autouse=True)
def _clear_canva_capacity_state():
    # _CAPACITY_BY_TRANSACTION is module-level state shared across every test that
    # touches reduce_canva_output; without this, a transaction id reused between
    # tests (e.g. "TX1") leaks capacity from one test into the next.
    hooks._CAPACITY_BY_TRANSACTION.clear()
    yield
    hooks._CAPACITY_BY_TRANSACTION.clear()


def _hook_input(tool_response, tool_name="mcp__monid__monid_get_run"):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name,
            "tool_input": {"runId": "01TEST"}, "tool_response": tool_response,
            "tool_use_id": "toolu_test"}


def _call(tool_response):
    return asyncio.run(hooks.reduce_monid_output(_hook_input(tool_response),
                                                 "toolu_test", {}))


def _completed(output):
    return json.dumps({"runId": "01TEST", "status": "COMPLETED",
                       "input": {"body": {"postedLimit": "week"}}, "output": output})


def _raw(job_id, location):
    return {"id": job_id, "title": "Developer", "company": {"name": "Acme"},
            "descriptionText": "desc", "linkedinUrl": "https://x",
            "postedDate": "2026-07-27T01:47:21.000Z",
            "location": {"linkedinText": location}}


def test_returns_updated_tool_output_as_a_content_block_array():
    out = _call(_completed([_raw("1", "Tel Aviv, Israel")]))
    spec = out["hookSpecificOutput"]
    assert spec["hookEventName"] == "PostToolUse"
    blocks = spec["updatedToolOutput"]
    # MUST be a bare array of content blocks; {"content": [...]} crashes the CLI
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "text"
    assert json.loads(blocks[0]["text"])["kept"] == 1


def test_output_is_not_the_wrapped_content_shape():
    out = _call(_completed([_raw("1", "Israel")]))
    blocks = out["hookSpecificOutput"]["updatedToolOutput"]
    # {"content": [...]} is what the CLI rejects with "e.reduce is not a function"
    assert not isinstance(blocks, dict)
    assert all(set(b) == {"type", "text"} for b in blocks)


def test_still_running_returns_no_replacement(monkeypatch):
    # _sleep_spy keeps the poll-pacing sleep from making the suite wait 3s for real.
    _sleep_spy(monkeypatch)
    out = _call(json.dumps({"runId": "01TEST", "status": "RUNNING"}))
    assert out == {}


def test_failed_run_returns_no_replacement():
    out = _call(json.dumps({"runId": "01TEST", "status": "FAILED"}))
    assert out == {}


def test_garbage_returns_no_replacement():
    assert _call("not json") == {}


def _sleep_spy(monkeypatch):
    """Record asyncio.sleep calls instead of actually sleeping."""
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(hooks.asyncio, "sleep", fake_sleep)
    return slept


def test_paces_the_poll_loop_while_the_run_is_in_progress(monkeypatch):
    # The agent has no wait tool (Bash is disallowed), so an unpaced RUNNING poll
    # spins through its turn budget. The hook sleeps on its behalf.
    slept = _sleep_spy(monkeypatch)
    out = _call(json.dumps({"runId": "01TEST", "status": "RUNNING"}))
    assert out == {}
    assert slept == [hooks.POLL_PACING_SECONDS]


def test_does_not_pace_on_a_terminal_failure(monkeypatch):
    # FAILED is terminal: the agent should see the error immediately, not after a wait.
    slept = _sleep_spy(monkeypatch)
    assert _call(json.dumps({"runId": "01TEST", "status": "FAILED"})) == {}
    assert slept == []


def test_does_not_pace_on_an_unparseable_payload(monkeypatch):
    # The oversized-stub case: not a run object, so there is nothing to wait for.
    slept = _sleep_spy(monkeypatch)
    assert _call("Error: result (774,006 characters) exceeds maximum allowed tokens.") == {}
    assert slept == []


def test_does_not_pace_when_the_run_completed(monkeypatch):
    slept = _sleep_spy(monkeypatch)
    out = _call(_completed([_raw("1", "Tel Aviv, Israel")]))
    assert out["hookSpecificOutput"]["updatedToolOutput"]
    assert slept == []


def _canva_input(operations):
    return {"hook_event_name": "PreToolUse",
            "tool_name": "mcp__canva__perform-editing-operations",
            "tool_input": {"transaction_id": "1", "page_index": 1, "operations": operations},
            "tool_use_id": "toolu_test"}


def _call_guard(operations):
    return asyncio.run(hooks.guard_canva_write(_canva_input(operations), "toolu_test", {}))


SKILLS_ID = config.CANVA_ELEMENT_MAP["skills"]
BULLETS_0_EL = config.CANVA_ELEMENT_MAP["experience.0.bullets"]


def test_guard_allows_skills_present_in_the_base_cv():
    out = _call_guard([{"type": "replace_text", "element_id": SKILLS_ID,
                        "text": "Python\nJava\nSQL"}])
    assert out == {}


def test_guard_denies_a_skill_absent_from_the_base_cv():
    out = _call_guard([{"type": "replace_text", "element_id": SKILLS_ID,
                        "text": "Python\nKubernetes"}])
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Kubernetes" in decision["permissionDecisionReason"]


def test_guard_ignores_operations_on_unmapped_elements():
    out = _call_guard([{"type": "replace_text", "element_id": "PAGE-unmapped",
                        "text": "anything at all"}])
    assert out == {}


def test_guard_allows_a_non_canva_tool_untouched():
    other = {"hook_event_name": "PreToolUse", "tool_name": "mcp__monid__monid_run",
             "tool_input": {}, "tool_use_id": "t"}
    assert asyncio.run(hooks.guard_canva_write(other, "t", {})) == {}


def test_guard_never_raises_on_a_malformed_payload():
    bad = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__canva__perform-editing-operations",
           "tool_input": None, "tool_use_id": "t"}
    assert asyncio.run(hooks.guard_canva_write(bad, "t", {})) == {}


def test_guard_allows_legitimate_find_and_replace_text_on_skills():
    """find_and_replace_text operations carry the new text in replace_text field."""
    out = _call_guard([{"type": "find_and_replace_text", "element_id": SKILLS_ID,
                        "find": "Java", "replace_text": "Python\nJava\nSQL"}])
    assert out == {}


def test_guard_denies_invented_skills_via_find_and_replace_text():
    """Invented skills in find_and_replace_text (replace_text field) must be denied."""
    out = _call_guard([{"type": "find_and_replace_text", "element_id": SKILLS_ID,
                        "find": "Java", "replace_text": "Python\nKubernetes"}])
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Kubernetes" in decision["permissionDecisionReason"]


# base_cv.md's Work Experience: entry [0] (IBM Research) has 2 bullets,
# entry [1] (Ness Technologies) has 1 bullet — these tests depend on that shape.
EXPERIENCE_0_BULLETS_EL = config.CANVA_ELEMENT_MAP["experience.0.bullets"]
EXPERIENCE_1_BULLETS_EL = config.CANVA_ELEMENT_MAP["experience.1.bullets"]


def test_guard_allows_a_bullet_count_that_matches_base_cv():
    out = _call_guard([{"type": "replace_text", "element_id": EXPERIENCE_0_BULLETS_EL,
                        "text": "Reworded bullet one.\nReworded bullet two."}])
    assert out == {}


def test_guard_denies_a_dropped_bullet():
    out = _call_guard([{"type": "replace_text", "element_id": EXPERIENCE_0_BULLETS_EL,
                        "text": "Only one bullet now."}])
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert "[0]" in reason and "2" in reason and "1" in reason


def test_guard_denies_an_added_bullet():
    out = _call_guard([{"type": "replace_text", "element_id": EXPERIENCE_1_BULLETS_EL,
                        "text": "Original bullet.\nA brand new invented bullet."}])
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert "[1]" in reason and "1" in reason and "2" in reason


PAGE = "PB5prZGGYdD17M0v"
SUMMARY_EL = f"{PAGE}-LBrJ8LlFHVgPZm7d"
SKILLS_EL = f"{PAGE}-LBkVtV7y5fKZMm0H"


def _post(tool_name, tool_response, tool_input=None):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name,
            "tool_input": tool_input or {}, "tool_response": tool_response,
            "tool_use_id": "toolu_test"}


def _reduce(tool_name, tool_response, tool_input=None):
    return asyncio.run(hooks.reduce_canva_output(
        _post(tool_name, tool_response, tool_input), "toolu_test", {}))


def _rt_el(eid, top, left, width, height, texts):
    return {"regions": [{"type": "character", "text": t} for t in texts],
            "containerElement": {"type": "TEXT",
                                 "position": {"top": top, "left": left},
                                 "dimension": {"width": width, "height": height}},
            "element_id": eid}


def _start_payload(summary_height=69.33332, skills_height=208.959984,
                   edit_operation_results=None):
    payload = {
        "transaction": {"status": "open", "transaction_id": "TX1"},
        "richtexts": [
            _rt_el(SUMMARY_EL, 119.31, 314.77, 470.38, summary_height, ["a"]),
            _rt_el(f"{PAGE}-header", 229.64, 339.83, 205.81, 26.46, ["Work Experience"]),
            _rt_el(SKILLS_EL, 403.86, 4.65, 178.20, skills_height, ["Java"]),
            _rt_el(f"{PAGE}-vol", 621.98, 42.47, 178.98, 26.46, [" Volunteering"]),
        ],
        "pages": [{"page_id": PAGE, "page_number": 1, "is_responsive": False,
                   "is_editable": True, "is_empty": False, "thumbnail": {"url": "..."}}],
    }
    # Only a real perform-editing-operations response carries this key. Tests that
    # exercise the "everything fits" / overflow paths pass it explicitly; tests
    # that only care about start-editing-transaction, or about the missing-key
    # pass-through itself, leave it out.
    if edit_operation_results is not None:
        payload["edit_operation_results"] = edit_operation_results
    return json.dumps(payload)


def test_start_transaction_is_reduced_to_ids_only():
    out = _reduce("mcp__canva__start-editing-transaction", _start_payload())
    text = out["hookSpecificOutput"]["updatedToolOutput"][0]["text"]
    payload = json.loads(text)
    assert payload["transaction_id"] == "TX1"
    assert payload["page_id"] == PAGE
    # the element dump must NOT reach the model
    assert "richtexts" not in payload
    assert "containerElement" not in text
    assert len(text) < 400


def test_start_transaction_forwards_the_pages_array_perform_needs():
    """`perform-editing-operations` documents `pages` as coming from this response;
    reducing it away would leave the agent unable to supply it at all."""
    out = _reduce("mcp__canva__start-editing-transaction", _start_payload())
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["pages"] == [{"page_id": PAGE, "is_responsive": False,
                                 "is_editable": True, "is_empty": False}]
    # only the fields the write tool's schema declares — no thumbnails, no bulk
    assert "thumbnail" not in out["hookSpecificOutput"]["updatedToolOutput"][0]["text"]


def test_start_transaction_survives_a_payload_with_no_pages():
    payload_without_pages = json.loads(_start_payload())
    del payload_without_pages["pages"]
    out = _reduce("mcp__canva__start-editing-transaction",
                  json.dumps(payload_without_pages))
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["pages"] == []
    assert payload["page_id"] is None


def test_perform_operations_reports_ok_when_everything_fits():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    out = _reduce("mcp__canva__perform-editing-operations",
                  _start_payload(edit_operation_results=[]),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "Java"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is True
    assert payload["overflow"] == {}


def test_perform_operations_reports_overflow_with_the_pixel_amount():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    # skills capacity is 621.98 - 403.86 = 218.12; make it 20px taller than that
    out = _reduce("mcp__canva__perform-editing-operations",
                  _start_payload(skills_height=238.12, edit_operation_results=[]),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "Java"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is False
    assert payload["overflow"][SKILLS_EL] == pytest.approx(20.0, abs=0.05)


def test_perform_operations_only_judges_the_elements_it_edited():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    # the summary is wildly over its capacity, but we only edited skills
    out = _reduce("mcp__canva__perform-editing-operations",
                  _start_payload(summary_height=9999, edit_operation_results=[]),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "Java"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is True


def test_perform_operations_reports_failed_operation_as_not_ok():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    out = _reduce(
        "mcp__canva__perform-editing-operations",
        _start_payload(edit_operation_results=[
            {"status": "error", "operation_info": {"type": "replace_text",
                                                    "element_id": SKILLS_EL}},
        ]),
        {"transaction_id": "TX1",
         "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                         "text": "Java"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is False
    assert len(payload["failed_operations"]) == 1
    assert payload["failed_operations"][0]["status"] == "error"


def test_perform_operations_passes_through_when_edit_operation_results_is_absent():
    # Finding 2: a missing key must NOT be treated as "no failures" — that would
    # let the agent commit and publish an untailored template as a real success.
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    payload_without_results = _start_payload()   # edit_operation_results omitted
    assert "edit_operation_results" not in json.loads(payload_without_results)
    out = _reduce(
        "mcp__canva__perform-editing-operations", payload_without_results,
        {"transaction_id": "TX1",
         "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                         "text": "Java"}]})
    assert out == {}


def test_commit_releases_the_stored_geometry():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    assert "TX1" in hooks._CAPACITY_BY_TRANSACTION
    _reduce("mcp__canva__commit-editing-transaction", "{}", {"transaction_id": "TX1"})
    assert "TX1" not in hooks._CAPACITY_BY_TRANSACTION


def test_cancel_releases_the_stored_geometry():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    _reduce("mcp__canva__cancel-editing-transaction", "{}", {"transaction_id": "TX1"})
    assert "TX1" not in hooks._CAPACITY_BY_TRANSACTION


def test_perform_without_a_known_transaction_passes_through():
    # No start seen for this id - do not invent an overflow verdict.
    assert _reduce("mcp__canva__perform-editing-operations", _start_payload(),
                   {"transaction_id": "UNKNOWN", "operations": []}) == {}


def test_unparseable_canva_payload_passes_through():
    assert _reduce("mcp__canva__start-editing-transaction", "not json") == {}


def test_non_canva_tool_is_untouched():
    assert _reduce("mcp__monid__monid_run", "{}") == {}


# --- Behaviours the first live smoke run discovered ---

def test_start_transaction_is_reduced_when_wrapped_in_content_blocks():
    """The Canva tools return TWO content blocks (JSON + thumbnail), so the payload
    arrives wrapped rather than as a bare dict. Assuming the bare shape is what made
    the whole Canva reduction sit inert on the first live run."""
    wrapped = [{"type": "text", "text": _start_payload()},
               {"type": "image", "source": {"data": "..."}}]
    out = _reduce("mcp__canva__start-editing-transaction", wrapped)
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["transaction_id"] == "TX1"
    assert "TX1" in hooks._CAPACITY_BY_TRANSACTION


def test_start_transaction_is_reduced_when_wrapped_in_a_content_dict():
    wrapped = {"content": [{"type": "text", "text": _start_payload()}]}
    out = _reduce("mcp__canva__start-editing-transaction", wrapped)
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["transaction_id"] == "TX1"


def test_perform_reports_not_applied_when_the_text_never_landed():
    """A find_and_replace_text whose find_text matches nothing changes nothing and
    still reports status success — measured against the live API. Committing that
    publishes the untailored template text as though it were tailored."""
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    out = _reduce(
        "mcp__canva__perform-editing-operations",
        _start_payload(edit_operation_results=[
            {"status": "success", "operation_info": {"element_id": SKILLS_EL}}]),
        {"transaction_id": "TX1",
         "operations": [{"type": "find_and_replace_text", "element_id": SKILLS_EL,
                         "find_text": "nothing matches this",
                         "replace_text": "Rust"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is False
    assert payload["failed_operations"] == []          # the API said success
    assert payload["not_applied"] == [{"element_id": SKILLS_EL,
                                       "type": "find_and_replace_text"}]


def test_perform_reports_applied_when_the_text_did_land():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    out = _reduce(
        "mcp__canva__perform-editing-operations",
        _start_payload(edit_operation_results=[]),
        {"transaction_id": "TX1",
         "operations": [{"type": "find_and_replace_text", "element_id": SKILLS_EL,
                         "find_text": "Ja", "replace_text": "Java"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is True
    assert payload["not_applied"] == []


def test_guard_allows_one_find_and_replace_operation_per_bullet():
    """IBM entry [0] has 2 bullets, so 2 single-bullet operations is correct and
    must not be read as '1 bullet' twice."""
    ops = [{"type": "find_and_replace_text", "element_id": BULLETS_0_EL,
            "find_text": "old one", "replace_text": "Reworded one"},
           {"type": "find_and_replace_text", "element_id": BULLETS_0_EL,
            "find_text": "old two", "replace_text": "Reworded two"}]
    assert _call_guard(ops) == {}


def test_guard_denies_a_dropped_bullet_across_find_and_replace_operations():
    ops = [{"type": "find_and_replace_text", "element_id": BULLETS_0_EL,
            "find_text": "old one", "replace_text": "Only one survives"}]
    out = _call_guard(ops)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "2 bullet(s)" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_guard_denies_an_extra_bullet_across_find_and_replace_operations():
    ops = [{"type": "find_and_replace_text", "element_id": BULLETS_0_EL,
            "find_text": f"old {i}", "replace_text": f"Reworded {i}"}
           for i in range(3)]
    out = _call_guard(ops)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
