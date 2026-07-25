# Agent SDK Refactor (Phase R1) — Design

**Date**: 2026-07-25
**Status**: Draft, awaiting review
**Builds on**: the current deterministic pipeline (Monid harvestapi source + repair-not-drop guards)

## Problem

The pipeline is a deterministic Python orchestrator: `main.py` runs a fixed search → score → tailor → write loop and calls the Claude API directly (`client.messages.parse`) at the two judgment points. We want to reconstruct the workflow as **one autonomous Claude Agent SDK session** that drives the flow itself via tool calls and MCP integrations — removing the hand-rolled API calls and opening the door to more MCPs (Monid now, Canva later). This is **Phase R1**: refactor the *existing* job-search + tailoring flow only. Canva résumé editing is Phase R2 and explicitly out of scope here.

## Decisions locked (from the design Q&A)

- **Trigger:** the agent is **started manually and runs autonomously to completion (1A).** Fully-unattended scheduled runs (1B) come later.
- **Monid access:** via the **Monid MCP**, not the REST wrapper.
- **Scope:** **Phase R1 = existing flow only.** Canva is Phase R2.
- **Language:** **Python** Agent SDK (`claude-agent-sdk`).
- **Content source:** `base_cv.md` stays the résumé-content source for R1 (the Canva-vs-file question is deferred with Phase R2).

## Spike result (Monid MCP) — the biggest risk, cleared

Verified live against `https://mcp.monid.ai/v1`:
- Authenticates with the **API key as a bearer token** (`401` without, `200` with `Authorization: Bearer monid_live_…`). **No interactive OAuth.**
- **Stateless** — `tools/list` and calls work with just the bearer; no session handshake.
- Exposes `monid_discover`, `monid_inspect`, `monid_run`, `monid_get_run`, `monid_balance`, and resource/run-management tools.

So the Monid MCP is fully usable **headlessly** from an Agent SDK session with only `MONID_API_KEY` in the environment. This removes the auth uncertainty that would otherwise have reshaped the design.

## Architecture

A single entry point, `agent.py`, runs **one autonomous Agent SDK session per invocation**. The session is configured with:

