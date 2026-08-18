"""Read one Canva design through the editing API, without ever writing to it.

`get-design-content` returns a design's text with no element ids and no ordering,
so it cannot build an element map. `start-editing-transaction` returns ids and
geometry — so the design is opened for editing, read, and cancelled. Nothing is
ever committed.

The design itself never passes through the model. A hook keeps the payload in
this process and hands the session a short receipt instead; the model's only job
is to find the right design and confirm. A CV design is 13-16KB, so asking it to
repeat the payload back as JSON would mean paying to read it and again to write
it out, for data no model needed to see — the same round trip the payload reducer
exists to prevent.
"""
import json

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher

from jobsearch import config
from jobsearch.agent.hooks import _parse_payload, _slim

_CANVA = {"canva": {"type": "http", "url": "https://mcp.canva.com/mcp"}}
_START = "mcp__canva__start-editing-transaction"

_CAPTURED = {}


def reset_captured():
    _CAPTURED.clear()


def captured():
    """The design payload this process kept, or None."""
    return _CAPTURED.get("payload")


async def capture_design(input, tool_use_id, context):
    """Keep the design here and give the session a receipt.

    Anything it cannot read passes through untouched, so a shape we did not
    anticipate degrades to the old behaviour rather than losing the design.
    """
    payload = _parse_payload(input.get("tool_response"))
    if payload is None:
        return {}
    _CAPTURED["payload"] = payload
    return _slim({"ok": True, "blocks": len(payload.get("richtexts") or [])})


_INSTRUCTIONS = """\
You are helping set up a CV tailoring tool. Your work is read-only.

1. Identify the Canva design the user names. If they gave an id or a URL, use it
   with `get-design`. If they gave neither, use `search-designs` and pick the
   design whose title most looks like a CV or resume.
2. Call `start-editing-transaction` on it.
3. Call `cancel-editing-transaction` immediately. NEVER commit, and never call
   `perform-editing-operations`. You are reading, not editing.
4. Reply with ONLY this JSON object and nothing else:

   {"design_id": "...", "title": "..."}

The design's contents are captured for you — do not repeat them back.
"""


def parse_reply(reply):
    """The {design_id, title} object out of whatever the model actually said.

    Decoded from the first brace rather than sliced to the last one: a streamed
    reply can repeat the object, or follow it with prose or a closing code fence,
    and a first-to-last slice then spans two objects and fails to parse.
    """
    decoder = json.JSONDecoder()
    for index, character in enumerate(reply):
        if character != "{":
            continue
        try:
            found, _ = decoder.raw_decode(reply[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(found, dict) and found.get("design_id"):
            return found
    raise RuntimeError(f"Canva returned nothing usable: {reply[:300]!r}")


async def read_design(design):
    """(design_id, title, payload) for one design, chosen by id, URL, or name."""
    reset_captured()
    options = ClaudeAgentOptions(
        system_prompt=_INSTRUCTIONS,
        mcp_servers=_CANVA,
        allowed_tools=["mcp__canva__search-designs", "mcp__canva__get-design",
                       "mcp__canva__start-editing-transaction",
                       "mcp__canva__cancel-editing-transaction"],
        disallowed_tools=["Bash", "Read", "Write", "WebFetch", "Agent"],
        max_turns=20,
        max_buffer_size=10 * 1024 * 1024,
        env={"MAX_MCP_OUTPUT_TOKENS": config.MAX_MCP_OUTPUT_TOKENS},
        load_timeout_ms=config.SDK_LOAD_TIMEOUT_MS,
        hooks={"PostToolUse": [HookMatcher(matcher=_START,
                                           hooks=[capture_design])]},
    )
    reply = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"The design is: {design}")
        async for message in client.receive_response():
            for block in getattr(message, "content", None) or []:
                text = getattr(block, "text", None)
                if text:
                    reply.append(text)

    payload = captured()
    if payload is None:
        raise RuntimeError(
            "The design was opened but its contents were never captured. "
            "Check that Canva is authorised and the design is a one-page CV.")

    named = parse_reply("".join(reply))
    return named["design_id"], named.get("title", ""), payload
