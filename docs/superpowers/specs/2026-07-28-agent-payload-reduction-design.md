# Agent Payload Reduction — Design

**Date**: 2026-07-28
**Status**: Draft, awaiting review
**Builds on**: the Agent SDK refactor (Phase R1) — `agent.py` + `tools.py` + `tooling.py`, Monid over MCP
**Branch**: `agent-sdk-refactor-impl` (Task 7 of the R1 plan, the first live run, is still open)

## Problem

The agent fetches jobs through the Monid MCP. `monid_get_run` returns the entire
harvestapi scrape — measured at **1,134,704 chars (~284K tokens)** on the real run
`01KYHZWB0YH5YB6N39XC10BFRZ` (2026-07-27, 111 results, $0.1675). That whole payload
lands in the model's context. The agent is then instructed to pass it to
`filter_jobs(raw_items: list)` (`tools.py:34`), which requires the model to **re-emit
~284K tokens as tool-call arguments**.

That second step is not merely wasteful — it is **not physically possible**. ~284K output
tokens in one tool call is far beyond any per-turn output limit, so the agent must chunk
across turns or silently drop jobs to fit. This is the most likely reason `output/` is
empty after the run that forced `max_buffer_size` to 10MB (`a2493d0`).

The structural error: **the model is used as a data pipe.** Data leaves a tool, enters the
model, and the model must retype a megabyte to hand it to the next tool. All of it is
avoidable — of 111 fetched records, **64 are discarded** (25 duplicate ids, 39 non-Israeli)
*after* the model has already paid to read them.

### Where the bytes actually go (measured on the same run)

| Field | Share of raw payload | Used by our code |
|---|---|---|
| `company` (logos, background covers, follower counts, company description) | **35.8%** | only `company.name` |
| `descriptionHtml` | **25.1%** | **nothing** |
| `descriptionText` | 22.1% | **yes — the signal** |
| `location`, `applyMethod`, `query`, `hiringTeam`, `benefits`, `salary`, `views`, `industries`, `_meta`, … | 17.0% | only `location` |

**Descriptions are not the problem.** All 47 Israeli descriptions together are 111 KB —
about 10% of the payload. They are already clean: whitespace normalisation saves 0.1%, and
boilerplate headings appear in only 21 instances across 47 postings. Compressing them buys
almost nothing and risks cutting the requirements text that fit-scoring depends on.
**Descriptions are kept in full.** The waste is the other ~89%.

## Constraint: the MCP stays

Keeping Monid on MCP is a locked decision. Two consequences, both verified:

- The harvestapi endpoint schema has **no field-projection option**
  (`jobTitles, locations, maxItems, company, workplaceType, employmentType,
  experienceLevel, salary, under10Applicants, easyApply, postedLimit, industryIds,
  sortBy, geoIds, page, cookie, userAgent, proxy`). The fat shape cannot be suppressed at
  the source.
- `monid_get_run` accepts only `runId` — no projection, no pagination there either.

So the reduction must happen **after the MCP returns and before the model sees it**.

## Spike result (PostToolUse hook) — the design risk, cleared

Verified live against the installed `claude-agent-sdk` 0.2.128 and the real Monid MCP:

| Question | Result |
|---|---|
| Does a `PostToolUse` hook fire on an MCP tool? | **Yes** (fired on `mcp__monid__monid_balance`) |
| Can it replace what the model sees? | **Yes** — the model reported the injected sentinel and never saw the real value |
| Which output field? | **`updatedToolOutput`** (`updatedMCPToolOutput` not needed) |
| Required shape | **`[{"type": "text", "text": ...}]`** — a bare content-block array. `{"content": [...]}` crashes the CLI with `e.reduce is not a function` |
| What the hook receives | `tool_response` as a **`str`** of JSON — the hook must `json.loads` it |
| `monid_get_run` payload shape | `{runId, status, input, cost, resultCount, …, output: [ …items… ]}` — jobs live under `output` |

