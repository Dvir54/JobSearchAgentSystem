"""Reading a design without paying to move it through a model twice."""
import asyncio
import json

from jobsearch.agent import canva_read


def _payload():
    return {"transaction": {"transaction_id": "TX1"},
            "richtexts": [
                {"element_id": "a", "regions": [{"text": "Dana Levi"}],
                 "containerElement": {"type": "TEXT",
                                      "position": {"top": 10.0, "left": 10.0},
                                      "dimension": {"width": 100.0, "height": 20.0}}},
                {"element_id": "b", "regions": [{"text": "A summary"}],
                 "containerElement": {"type": "TEXT",
                                      "position": {"top": 40.0, "left": 10.0},
                                      "dimension": {"width": 100.0, "height": 20.0}}},
            ],
            "pages": [{"page_id": "PAGE"}]}


def _hook(tool_response):
    canva_read.reset_captured()
    return asyncio.run(canva_read.capture_design(
        {"hook_event_name": "PostToolUse",
         "tool_name": "mcp__canva__start-editing-transaction",
         "tool_response": tool_response, "tool_use_id": "t"}, "t", {}))


def test_the_design_is_captured_in_process():
    _hook(json.dumps(_payload()))
    assert canva_read.captured()["richtexts"][0]["element_id"] == "a"


def test_the_model_gets_a_receipt_not_the_design():
    """A CV design is 13-16KB. Echoing it back as JSON means paying to read it
    and again to write it out, for data that never needed to reach a model —
    the same round trip the payload reducer exists to prevent."""
    out = _hook(json.dumps(_payload()))
    text = out["hookSpecificOutput"]["updatedToolOutput"][0]["text"]
    assert "richtexts" not in text
    assert "containerElement" not in text
    assert len(text) < 120
    assert json.loads(text)["blocks"] == 2


def test_it_survives_the_two_content_block_shape():
    # The Canva editing tools return the JSON design AND a thumbnail image.
    wrapped = [{"type": "text", "text": json.dumps(_payload())},
               {"type": "image", "source": {"data": "..."}}]
    _hook(wrapped)
    assert canva_read.captured()["pages"][0]["page_id"] == "PAGE"


def test_an_unreadable_response_captures_nothing_and_passes_through():
    assert _hook("not json at all") == {}
    assert canva_read.captured() is None


def test_the_reply_is_read_even_when_the_model_says_more():
    """Streamed replies can repeat the object, or wrap it in prose or a code
    fence. Taking everything between the first brace and the last one then spans
    two objects and fails to parse."""
    cases = [
        '{"design_id": "D1", "title": "My CV"}',
        'Here it is:\n```json\n{"design_id": "D1", "title": "My CV"}\n```\nDone.',
        '{"design_id": "D1", "title": "My CV"}\n{"design_id": "D1", "title": "My CV"}',
        '{"design_id": "D1", "title": "My CV"}\nI cancelled the transaction.',
    ]
    for reply in cases:
        named = canva_read.parse_reply(reply)
        assert named["design_id"] == "D1", reply
        assert named["title"] == "My CV"


def test_an_unusable_reply_says_so():
    import pytest
    with pytest.raises(RuntimeError):
        canva_read.parse_reply("I could not find that design.")
