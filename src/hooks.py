"""PostToolUse hook: reduce the Monid run payload before the model ever sees it.

The Monid MCP returns the whole harvestapi scrape (~1.1MB) from `monid_get_run`.
This hook intercepts that result inside our own process, reduces it via
`tooling.reduce_run_payload`, and hands the model only the reduced envelope.

No claude_agent_sdk import: the callback is a plain async function returning a
dict, so it stays unit-testable without the SDK. agent.py does the registering.
"""
import asyncio
import json
import re
import sys

import canva
import config
import tooling
from resume import parse_resume
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


_BULLETS_SLOT_RE = re.compile(r"^experience\.(\d+)\.bullets$")


def _bullet_entry_index_by_element():
    """element_id -> entry_index, for every mapped `experience.N.bullets` slot."""
    result = {}
    for slot, eid in config.CANVA_ELEMENT_MAP.items():
        match = _BULLETS_SLOT_RE.match(slot)
        if match:
            result[eid] = int(match.group(1))
    return result


def _expected_bullet_counts(base_cv):
    """entry_index -> bullet count, parsed from base_cv.md's Work Experience
    section — the same source prepare_resume gates against."""
    section = parse_resume(base_cv).get(config.EXPERIENCE_SECTION)
    if not section:
        return {}
    return {i: len(entry.bullets) for i, entry in enumerate(section.entries)}


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