Type source: `claude_agent_sdk/types.py:423-436`, `updatedToolOutput` — *"Replaces the tool
output before it is sent to the model."* Wiring: `ClaudeAgentOptions.hooks:
dict[HookEvent, list[HookMatcher]]` (`types.py:1947`).

### CORRECTION (2026-07-28, after the first live run)

**The spike above was run against `monid_balance`, whose payload is tiny. It therefore
verified the hook contract but never exercised the behaviour that actually mattered:
what happens at size.** The first live run exposed two facts that invalidate the naive
reading of the table above.

**1. The CLI's MCP output-size guard runs BEFORE PostToolUse hooks.** For the real
774,006-char `monid_get_run` result, the hook did not receive JSON. It received a
1,629-char stub:

```
Error: result (774,006 characters) exceeds maximum allowed tokens. Output has been
saved to ...\tool-results\mcp-monid-monid_get_run-1785256341558.txt.
Format: JSON with schema: {runId: stri...
```

`json.loads` failed, `reduce_run_payload` returned `None`, the hook passed the payload
through, and the agent parsed the offloaded 787KB file by hand — 216 `Grep` + 9 `Bash` +
3 sub-`Agent` calls, 256 tool calls, **$7.19**. The reduction never ran.

**Required:** `env={"MAX_MCP_OUTPUT_TOKENS": config.MAX_MCP_OUTPUT_TOKENS}` on
`ClaudeAgentOptions`. Verified: with the cap raised, the hook receives all 773,981 chars
and reduces 79 fetched → 27 kept. `max_buffer_size` is a *separate* limit — both are
needed.

**2. `allowed_tools` does not restrict built-in tools; it only pre-approves them.** The
agent retained `Bash`, `Grep`, `Glob`, `Read`, `PowerShell`, and `Agent`, which is how it
routed around the failed reduction. **Required:** `disallowed_tools`
(`claude_agent_sdk/types.py:1847`).

Note the SDK's own docstring for `permission_mode` (`types.py:1817`) states
`"dontAsk" — Don't prompt for permissions; deny if not pre-approved`. That is
**empirically false** for built-in tools, as the $7.19 run demonstrates. Do not "correct"
`agent.py`'s comment back to match the SDK docs.

**Process lesson:** a spike that does not exercise the property under design — here,
payload size — has not cleared the risk it was run to clear.

## Architecture

A `PostToolUse` hook matched to `mcp__monid__monid_get_run` intercepts the run result
**inside the Python process**, runs the deterministic reducer, and returns
`updatedToolOutput`. The model never sees the raw scrape.

```
agent: monid_run(pinned recipe)  --> MCP --> run id            (unchanged)
agent: monid_get_run(run_id)     --> MCP
                                      |
                                      |  1.13 MB raw
                                      v
                    +-----------------------------------+
                    |  PostToolUse hook (Python)        |
                    |  json.loads(tool_response)        |
                    |                                   |
                    |  status != COMPLETED?             |
                    |     pass through untouched -------+--> agent keeps polling
                    |                                   |
                    |  else reduce(run):                |
                    |    (1) shrink JOB COUNT           |
                    |        dedupe by id   111 -> 86   |
                    |        Israel filter   86 -> 47   |
                    |    (2) shrink PER-JOB DATA        |
                    |        keep 7 fields, incl.       |
                    |        description IN FULL        |
                    |                                   |
                    |  updatedToolOutput=[{type,text}]  |
                    +-----------------------------------+
                                      |  124 KB
                                      v
model sees 47 clean jobs, ~31K tok — descriptions intact
agent judges each --> write_resume                             (unchanged)
```

`filter_jobs` is **deleted**, not reshaped. Filtering happens before the model sees
anything, so there is nothing left to round-trip.

### Measured effect (real run `01KYHZWB0YH5YB6N39XC10BFRZ`)

```
what the model sees today   1,134,704 chars   ~283,676 tok
after hook reduction          124,269 chars   ~ 31,067 tok      89.0% reduction
111 fetched -> 25 dup dropped -> 86 unique -> 39 non-Israel dropped -> 47 kept
descriptions preserved IN FULL: 47/47
```

Total session traffic falls from **~598K tokens** (284K raw + 284K retype + 31K result) to
**~31K tokens** — a **95%** reduction. The payload also inverts from noise to signal:
descriptions were ~10% of the raw payload and are **89.3%** of what remains.

## The reducer (deterministic, reused)

`tooling.clean_jobs` (`tooling.py:49`) already does exactly the required work and is
already unit-tested. It is **reused unchanged**. Only a thin parse/serialise wrapper is new:

- **`reduce_run_payload(tool_response: str) -> str | None`** — parse the run JSON; if
  `status` is not `COMPLETED`, return `None` (signal "pass through"); otherwise call
  `clean_jobs(run["output"])`, wrap in the envelope below, and return the serialised text.
  SDK-free, so it stays unit-testable without an agent run.

`clean_jobs` keeps its current signature (`list -> list`) and its existing tests.
`reduce_run_payload` derives the envelope counts itself, without changing it:

```
items             = run["output"]
fetched           = len(items)
unique            = len({str(i["id"]) for i in items})
dropped_duplicate = fetched - unique
jobs              = clean_jobs(items)
kept              = len(jobs)
dropped_non_israel = unique - kept
window            = run["input"]["body"]["postedLimit"]
```

`window` is read from Monid's **echo of the input that actually ran** — verified present at
`input.body.postedLimit` (value `"week"` on the reference run) — not from `config`, so the
envelope reports the window the results really came from rather than what config currently
says.

Two reduction axes, matching the two costs:

1. **Job count** — dedupe by id (first wins) and keep only Israel-located postings.
2. **Per-job data** — project to the seven fields the downstream flow actually consumes:
   `id, title, company, description, url, posted_date, location`.

### Envelope

The hook returns a small object rather than a bare list:

```json
{ "window": "week", "fetched": 111, "kept": 47,
  "dropped_duplicate": 25, "dropped_non_israel": 39,
  "jobs": [ /* 47 jobs, descriptions intact */ ] }
```

Costs ~30 tokens. Buys: the agent sees the time span it is working in rather than inferring
it; its final summary reports real counts instead of guessing; and each run leaves a log
line for tuning the window.

## The time-span indicator

Two distinct things, both preserved:

- **The search window** — `config.POSTED_LIMIT` (`config.py:34`) flows into
  `_SEARCH_RECIPE_BODY` (`agent.py:120`) and is stated verbatim in the agent instructions.
  Monid echoes it back in the run's `input.body.postedLimit`. The hook sits *downstream* of
  the fetch and never touches the recipe, so **`"week"` → `"24h"` stays a one-line change in
  `config.py`**, as the existing comment there anticipates. The window is also surfaced to
  the agent as `envelope.window`.
- **Per-job freshness** — `posted_date` is one of the seven kept fields, populated **47/47**
  with a full timestamp (e.g. `2026-07-27T01:47:21.000Z`).

The source honours the window: of the 47 kept jobs, 19 were posted within 24h, 9 within
48h, 19 within 7 days, and **none older than 7 days**. The hook therefore does **not** apply
a redundant date filter.

The window and the reduction are independent levers that multiply:

| | jobs | model sees | Monid cost |
|---|---|---|---|
| week, today | 47 | ~284K tok | $0.1675 |
| week + hook | 47 | ~31K tok | $0.1675 |
| 24h + hook (projected) | 19 | ~11.3K tok | ~$0.03–0.07 |

Billing is per actual result, not the cap: `resultCount=111, billedUnits=111`, and
111 × $0.0015 + $0.001 flat = $0.1675 exactly.

## Module changes

| File | Change |
|---|---|
| `tooling.py` | **add** `reduce_run_payload()`; `clean_jobs` unchanged |
| `hooks.py` *(new)* | the `PostToolUse` callback: parse, pass-through-or-reduce, return `updatedToolOutput` |
| `agent.py` | register `hooks={"PostToolUse": [HookMatcher(matcher="mcp__monid__monid_get_run", …)]}`; drop `filter_jobs` from `allowed_tools`; remove workflow step 3 and state that results arrive already deduped and Israel-filtered |
| `tools.py` | **delete** the `filter_jobs` tool wrapper and `_filter_jobs_impl` |
| `config.py` | unchanged |
| `tests/` | unit-test `reduce_run_payload` against a saved real payload (COMPLETED, RUNNING, FAILED, malformed); existing `clean_jobs` tests carry over |

`max_buffer_size = 10 * 1024 * 1024` **stays.** The raw result still crosses the CLI↔Python
pipe to reach the hook; the reduction only shrinks what goes on to the model. Removing it
would re-break the run.

## Error handling

- **Non-terminal status** (`RUNNING`, `PENDING`) — pass through untouched. The agent polls
  `monid_get_run` repeatedly; mangling a poll response would break the loop. This is the
  most important correctness case.
- **`FAILED` / `BLOCKED` / `TIMED_OUT`** — pass through untouched so the agent sees the real
  error rather than an empty job list it might mistake for "no matches".
- **Malformed / unparseable `tool_response`** — pass through untouched and warn on stderr.
  The hook must never turn a data problem into a silent empty result.
- **Reducer raises** — catch, warn on stderr, pass through untouched. A failed optimisation
  must degrade to today's behaviour, not to a broken run.
- **Missing `input.body.postedLimit`** — not fatal. Set `window` to `null` and reduce as
  normal; a missing label is not a reason to ship 284K tokens.

The rule: **the hook only ever replaces output it fully understands.** Every other path is
transparent.

## Testing

- `reduce_run_payload` is unit-tested against the saved real 111-item payload: COMPLETED
  (asserts 47 kept, 25/39 drop counts, descriptions byte-identical to source), RUNNING,
  FAILED, and malformed input (all assert pass-through).
- The hook callback is tested for shape: returns
  `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": [{"type": "text", "text": …}]}}`.
- Live verification folds into the still-open Task 7 first run.

## Out of scope

Deferred, flagged only — each is a separate decision:

- **`geoIds`** (hard LinkedIn geo filter, would cut the 39 non-Israeli records at source and
  reduce the bill) and **`industryIds`** — these change *which jobs you see*, a product
  decision, not an efficiency one.
- **Role-query consolidation** — 25 of 111 records were duplicate ids from overlapping
  queries, and billing is per result per query.
- **Description compression** — measured as not worth the risk (see Problem).
- **The daily-agent work** — cross-run seen-ID store, dated output folders, scheduling.
- **Switching `POSTED_LIMIT` to `"24h"`** — the knob is preserved and verified; flipping it
  is the daily phase's call.

## Risks / tradeoffs

- **Hook contract is version-sensitive.** `updatedToolOutput` and its required shape were
  verified against `claude-agent-sdk` 0.2.128 by live spike, not documentation. An SDK
  upgrade could change it. Mitigated by the pass-through-on-anything-unexpected rule: if the
  replacement is rejected, the original output survives and the run degrades to today's
  behaviour rather than failing.
- **Filtering becomes invisible to the agent.** The agent no longer performs the filter step,
  so it cannot report on it from its own actions — hence the envelope carrying the counts.
- **Debuggability.** Raw results no longer appear in the transcript. The hook should log
  `fetched/kept/dropped` and the run id to stderr so a surprising result can be traced, and
  the raw payload remains re-readable for free via `monid_get_run` on the stored run.
- **Stronger guarantee, as a side effect.** Dedupe and the Israel filter currently depend on
  the agent choosing to call `filter_jobs` and passing the data faithfully. In the hook they
  are unconditional — the agent cannot skip, forget, or corrupt them. This extends the
  enforcement-boundary principle `write_resume` established to the read side.

## Operational note

**Monid balance at design time: $0.205.** Recent runs cost $0.157–$0.19 each, so that is
roughly **one more week-window run**. Top up before the live Task 7 verification, or run it
with `POSTED_LIMIT="24h"` (~$0.03–0.07).
