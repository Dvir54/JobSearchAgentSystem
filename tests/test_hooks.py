import asyncio
import json

import hooks


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


def test_still_running_returns_no_replacement():
    out = _call(json.dumps({"runId": "01TEST", "status": "RUNNING"}))
    assert out == {}


def test_failed_run_returns_no_replacement():
    out = _call(json.dumps({"runId": "01TEST", "status": "FAILED"}))
    assert out == {}


def test_garbage_returns_no_replacement():
    assert _call("not json") == {}