async def guard_canva_write(input, tool_use_id, context):
    """Deny a Canva write that would publish a skill the base CV does not support,
    or that adds/drops/splits/merges bullets in a mapped experience entry.

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
    bullets_entry_by_element = _bullet_entry_index_by_element()
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    expected_bullet_counts = _expected_bullet_counts(base_cv)

    # Grouped by element, because a bullet block now arrives as ONE
    # find_and_replace_text per bullet: the count to check is the number of
    # operations against that element, not the line count of any single one.
    by_element = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        element_id = operation.get("element_id")
        if not element_id:
            continue
        text = operation.get("text") or operation.get("replace_text") or ""
        by_element.setdefault(element_id, []).append(text)

    skills_writes = by_element.get(skills_id)
    if skills_writes:
        claimed = [line.strip() for text in skills_writes
                   for line in text.split("\n") if line.strip()]
        if claimed:
            probe = TailoredCV(summary="", skills=claimed, experience=[], projects=[])
            _, removed = strip_invented_skills(probe, base_cv)
            if removed:
                return _deny(
                    f"These skills are not supported by base_cv.md and must not be "
                    f"published: {', '.join(removed)}. Cancel the transaction, drop "
                    f"them, and try again.")

    for element_id, texts in by_element.items():
        entry_index = bullets_entry_by_element.get(element_id)
        if entry_index is None:
            continue
        expected = expected_bullet_counts.get(entry_index)
        if expected is None:
            continue                       # unparseable/out-of-range: pass through
        # One operation per bullet, each carrying exactly one bullet's text. A
        # single operation carrying several newline-separated bullets is counted
        # by its lines, so a wholesale write is still checked rather than waved past.
        claimed = sum(max(1, len([line for line in text.split("\n") if line.strip()]))
                      for text in texts)
        if claimed != expected:
            return _deny(
                f"experience entry [{entry_index}] has {expected} bullet(s) in "
                f"base_cv.md, but this write carries {claimed}. Bullets must not be "
                f"added, dropped, split, or merged. Cancel the transaction and fix "
                f"the bullet count.")
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


# `perform-editing-operations` documents its `pages` argument as "the `pages` array
# returned by the last call ... or if this is the first call the
# `start-editing-transaction` tool" — it uses it to decide which pages are responsive
# and editable. The schema does not list it as required, but reducing it away would
# leave the agent unable to supply it at all, so it is forwarded verbatim. It is
# three fields for a one-page design; `richtexts` was always the bulk, not this.
_PAGE_FIELDS = ("page_id", "is_responsive", "is_editable", "is_empty")


def _forwarded_pages(run):
    return [{k: page[k] for k in _PAGE_FIELDS if k in page}
            for page in (run.get("pages") or []) if isinstance(page, dict)]


def _candidate_payloads(value, depth=0):
    """Every dict reachable inside a tool_response, outermost first."""
    if depth > 4:
        return
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return
        yield from _candidate_payloads(parsed, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _candidate_payloads(item, depth + 1)
    elif isinstance(value, dict):
        yield value
        for key in ("content", "text", "result", "toolResult", "structuredContent"):
            if key in value:
                yield from _candidate_payloads(value[key], depth + 1)


def _parse_payload(tool_response):
    """Dig the Canva design payload out of whatever shape the CLI hands the hook.

    The Monid reducer receives a plain dict, and this used to assume the same. It
    is not the same: the Canva editing tools return TWO content blocks — the JSON
    design plus a thumbnail image — so the result does not collapse to a single
    object and arrives wrapped. The first live smoke run declined on exactly this,
    which left the whole Canva reduction inert: no geometry retained, no overflow
    check, and the raw ~16KB design reaching the model twice per job. Searching the
    shape beats betting on one.
    """
    for candidate in _candidate_payloads(tool_response):
        if "richtexts" in candidate:
            return candidate
    return None


def _describe(tool_response):
    """A short, safe shape description for a decline log — never the payload."""
    if isinstance(tool_response, dict):
        return f"dict(keys={sorted(tool_response)[:8]})"
    if isinstance(tool_response, list):
        kinds = sorted({type(item).__name__ for item in tool_response})
        return f"list(len={len(tool_response)}, of={kinds})"
    if isinstance(tool_response, str):
        return f"str(len={len(tool_response)}, starts={tool_response[:60]!r})"
    return type(tool_response).__name__


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
        tool_response = input.get("tool_response")
        run = _parse_payload(tool_response)
        if run is None:
            print(f"[canva] DECLINED: no 'richtexts' found in the "
                  f"start-editing-transaction response — reduction did NOT run, and "
                  f"the overflow check will not run for this job either. Shape was "
                  f"{_describe(tool_response)}", file=sys.stderr)
            return {}
        transaction_id = (run.get("transaction") or {}).get("transaction_id")
        elements = canva.parse_elements(run["richtexts"])
        _CAPACITY_BY_TRANSACTION[transaction_id] = canva.compute_capacity(elements)
        pages = _forwarded_pages(run)
        print(f"[canva] transaction {transaction_id}: {len(elements)} elements, "
              f"geometry retained in-process", file=sys.stderr)
        return _slim({"transaction_id": transaction_id,
                      "page_id": pages[0].get("page_id") if pages else None,
                      "pages": pages,
                      "element_count": len(elements)})

    if name == _CANVA_PERFORM:
        tool_input = input.get("tool_input") or {}
        transaction_id = tool_input.get("transaction_id")
        capacity = _CAPACITY_BY_TRANSACTION.get(transaction_id)
        tool_response = input.get("tool_response")
        run = _parse_payload(tool_response)
        if capacity is None or run is None:
            print(f"[canva] DECLINED: retained geometry for transaction "
                  f"{transaction_id!r} is {'missing' if capacity is None else 'present'} "
                  f"and the payload was {'unreadable' if run is None else 'readable'} — "
                  f"no overflow check for this write. Shape was "
                  f"{_describe(tool_response)}", file=sys.stderr)
            return {}

        if "edit_operation_results" not in run:
            print(f"[canva] DECLINED: no edit_operation_results in the "
                  f"perform-editing-operations payload for transaction "
                  f"{transaction_id!r} — cannot confirm the writes actually "
                  f"succeeded; passing through unreduced rather than implying "
                  f"success", file=sys.stderr)
            return {}

        sent = [op for op in (tool_input.get("operations") or [])
                if isinstance(op, dict) and op.get("element_id")]
        edited = [op["element_id"] for op in sent]
        after = canva.parse_elements(run["richtexts"])
        overflows = canva.find_overflows(after, capacity, edited)
        # A find_and_replace_text that matched nothing still reports success, so
        # the reported status is checked AND the result is verified against the
        # text that actually landed.
        unapplied = canva.find_unapplied(sent, after)

        results = run.get("edit_operation_results") or []
        failed = [r for r in results if r.get("status") != "success"]
        if overflows:
            summary = {eid: round(info["overflow_px"], 1)
                       for eid, info in overflows.items()}
            print(f"[canva] OVERFLOW in transaction {transaction_id}: {summary}",
                  file=sys.stderr)
        if unapplied:
            print(f"[canva] NOT APPLIED in transaction {transaction_id}: {unapplied} "
                  f"— the API reported success but the text is not in the element",
                  file=sys.stderr)
        return _slim({
            "ok": not overflows and not failed and not unapplied,
            "overflow": {eid: round(info["overflow_px"], 1)
                         for eid, info in overflows.items()},
            "failed_operations": failed,
            "not_applied": unapplied,
        })

    return {}
