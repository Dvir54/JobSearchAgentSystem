# Agent Payload Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the raw Monid scrape from reaching the model — reduce it in-process with a `PostToolUse` hook so the agent sees ~31K tokens of clean jobs instead of ~284K tokens of raw JSON, with job descriptions kept in full.

**Architecture:** A `PostToolUse` hook matched to `mcp__monid__monid_get_run` intercepts the run result inside the Python process, runs a deterministic reducer built on the existing `tooling.clean_jobs`, and returns `updatedToolOutput` so the model only ever sees the reduced payload. The `filter_jobs` tool — which required the model to re-emit the entire scrape as tool arguments — is deleted. The Monid MCP itself is unchanged.

**Tech Stack:** Python 3.11, `claude-agent-sdk` 0.2.128, pytest 8 (`pythonpath = ["src"]`, `testpaths = ["tests"]`).

**Spec:** `docs/superpowers/specs/2026-07-28-agent-payload-reduction-design.md`

## Global Constraints

- **Descriptions are kept in full.** Never truncate, summarise, or strip `description`. The measured waste is the ~89% of the payload that is *not* description.
- **Pass through anything not fully understood.** The hook replaces output only for a `COMPLETED` run with a list `output`. Non-terminal status, `FAILED`/`BLOCKED`, unparseable text, unexpected shape, or any exception from the reducer must return the original output untouched. A failed optimisation degrades to today's behaviour; it never turns a data problem into a silent empty result.
- **Hook output contract (verified live against `claude-agent-sdk` 0.2.128):** field is `updatedToolOutput`; value must be a **bare content-block array** `[{"type": "text", "text": ...}]`. `{"content": [...]}` crashes the CLI with `e.reduce is not a function`.
- **Hook input:** `input["tool_response"]` arrives as a **`str`** of JSON.
- **`max_buffer_size = 10 * 1024 * 1024` stays** in `agent.py`. The raw result still crosses the CLI↔Python pipe to reach the hook; the reduction only shrinks what goes on to the model. Removing it re-breaks the run.
- **`clean_jobs` keeps its current signature** (`list -> list`) and its existing tests. All new logic goes in a wrapper.
- **`window` comes from `run["input"]["body"]["postedLimit"]`** — Monid's echo of the input that actually ran — not from `config`.
- **`tooling.py` and `hooks.py` stay SDK-free** so both remain unit-testable without an agent run. Only `agent.py` imports from `claude_agent_sdk`.
- Run tests with `.venv/Scripts/python.exe -m pytest` from the repo root.

---

### Task 1: The deterministic reducer (`reduce_run_payload`)

**Files:**
- Modify: `src/tooling.py` (add `reduce_run_payload` + `_window`; `clean_jobs` at `tooling.py:49` is unchanged)
- Test: `tests/test_reduce.py` (create)

**Interfaces:**
- Consumes: `tooling.clean_jobs(raw_items: list) -> list[dict]` — existing, unchanged. Returns dicts with keys `id, title, company, description, url, posted_date, location`.
- Produces: `tooling.reduce_run_payload(tool_response: str | dict) -> str | None` — returns the reduced envelope as JSON **text**, or `None` meaning "pass the original through untouched". Task 2 depends on exactly this `None` contract.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reduce.py`:

```python
import json
from pathlib import Path

from tooling import reduce_run_payload

FIXTURE = Path(__file__).parent / "fixtures" / "harvestapi_response.json"


def _raw(job_id, location, title="Developer", description="desc"):
    return {"id": job_id, "title": title, "company": {"name": "Acme"},
            "descriptionText": description, "linkedinUrl": "https://x",
            "postedDate": "2026-07-27T01:47:21.000Z",
            "location": {"linkedinText": location}}


def _run(status="COMPLETED", output=None, window="week", run_id="01TEST"):
    return json.dumps({
        "runId": run_id,
        "status": status,
        "input": {"body": {"postedLimit": window}},
        "output": output if output is not None else [],
    }, ensure_ascii=False)