- **Agent instructions** (system prompt / skill): the agent's role, the **relocated scoring rubric and CV-editor rules** (currently the `SYSTEM_PROMPT`s in `scoring.py`/`tailoring.py`), the **fixed search recipe** (role queries, Israel, entry-level, posted window, the pinned Monid provider/endpoint), and the required workflow.
- **Monid MCP** connected over streamable HTTP with the bearer header.
- **In-process deterministic tools** (the existing Python logic, exposed via the SDK's custom-tool mechanism): `get_resume`, `filter_jobs`, `write_resume`.
- **Filesystem read** for `base_cv.md` if needed (or served through `get_resume`).
- **Permissions** set so the registered MCP + custom tools auto-run (autonomous, 1A), with a bounded turn count.

The agent owns **judgment and orchestration**; the **deterministic core stays code** and is enforced at a single boundary.

## The enforcement boundary (the principle that keeps our guarantees)

An agent is free-wheeling, so truthfulness is **not** delegated to it. The `write_resume` tool is a deterministic gate. Given a job, the agent's fit judgment, and the tailored fields, it runs — in code, every time:

1. the **relevance gate** (refuse if `fit_score < FIT_THRESHOLD` or not junior-friendly),
2. `strip_invented_skills` (alias/boundary-aware),
3. `repair_entry_coverage` (rebuild missing jobs from the base CV),
4. `render` the markdown, and writes it to `output/`,

then returns what it corrected or why it refused. The agent **cannot** produce an unchecked or untruthful résumé — the guards and threshold hold by construction, exactly as they do today.

## Tools (the deterministic core, as an in-process MCP)

- **`get_resume() -> dict`** — returns the parsed `base_cv.md`: preamble, sections, and the **indexed** Work Experience / Project entries (index + anchor + bullets) plus skills and summary. This is how the agent tailors *by index* (mirroring today's `build_tailoring_prompt`).
- **`filter_jobs(raw_items: list) -> list`** — wraps `normalize_posting` + the Israel-location filter + within-run dedup. Input: raw `monid_run` output items; output: clean job dicts. Stays deterministic because the source leaks EMEA/MENA.
- **`write_resume(job, fit_score, reason, match_kind, summary, skills, experience, projects) -> dict`** — the enforcement boundary above. Returns `{path, corrections[], rejected, reason}`.
- **Monid MCP tools** — `monid_run` (against the pinned `apify` / `/harvestapi/linkedin-job-search`) → `monid_get_run` to poll; `monid_balance` optionally to check funds before spending.

The agent never calls the guards directly — `write_resume` does. No separate guard tool, no way to skip them.

## Flow of one run

1. Agent calls `get_resume` to load the candidate's indexed résumé.
2. Agent calls **`monid_run`** with the pinned search recipe → polls `monid_get_run` until complete.
3. Agent calls **`filter_jobs`** on the raw results → Israel-only, deduped list.
4. For each job: the agent **judges fit itself** (replacing `scoring.py`) and, for good ones, drafts the tailored fields **by entry index** (replacing `tailoring.py`).
5. Agent calls **`write_resume`** → gate + guards + render + write.
6. Agent reports a run summary (written, skipped, corrected).

## Module changes (native, no duplication, no dead code)

| Current | Fate |
|---|---|
| `main.py` (deterministic loop) | **deleted** → replaced by `agent.py` |
| `scoring.py` (`score_job` + `messages.parse`) | **deleted**; its rubric prompt **relocated** into agent instructions |
| `tailoring.py` `tailor_cv` + `messages.parse` + prompts | **deleted / relocated**; the **guards stay** |
| `monid.py` (REST transport) + `jobs.fetch_jobs`/`build_harvestapi_input` | **deleted** → Monid MCP |
| `strip_invented_skills`, `repair_entry_coverage`, alias helpers | **kept**, called inside `write_resume` |
| Israel-filter + dedup + `normalize_posting` + `JobPosting` | **kept**, exposed via `filter_jobs` |
| `resume.py` (parse `base_cv.md`), `render.py` (markdown) | **kept**, used by `get_resume`/`write_resume` |
| `JobScore` / `TailoredCV` / `TailoredEntry` models | **kept** as tool input schemas |
| **new** | `agent.py`, `tools.py` (thin `@tool` adapters over the kept logic), the agent instructions/skill |
| `config.py` | **kept, slimmed** (queries, filters, threshold, pinned Monid provider/endpoint, MCP config) |

Deleted pieces have no remaining callers; nothing runs old-and-new in parallel.

## Search parameters (pinned, deterministic)

The role queries, `LOCATION`, `EXPERIENCE_LEVELS`, `POSTED_LIMIT`, `MAX_ITEMS_PER_QUERY`, and the pinned `MONID_PROVIDER="apify"` / `MONID_ENDPOINT="/harvestapi/linkedin-job-search"` stay in `config.py` and are stated verbatim in the agent instructions, so the agent's `monid_run` call is reproducible rather than improvised.

## Auth & dependencies

- `MONID_API_KEY` (Monid MCP bearer) and `ANTHROPIC_API_KEY` (the SDK's model) both stay in `.env`.
- Add `claude-agent-sdk`; remove direct `anthropic` usage and the Monid REST path (`monid.py`, and `requests` if unused elsewhere).

## Testing

- The **deterministic tools stay fully unit-tested**: `filter_jobs`, `get_resume`, the guards, `render`, and `write_resume` (which is testable directly with a dirty `TailoredCV`, no agent) — most existing tests carry over.
- The **agent flow** gets a **smoke/eval test** (a bounded session against a stubbed Monid MCP, or a small live check), not per-step asserts. This is the deliberate tradeoff of moving orchestration into the agent.

## Out of scope (Phase R1)

Canva résumé editing (Phase R2); unattended scheduling (1B); reading résumé content from Canva (deferred Q4); any change to the scoring rubric or CV-editor rules beyond relocating them.

## Risks / tradeoffs

- **Nondeterminism:** the agent decides how many tool calls to make, so cost and step count are less predictable than the fixed loop. Bounded by a max-turns cap, the pinned search recipe, and restricted permissions.
- **Search-input rigidity:** with Monid as an MCP the agent issues the `monid_run` call, so the search input is pinned in instructions rather than hardcoded in a function — explicit, but softer than before.
- **SDK specifics:** the exact `claude-agent-sdk` API (MCP-server config schema, custom-tool decorator, permission options) is verified against the installed version in the first implementation task, not assumed here.
- **Testability:** agent orchestration is no longer unit-testable; the deterministic core is why the guards/filters stay code.
