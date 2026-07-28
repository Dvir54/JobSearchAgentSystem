"""PostToolUse hook: reduce the Monid run payload before the model ever sees it.

The Monid MCP returns the whole harvestapi scrape (~1.1MB) from `monid_get_run`.
This hook intercepts that result inside our own process, reduces it via
`tooling.reduce_run_payload`, and hands the model only the reduced envelope.

No claude_agent_sdk import: the callback is a plain async function returning a
dict, so it stays unit-testable without the SDK. agent.py does the registering.
"""
import tooling


async def reduce_monid_output(input, tool_use_id, context):
    """Replace a completed `monid_get_run` result with the reduced job envelope.

    Returns {} — meaning "no change, keep the original output" — for anything the
    reducer cannot fully vouch for: a run still polling, a failed run, or a
    payload it could not parse.
    """
    reduced = tooling.reduce_run_payload(input.get("tool_response"))
    if reduced is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            # Verified against claude-agent-sdk 0.2.128: this must be a bare
            # content-block array. {"content": [...]} is rejected by the CLI.
            "updatedToolOutput": [{"type": "text", "text": reduced}],
        }
    }