def test_reduces_completed_run_to_envelope_with_counts():
    payload = _run(output=[
        _raw("1", "Tel Aviv, Israel"),
        _raw("1", "Tel Aviv, Israel"),     # duplicate id
        _raw("2", "EMEA"),                 # not Israel
        _raw("3", "Haifa, Israel"),
    ])
    env = json.loads(reduce_run_payload(payload))
    assert env["window"] == "week"
    assert env["fetched"] == 4
    assert env["kept"] == 2
    assert env["dropped_duplicate"] == 1
    assert env["dropped_non_israel"] == 1
    assert [j["id"] for j in env["jobs"]] == ["1", "3"]


def test_kept_jobs_carry_only_the_seven_needed_fields():
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")])))
    assert set(env["jobs"][0]) == {"id", "title", "company", "description",
                                   "url", "posted_date", "location"}


def test_posted_date_survives_reduction():
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")])))
    assert env["jobs"][0]["posted_date"] == "2026-07-27T01:47:21.000Z"


def test_descriptions_are_kept_in_full():
    long_desc = "Requirements: " + ("Python and SQL. " * 500)
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Israel", description=long_desc)])))
    assert env["jobs"][0]["description"] == long_desc


def test_window_missing_is_null_not_fatal():
    payload = json.dumps({"runId": "01TEST", "status": "COMPLETED", "input": {},
                          "output": [_raw("1", "Israel")]})
    env = json.loads(reduce_run_payload(payload))
    assert env["window"] is None
    assert env["kept"] == 1


def test_running_run_passes_through():
    assert reduce_run_payload(_run(status="RUNNING", output=None)) is None


def test_failed_run_passes_through():
    assert reduce_run_payload(_run(status="FAILED")) is None


def test_unparseable_text_passes_through():
    assert reduce_run_payload("not json at all") is None


def test_missing_output_list_passes_through():
    assert reduce_run_payload(json.dumps({"status": "COMPLETED"})) is None


def test_malformed_items_pass_through_rather_than_raise():
    # normalize_posting requires id/title/linkedinUrl; a junk item must not
    # blow up the run — it must fall back to the raw payload.
    payload = _run(output=[{"nonsense": True}])
    assert reduce_run_payload(payload) is None


def test_dict_tool_response_is_also_accepted():
    payload = json.loads(_run(output=[_raw("1", "Israel")]))
    env = json.loads(reduce_run_payload(payload))
    assert env["kept"] == 1


def test_real_scrape_shrinks_hard_and_preserves_descriptions():
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = _run(output=items)
    reduced = reduce_run_payload(payload)
    env = json.loads(reduced)

    assert env["fetched"] == 99
    assert env["kept"] == 47
    assert env["dropped_duplicate"] == 17
    assert env["dropped_non_israel"] == 35
    # the whole point: a large majority of the payload is gone
    assert len(reduced) < len(payload) * 0.25
    # ...and none of it came out of the descriptions
    by_id = {str(i["id"]): i.get("descriptionText", "") for i in items}
    for job in env["jobs"]:
        assert job["description"] == by_id[job["id"]]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reduce.py -v`
Expected: FAIL — `ImportError: cannot import name 'reduce_run_payload' from 'tooling'`

- [ ] **Step 3: Implement the reducer**

In `src/tooling.py`, add `import json` and `import sys` to the existing imports at the top, then append:

```python
def _window(run):
    """The posting-age window Monid echoes back from the input that actually ran."""
    body = (run.get("input") or {}).get("body") or {}
    return body.get("postedLimit")


