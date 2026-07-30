"""PostToolUse hook: reduce the Monid run payload before the model ever sees it.

The Monid MCP returns the whole harvestapi scrape (~1.1MB) from `monid_get_run`.
This hook intercepts that result inside our own process, reduces it via
`tooling.reduce_run_payload`, and hands the model only the reduced envelope.

No claude_agent_sdk import: the callback is a plain async function returning a
dict, so it stays unit-testable without the SDK. agent.py does the registering.
"""
import asyncio
import json
import sys

import canva
import config
import tooling
from tailoring import TailoredCV, strip_invented_skills

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


_CANVA_WRITE_TOOL = "mcp__canva__perform-editing-operations"


def _skills_element_id():
    return config.CANVA_ELEMENT_MAP["skills"]


async def guard_canva_write(input, tool_use_id, context):
    """Deny a Canva write that would publish a skill the base CV does not support.

    prepare_resume computes the correct text, but the AGENT makes the Canva calls —
    an in-process tool cannot call an MCP server — so this hook is the real
    enforcement boundary. It inspects what is actually about to be written.

    Returns {} to allow. Anything it cannot fully parse is allowed through
    unchanged: this hook exists to catch untruthful content, not to become a new
    way for the run to fail.
    """
    if input.get("tool_name") != _CANVA_WRITE_TOOL:
        return {}

    tool_input = input.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    operations = tool_input.get("operations")
    if not isinstance(operations, list):
        return {}

    skills_id = _skills_element_id()
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")

    for operation in operations:
        if not isinstance(operation, dict) or operation.get("element_id") != skills_id:
            continue
        text = operation.get("text") or operation.get("replace_text") or ""
        claimed = [line.strip() for line in text.split("\n") if line.strip()]
        if not claimed:
            continue
        probe = TailoredCV(summary="", skills=claimed, experience=[], projects=[])
        _, removed = strip_invented_skills(probe, base_cv)
        if removed:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"These skills are not supported by base_cv.md and must not be "
                        f"published: {', '.join(removed)}. Cancel the transaction, drop "
                        f"them, and try again."),
                }
            }
    return {}


_CANVA_START = "mcp__canva__start-editing-transaction"
_CANVA_PERFORM = "mcp__canva__perform-editing-operations"
_CANVA_END = ("mcp__canva__commit-editing-transaction",
              "mcp__canva__cancel-editing-transaction")

# transaction_id -> {element_id: capacity_px}, held here so the geometry never
# reaches the model. Released when the transaction commits or cancels.
_CAPACITY_BY_TRANSACTION = {}


def _slim(payload):
    return {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "updatedToolOutput": [{"type": "text",
                               "text": json.dumps(payload, ensure_ascii=False)}],
    }}


def _parse_payload(tool_response):
    if isinstance(tool_response, dict):
        return tool_response
    try:
        return json.loads(tool_response)
    except (TypeError, ValueError):
        return None


async def reduce_canva_output(input, tool_use_id, context):
    """Replace the Canva element dumps with the few facts the agent needs.

    start-editing-transaction and perform-editing-operations each return the whole
    design (~13-16KB, twice per job). The agent needs a transaction id and an
    overflow verdict; the element map is already pinned in config. Holding the
    geometry here also lets `canva.find_overflows` make the overflow call in code,
    rather than the model comparing floats.

    Anything it cannot fully vouch for passes through untouched.
    """
    name = input.get("tool_name")

    if name in _CANVA_END:
        transaction_id = (input.get("tool_input") or {}).get("transaction_id")
        _CAPACITY_BY_TRANSACTION.pop(transaction_id, None)
        return {}

    if name == _CANVA_START:
        run = _parse_payload(input.get("tool_response"))
        if not isinstance(run, dict) or "richtexts" not in run:
            print("[canva] DECLINED: start-editing-transaction payload not understood; "
                  "passing through unreduced", file=sys.stderr)
            return {}
        transaction_id = (run.get("transaction") or {}).get("transaction_id")
        elements = canva.parse_elements(run["richtexts"])
        _CAPACITY_BY_TRANSACTION[transaction_id] = canva.compute_capacity(elements)
        pages = run.get("pages") or [{}]
        print(f"[canva] transaction {transaction_id}: {len(elements)} elements, "
              f"geometry retained in-process", file=sys.stderr)
        return _slim({"transaction_id": transaction_id,
                      "page_id": pages[0].get("page_id"),
                      "element_count": len(elements)})

    if name == _CANVA_PERFORM:
        tool_input = input.get("tool_input") or {}
        transaction_id = tool_input.get("transaction_id")
        capacity = _CAPACITY_BY_TRANSACTION.get(transaction_id)
        run = _parse_payload(input.get("tool_response"))
        if capacity is None or not isinstance(run, dict) or "richtexts" not in run:
            print(f"[canva] DECLINED: no retained geometry for transaction "
                  f"{transaction_id!r}, or unreadable payload; passing through",
                  file=sys.stderr)
            return {}

        edited = [op.get("element_id") for op in (tool_input.get("operations") or [])
                  if isinstance(op, dict) and op.get("element_id")]
        after = canva.parse_elements(run["richtexts"])
        overflows = canva.find_overflows(after, capacity, edited)

        results = run.get("edit_operation_results") or []
        failed = [r for r in results if r.get("status") != "success"]
        if overflows:
            summary = {eid: round(info["overflow_px"], 1)
                       for eid, info in overflows.items()}
            print(f"[canva] OVERFLOW in transaction {transaction_id}: {summary}",
                  file=sys.stderr)
        return _slim({
            "ok": not overflows and not failed,
            "overflow": {eid: round(info["overflow_px"], 1)
                         for eid, info in overflows.items()},
            "failed_operations": failed,
        })

    return {}
