"""Read one Canva design through the editing API, without ever writing to it.

`get-design-content` returns a design's text with no element ids and no ordering,
so it cannot build an element map. `start-editing-transaction` returns ids and
geometry — so the design is opened for editing, read, and cancelled. Nothing is
ever committed.
"""
import json

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from jobsearch import config

_CANVA = {"canva": {"type": "http", "url": "https://mcp.canva.com/mcp"}}

_INSTRUCTIONS = """\
You are helping set up a CV tailoring tool. You have read-only work to do.

1. Identify the Canva design the user names. If they gave an id or a URL, use it
   directly with `get-design`. If they gave nothing, use `search-designs` and pick
   the design whose title most looks like a CV or résumé.
2. Call `start-editing-transaction` on it.
3. Call `cancel-editing-transaction` immediately. NEVER commit, and never call
   `perform-editing-operations`. You are reading, not editing.
4. Reply with ONLY a JSON object, no prose:

   {"design_id": "...", "title": "...", "payload": <the full
    start-editing-transaction response, verbatim>}

The payload must include its `richtexts` and `pages` exactly as returned.
"""


async def read_design(design):
    """(design_id, title, payload) for one design, chosen by id, URL, or name."""
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
    )
    asked = design or "whichever of my designs is my CV"
    reply = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(f"The design is: {asked}")
        async for message in client.receive_response():
            for block in getattr(message, "content", None) or []:
                text = getattr(block, "text", None)
                if text:
                    reply.append(text)

    joined = "".join(reply)
    start, end = joined.find("{"), joined.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"Canva returned nothing usable: {joined[:300]!r}")
    data = json.loads(joined[start:end + 1])
    return data["design_id"], data.get("title", ""), data["payload"]
