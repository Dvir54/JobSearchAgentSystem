"""PostToolUse hook: reduce the Monid run payload before the model ever sees it.

The Monid MCP returns the whole harvestapi scrape (~1.1MB) from `monid_get_run`.
This hook intercepts that result inside our own process, reduces it via
`tooling.reduce_run_payload`, and hands the model only the reduced envelope.

No claude_agent_sdk import: the callback is a plain async function returning a
dict, so it stays unit-testable without the SDK. agent.py does the registering.
"""
import asyncio

import tooling

# The harvestapi scrape is async and takes ~45s-2min. The agent has no clock and
# no way to wait: `disallowed_tools` removed Bash (which it previously used for
# `sleep`), so its only possible action while a run is in progress is to call
# `monid_get_run` again immediately. Pacing the loop here — rather than handing
# the agent a wait tool — keeps the capability surface closed: there is nothing
# new for it to misuse, and unlike a tool it cannot be skipped.
# Must stay well under HookMatcher's 60s default timeout.
POLL_PACING_SECONDS = 3


async def reduce_monid_output(input, tool_use_id, context):
    """Replace a completed `monid_get_run` result with the reduced job envelope.

    Returns {} — meaning "no change, keep the original output" — for anything the
    reducer cannot fully vouch for: a run still polling, a failed run, or a
    payload it could not parse.

    While a run is still in progress, sleeps briefly first so the agent's poll
    loop does not spin through its turn budget.
    """
    tool_response = input.get("tool_response")
    reduced = tooling.reduce_run_payload(tool_response)
    if reduced is None:
        if tooling.is_run_in_progress(tool_response):
            await asyncio.sleep(POLL_PACING_SECONDS)
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            # Verified against claude-agent-sdk 0.2.128: this must be a bare
            # content-block array. {"content": [...]} is rejected by the CLI.
            "updatedToolOutput": [{"type": "text", "text": reduced}],
        }
    }