def reduce_run_payload(tool_response):
    """Reduce a `monid_get_run` payload to the jobs the agent actually needs.

    Returns the reduced envelope as JSON text, or None meaning "pass the original
    through untouched". None is returned for every case this cannot fully vouch
    for: a non-COMPLETED run (the agent is still polling, or needs to see a real
    error), an unparseable or unexpected shape, or a reducer failure. A failed
    optimisation must degrade to today's behaviour, never to a silent empty result.
    """
    if isinstance(tool_response, dict):
        run = tool_response
    else:
        try:
            run = json.loads(tool_response)
        except (TypeError, ValueError):
            return None

    if not isinstance(run, dict) or run.get("status") != "COMPLETED":
        return None

    items = run.get("output")
    if not isinstance(items, list):
        return None

    try:
        jobs = clean_jobs(items)
    except Exception as exc:                      # noqa: BLE001 - degrade, never crash the run
        print(f"[reduce] clean_jobs failed ({exc!r}); passing raw output through",
              file=sys.stderr)
        return None

    fetched = len(items)
    unique = len({str(i.get("id")) for i in items if isinstance(i, dict)})
    kept = len(jobs)
    envelope = {
        "window": _window(run),
        "fetched": fetched,
        "kept": kept,
        "dropped_duplicate": fetched - unique,
        "dropped_non_israel": unique - kept,
        "jobs": jobs,
    }
    print(f"[reduce] run={run.get('runId')} window={envelope['window']} "
          f"fetched={fetched} kept={kept} "
          f"dropped_duplicate={envelope['dropped_duplicate']} "
          f"dropped_non_israel={envelope['dropped_non_israel']}", file=sys.stderr)
    return json.dumps(envelope, ensure_ascii=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reduce.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Run the whole suite for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all existing tests still pass (`clean_jobs` was not touched).

- [ ] **Step 6: Commit**

```bash
git add src/tooling.py tests/test_reduce.py
git commit -m "feat: deterministic reducer for monid_get_run payloads

Dedupe + Israel filter + field projection via the existing clean_jobs,
wrapped in an envelope carrying the window and drop counts. Descriptions
are kept in full. Returns None to signal pass-through for any payload it
cannot fully vouch for."
```

---

### Task 2: The `PostToolUse` hook callback

**Files:**
- Create: `src/hooks.py`
- Test: `tests/test_hooks.py` (create)

**Interfaces:**
- Consumes: `tooling.reduce_run_payload(tool_response) -> str | None` from Task 1.
- Produces: `hooks.reduce_monid_output(input, tool_use_id, context) -> dict` — an async callback matching the SDK's `HookCallback` signature. Task 3 registers this in `agent.py`.

**Note:** `hooks.py` deliberately does **not** import `claude_agent_sdk` — the callback is a plain async function returning a dict, so it stays testable without the SDK. Only `agent.py` imports `HookMatcher`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hooks.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hooks'`

- [ ] **Step 3: Implement the hook**

Create `src/hooks.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add src/hooks.py tests/test_hooks.py
git commit -m "feat: PostToolUse hook that reduces monid_get_run output

Returns updatedToolOutput as a bare content-block array (the shape the CLI
accepts, verified by spike), and {} to leave output untouched whenever the
reducer declines."
```

---

### Task 3: Wire the hook in and delete `filter_jobs`

**Files:**
- Modify: `src/agent.py` (imports; `WORKFLOW` steps 2-5 at `agent.py:123-166`; `build_options()` at `agent.py:186-210`)
- Modify: `src/tools.py` (delete `_filter_jobs_impl` at `tools.py:15-16` and the `filter_jobs` tool at `tools.py:33-36`; drop it from the `tools=[...]` list at `tools.py:49`)
- Modify: `tests/test_tools_import.py`

**Interfaces:**
- Consumes: `hooks.reduce_monid_output` from Task 2.
- Produces: nothing downstream — this is the wiring task.

- [ ] **Step 1: Write the failing test**

Replace `tests/test_tools_import.py` entirely:

```python
def test_tools_module_exposes_server():
    import tools
    assert hasattr(tools, "resume_tools")


def test_filter_jobs_tool_is_gone():
    # Filtering now happens in the PostToolUse hook, before the model sees the
    # payload. Re-adding this tool would reintroduce the round-trip that made
    # the agent re-emit the entire scrape as tool arguments.
    import tools
    assert not hasattr(tools, "filter_jobs")
    assert not hasattr(tools, "_filter_jobs_impl")


def test_agent_registers_the_reduction_hook():
    import agent
    opts = agent.build_options()
    matchers = opts.hooks["PostToolUse"]
    assert any(m.matcher == "mcp__monid__monid_get_run" for m in matchers)
    assert "mcp__resume_tools__filter_jobs" not in opts.allowed_tools
    # the raw payload still crosses the CLI<->Python pipe to reach the hook
    assert opts.max_buffer_size == 10 * 1024 * 1024
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools_import.py -v`
Expected: FAIL — `test_filter_jobs_tool_is_gone` fails on `hasattr(tools, "filter_jobs")`, and `test_agent_registers_the_reduction_hook` fails with `TypeError: 'NoneType' object is not subscriptable` (no hooks registered).

Note: `agent.py` calls `load_dotenv()` at import and `build_options()` reads `os.environ['MONID_API_KEY']`, so `.env` must contain `MONID_API_KEY` for this test to run. It makes no network call and spends nothing.

- [ ] **Step 3: Delete the `filter_jobs` tool**

In `src/tools.py`, delete these two blocks:

```python
def _filter_jobs_impl(raw_items):
    return tooling.clean_jobs(raw_items)
```

```python
@tool("filter_jobs", "Normalize raw scraped job postings, dedupe by id, and keep "
      "only Israel-located postings.", {"raw_items": list})
async def filter_jobs(args: dict) -> dict:
    return _json_result(_filter_jobs_impl(args["raw_items"]))
```

and change the server registration to:

```python
resume_tools = create_sdk_mcp_server(
    name="resume_tools",
    version="1.0.0",
    tools=[get_resume, write_resume],
)
```

Leave the `import config` and `import tooling` lines alone — `_get_resume_impl` and `_write_resume_impl` still use both.

- [ ] **Step 4: Register the hook in `agent.py`**

Add `hooks` to the imports near the top (beside `import config`):

```python
import config
import hooks
from tools import resume_tools
```

Add `HookMatcher` to the `claude_agent_sdk` import block:

```python
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    query,
)
```

In `build_options()`, remove the `"mcp__resume_tools__filter_jobs",` line from `allowed_tools`, and add the `hooks` argument after `max_buffer_size`:

```python
        max_buffer_size=10 * 1024 * 1024,  # 10MB
        # The raw harvestapi scrape (~1.1MB, ~284K tokens) never reaches the
        # model: this hook reduces it in-process to the jobs we actually need.
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="mcp__monid__monid_get_run",
                            hooks=[hooks.reduce_monid_output]),
            ],
        },
```

- [ ] **Step 5: Update the workflow instructions**

In `agent.py`, replace `WORKFLOW` steps 2, 3 and the opening of step 4. The current step 2 tail, step 3, and step 4 opener read:

```
   `monid_run` is asynchronous — after starting it, poll `monid_get_run` with the returned
   run id until the run reports completion, then take its output (a list of raw postings).

3. Call `filter_jobs` on that raw output. It normalizes postings, dedupes by id, and keeps
   only Israel-located ones — treat its return value as the job list to work through.

4. For EACH job in that filtered list:
```

Replace with:

```
   `monid_run` is asynchronous — after starting it, poll `monid_get_run` with the returned
   run id until the run reports completion.

3. The completed run's result arrives ALREADY REDUCED for you: postings are normalized,
   deduped by id, and filtered down to Israel-located roles only. It is an object with
   `window` (the posting-age window this search used), `fetched`, `kept`,
   `dropped_duplicate`, `dropped_non_israel`, and `jobs`. Work through `jobs`. Do not try
   to re-filter or re-dedupe it, and do not look for a `filter_jobs` tool — there is none.

4. For EACH job in `jobs`:
```

Then update step 5 so the summary uses the envelope's real counts:

```
5. When every job has been judged, report a final summary: the `window` the search covered,
   how many jobs were fetched vs kept (`fetched`, `kept`, `dropped_duplicate`,
   `dropped_non_israel`), how many résumés were written (and to which companies/titles),
   how many were skipped and why, and any corrections `write_resume` reported (e.g.
   stripped skills, repaired entry coverage).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools_import.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 7: Confirm no dangling references to `filter_jobs`**

Run: `git grep -n "filter_jobs" -- src/ tests/`
Expected: only the two guard assertions in `tests/test_tools_import.py` and the "there is none" line in `agent.py`'s workflow text. No call sites, no `allowed_tools` entry, no tool definition.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/agent.py src/tools.py tests/test_tools_import.py
git commit -m "refactor: reduce jobs in a hook; delete the filter_jobs round-trip

filter_jobs required the model to re-emit the entire ~284K-token scrape as
tool arguments - beyond any per-turn output limit, so it could not complete.
Filtering now happens in the PostToolUse hook before the model sees anything,
so the tool has nothing left to do. Workflow instructions updated to describe
the pre-reduced envelope."
```

---

### Task 4: First live agent run (spends money)

No new code. **Requires `base_cv.md` at the repo root, and `MONID_API_KEY` + `ANTHROPIC_API_KEY` in `.env`.** This closes out Task 7 of the R1 plan, which has been blocked on the payload problem.

⚠️ **Monid balance was $0.205 on 2026-07-28.** Recent week-window runs cost $0.157–$0.19 each (billing is per actual result: $0.0015 × `resultCount` + $0.001 flat). That is roughly **one run**. Top up first, or set `config.POSTED_LIMIT = "24h"` for a ~$0.03–0.07 run.

- [ ] **Step 1: Check the balance before spending**

Run: `.venv/Scripts/python.exe -c "import agent"` first to confirm the module imports cleanly, then check funds via `monid_balance` (free) — either through the agent or the direct JSON-RPC probe.
Expected: balance ≥ the cost of the window you are about to run.

- [ ] **Step 2: Run the agent**

Run: `.venv/Scripts/python.exe src/agent.py`
Expected: the agent calls `get_resume`, runs the pinned Monid search, polls `monid_get_run`, and — without any `filter_jobs` call — receives the reduced envelope, judges each job, and calls `write_resume` for strong matches.

- [ ] **Step 3: Verify the reduction actually happened**

Confirm in the run output:
- the `[reduce] run=… window=… fetched=… kept=…` line appears on stderr, with `kept` well below `fetched`
- the agent's `[tool result]` for `monid_get_run` shows the **envelope**, not raw harvestapi items (no `descriptionHtml`, no nested `company` object with logos)
- **no** `filter_jobs` tool call appears anywhere
- the agent's final summary reports the window and the real counts

- [ ] **Step 4: Verify behaviour is unchanged where it matters**

Confirm: written files are genuinely Israeli junior roles; senior/irrelevant jobs were skipped; every written résumé is truthful to `base_cv.md` (guards enforced inside `write_resume`); `Auto-corrected` banners appear where guards fired. Spot-check one written résumé against its job's description to confirm the full description reached the agent — tailoring quality is the canary for description truncation.

- [ ] **Step 5: Commit what you learned**

```bash
git commit --allow-empty -m "chore: first live agent run - <N> résumés; <observed fetched/kept, cost, tuning notes>"
```

**With this, the agent judges jobs from a payload it can actually receive, and the deterministic dedupe/Israel filter runs unconditionally instead of depending on the agent choosing to call a tool.**

---

## Self-Review

- **Spec coverage:** reducer with dedupe + Israel filter + field projection and descriptions kept in full (Task 1) ✓; envelope with `window`/`fetched`/`kept`/`dropped_duplicate`/`dropped_non_israel`, counts derived without changing `clean_jobs` (Task 1, Step 3) ✓; `window` read from `run["input"]["body"]["postedLimit"]` with a `null` fallback (Task 1 `_window` + `test_window_missing_is_null_not_fatal`) ✓; every error-handling case from the spec — non-terminal status, FAILED/BLOCKED, malformed, reducer raises, missing `postedLimit` — has a test (Task 1) ✓; hook returns `updatedToolOutput` as a content-block array (Task 2) ✓; `filter_jobs` deleted with no dangling refs (Task 3, Step 7) ✓; `max_buffer_size` retained and asserted (Task 3, Step 1) ✓; `posted_date` preservation tested (Task 1) ✓; `POSTED_LIMIT` untouched in `config.py`, so the week→24h knob still works ✓; live verification (Task 4) ✓. `geoIds`, `industryIds`, query consolidation, description compression, and the daily-phase seen-ID store are out of scope per the spec and appear in no task ✓.
- **Placeholder scan:** every code step carries complete, runnable content — no "add error handling", no "similar to Task N", no TBDs. The two judgment-based verifications (Task 4 Steps 3-4) are live-observation checklists with concrete things to look for, which is the honest form for a live run.
- **Type consistency:** `reduce_run_payload(tool_response) -> str | None` is defined in Task 1 and consumed identically in Task 2's `hooks.reduce_monid_output`, which checks `is None` for pass-through exactly as Task 1 specifies. `clean_jobs(list) -> list[dict]` is used with its existing unchanged signature. `hooks.reduce_monid_output` is registered in Task 3 under the name Task 2 defines. The envelope key names (`window`, `fetched`, `kept`, `dropped_duplicate`, `dropped_non_israel`, `jobs`) are identical in Task 1's implementation, Task 1's tests, Task 2's tests, and Task 3's workflow instructions.

---

## Task 4 outcome and Task 5 (added 2026-07-28, after the first live run)

**Task 4 ran and FAILED at its purpose.** The session completed (exit 0, 7 résumés written,
`24h` window), but **the reduction never ran**. Task 4's Step 3 checks are exactly what
caught it: no `[reduce]` line, and `descriptionHtml` present in the transcript.

Root cause, evidenced not inferred: **the CLI's MCP output-size guard runs before
PostToolUse hooks.** The hook received a 1,629-char `exceeds maximum allowed tokens ...
saved to <file>` stub instead of JSON, `json.loads` failed, `reduce_run_payload` returned
`None` **silently**, and the raw payload passed through. The agent then hand-parsed the
offloaded 787KB file: 216 `Grep` + 9 `Bash` + 3 sub-`Agent` calls, 256 tool calls, **$7.19**.

The step-3 fallback prose added during the final-review fix wave worked as written — the
agent recognised a raw run object and filtered it itself — which is why the run produced
correct output and looked successful. **The safety net masked the failure.**

### Task 5: the fix (commit `d35b09a`)

1. `config.MAX_MCP_OUTPUT_TOKENS = "500000"`, passed via `ClaudeAgentOptions.env`, so the
   full payload reaches the hook. `max_buffer_size` is a *different* limit; both are needed.
2. `disallowed_tools` — `allowed_tools` only PRE-APPROVES, it does not restrict. Without
   this, any future reduction failure degrades into another expensive hand-parse instead of
   failing loudly.
3. **Every** `return None` in `reduce_run_payload` now logs. The silence is what made a
   total feature failure invisible for a whole run.

**Verified end-to-end** against the real `agent.build_options()` on a completed run (free
re-read, no scrape): `[reduce] window=24h fetched=79 kept=27 dropped_duplicate=13
dropped_non_israel=39`, model saw only envelope keys, no forbidden tools used, no raw
leakage. 70/70 tests pass.

### Still outstanding

A **fresh live scrape** has not been run with the fix in place — the Monid balance
($0.0855) is below the ~$0.12 cost of a 24h run. Top up, then re-run Task 4's Steps 2-5.
The free completed-run re-read exercises the whole chain except the initial `monid_run`
and the judge/tailor loop.

### Process lesson

The original spike verified the hook contract using `monid_balance` — a tiny payload — and
the spec recorded the mechanism as "verified live". **A spike that does not exercise the
property under design (here, payload size) has not cleared the risk it was run to clear.**
