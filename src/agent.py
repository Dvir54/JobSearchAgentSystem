"""Entry point: run one autonomous job-search + tailoring session.

The agent owns judgment and orchestration (job search, fit scoring, CV
tailoring). The deterministic core splits across three enforcement points:
the Israel filter/dedup happens in the PostToolUse hook before the agent
ever sees a job (see hooks.py / tooling.reduce_run_payload); `prepare_resume`
(tools.py / tooling.py) computes a guard-checked Canva edit plan but writes
nothing itself; and because the agent makes the Canva calls itself, and
could send something other than what `prepare_resume` returned, the
PreToolUse hook on `perform-editing-operations` (hooks.guard_canva_write) is
what actually inspects and denies an untruthful write. Both halves are
required for the guarantee to hold.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

import config
import hooks
from tools import resume_tools

# Loaded at import time (not just inside main()) so that MONID_API_KEY is already
# in os.environ by the time build_options() runs — including when build_options()
# is exercised directly (e.g. the no-spend dry check: `import agent; agent.build_options()`).
load_dotenv()

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

# --- Scoring rubric, relocated verbatim from scoring.py's SYSTEM_PROMPT ---
SCORING_RUBRIC = """You evaluate job postings for a junior software engineer in Israel who
is finishing a Computer Science degree. Your job is to decide whether this is a role the
candidate should realistically apply to as a junior — NOT whether they already tick every
box on the posting's wish list. Most postings list aspirational requirements; a strong
junior candidate rarely meets all of them on paper, and that is expected.

Decide three things:

1. is_junior_friendly: would this role realistically consider a junior or a recent
   graduate? The requirements text always wins over the title — judge from what is
   actually asked for, never from the job title alone. A "Junior" title demanding 5
   years is not junior-friendly; a plain title with entry-level requirements is. The
   same cuts the other way: "Senior"/"Staff"/"Lead"/"Principal"/"Experienced" in the
   title is a prompt to read the requirements carefully, not a disqualification by
   itself — a title like that IS junior-friendly when its stated must-haves are ones a
   recent graduate could meet (e.g. a BSc plus familiarity with a language, with
   everything else — frameworks, specific tools, years of experience — listed only as
   an advantage). Treat a hard requirement of 3+ years of professional experience as
   NOT junior-friendly regardless of title; that rule does not bend. If it is not
   junior-friendly, the candidate should not apply, so score fit low regardless of
   skills overlap. When the title and the requirements pull in different directions,
   say so in the reason and name the specific requirement (or its absence) that
   decided the call.

2. fit_score (0-100): for a JUNIOR-FRIENDLY role, how good a target is this for THIS
   candidate? Score generously toward "should apply," in two ways:
   - The candidate already meets the core requirements (their languages, projects, or
     internship line up with what the role centers on).
   - OR the role is a junior-level entry point where the candidate lacks some specific
     tools, BUT the gap is learnable on the job given their CS foundation, programming
     fundamentals, and adjacent skills. A missing framework, cloud tool, or library that a
     motivated junior could pick up in weeks is NOT a reason to score low.
   Score LOW only when the role is a genuine mismatch: it needs deep specialized expertise
   a junior cannot pick up quickly (e.g. years of chip physical-design, advanced ML
   research, senior security work), it is in a different discipline, or it is not
   junior-friendly. A score of 70+ means "this is a sensible junior application" — whether
   a direct fit or a reasonable learnable stretch. Do not reserve 70+ for perfect matches.

3. match_kind: "direct" if the candidate substantially meets the role's core requirements
   already; "stretch" if it is a junior-friendly role they should apply to but would be
   learning meaningful parts on the job. (This label only matters when the role passes, but
   always provide it.)

Give a one-sentence reason citing the specific requirement or gap that drove your decision,
and — for a stretch — what makes the gap learnable for a junior."""

# --- CV-editor rules, relocated verbatim from tailoring.py's SYSTEM_PROMPT ---
CV_EDITOR_RULES = """You are a CV editor. You adapt one candidate's existing CV to one
specific job posting. You are given the candidate's summary, skills, and their
Work Experience and Project entries, each entry labelled with an index like [0], [1].

Work through this before writing anything:
1. Extract the posting's real requirements — the 5-8 skills and technologies that
   actually matter, and the role's core focus. Ignore boilerplate.
2. Build an evidence matrix. For each requirement, find the candidate's proof in the
   CV: what proves it outright, what partially proves it, and what is missing entirely.
3. Rewrite only what the evidence supports. Leave the gaps as gaps.

Produce:
- summary: 2-3 sentences positioning the candidate for this specific role, built only
  from evidence in the CV.
- skills: the candidate's skills, ordered so the ones this posting names come first.
  Include only skills already in the CV.
- experience: every Work Experience entry, referenced by its index. For each entry,
  reword the bullets it already has to surface the skills this posting cares about.
  Return exactly the same number of bullets the entry already has, in the same
  one-to-one correspondence — reword each, but never add a new bullet, never split one
  bullet into two, never merge two into one. Reference every index exactly once — never
  drop, add, or duplicate an entry.
- projects: every Project entry, referenced by its index. Projects have no bullets to
  rewrite; return an empty bullet list for each. Reference every index exactly once.

Hard constraints on truth:
- Every claim must be one the candidate could defend in an interview.
- Never add a technology, tool, employer, project, or metric that is not already in
  the CV. Do not imply, hint, or use adjacent phrasing to suggest one.
- Never invent numbers. Reuse the candidate's real metrics, or omit metrics entirely.
- Never add a bullet point. A bullet that is not a rewording of an existing bullet is
  invented experience, even when it sounds plausible. Keep each entry's bullet count.

Hard constraints on sounding natural — these matter as much as accuracy:
- Do not copy the posting's phrasing verbatim. Use the ordinary vocabulary of the field.
- Never force a keyword into a bullet where the underlying work did not involve it.
- No hype or buzzwords: no "leverage", "synergy", "spearheaded", "passionate",
  "results-driven", "cutting-edge", "ninja", "rockstar".
- Keep the candidate's own voice. The result must read like they rewrote it themselves."""

# --- The pinned search recipe (verbatim values, not improvised) ---
_SEARCH_RECIPE_BODY = {
    "jobTitles": config.ROLE_QUERIES,
    "locations": [config.LOCATION],
    "experienceLevel": config.EXPERIENCE_LEVELS,
    "maxItems": config.MAX_ITEMS_PER_QUERY,
    "postedLimit": config.POSTED_LIMIT,
    "sortBy": "date",
}

WORKFLOW = f"""You are running one autonomous job-search-and-tailoring session end to end.
Follow this workflow exactly:

1. Call `get_resume` to load the candidate's résumé: summary, skills, and the indexed
   Work Experience / Project entries. Remember these indices — you will reference entries
   by index only, never by rewriting their anchors.

2. Call `monid_run` with EXACTLY this pinned search recipe (do not change any field):
   - provider: {config.MONID_PROVIDER!r}
   - endpoint: {config.MONID_ENDPOINT!r}
   - input: {_SEARCH_RECIPE_BODY!r}
   `monid_run` is asynchronous — after starting it, poll `monid_get_run` with the returned
   run id until the run reports completion.

3. The completed run's result arrives ALREADY REDUCED for you: postings are normalized,
   deduped by id, and filtered down to Israel-located roles only. It is an object with
   `status` and `runId` (confirming which run this is), `window` (the posting-age window
   this search used), `fetched`, `kept`, `dropped_duplicate`, `dropped_non_israel`, and
   `jobs`. Receiving this object means the run has already reached COMPLETED — stop
   polling as soon as you see it. Work through `jobs`. Do not try to re-filter or
   re-dedupe it, and do not look for a `filter_jobs` tool — there is none. If instead you
   ever receive a raw run object that has an `output` list but no `jobs` field, the
   reduction step did not run for that result: in that case, and only that case, work
   through `output` yourself — dedupe by `id` (first occurrence wins) and keep only
   postings whose location mentions Israel — since nothing upstream has done it for you.

3b. Create this run's Canva folder with `create-folder`, named
    "{config.CANVA_FOLDER_PREFIX} — <today's date, YYYY-MM-DD>". Keep its id.

4. For EACH job in `jobs`:
   a. Judge fit yourself using the rubric below: `is_junior_friendly`, `fit_score`
      (0-100), `match_kind` ("direct" or "stretch"), and a one-sentence `reason`.
   b. If the job is NOT junior-friendly or `fit_score` < {config.FIT_THRESHOLD},
      skip it — no Canva copy, no PDF. Just note it as skipped.
   c. Otherwise draft the tailored fields using the CV-editor rules below, then call
      `prepare_resume` with `job` as an object with ONLY `id`, `title`, `company`, and
      `url` (never the posting's description text — `prepare_resume` doesn't use it),
      your score, and those fields. If it returns `rejected: true`, note the reason
      and move on — do NOT touch Canva. On success it returns both `edits` (for your
      own records) and `operations` — the exact Canva operations to send.
   d. Call `copy-design` with design_id {config.CANVA_TEMPLATE_DESIGN_ID!r}. Keep the
      new design's id and its edit URL.
   e. Call `start-editing-transaction` on that new design. Keep the transaction id.
   f. Call `perform-editing-operations` passing the `operations` array `prepare_resume`
      returned VERBATIM — together with the transaction id and `page_index: 1`. Do NOT
      construct, reorder, or edit the operations yourself; send exactly what was
      returned.
   g. The response is `{{"ok": bool, "overflow": {{element_id: px}}, "failed_operations":
      [...]}}`. The overflow check is done for you in code — do NOT try to compare
      element heights yourself.
      - If the response has NO `ok` field at all, the overflow check did not run (the
        payload could not be read) — call `cancel-editing-transaction`, record the job
        as skipped, and move on. Do NOT commit.
      - If `ok` is true, continue to (h).
      - If `ok` is false there are two distinct cases, and they call for different
        responses:
        - `overflow` is non-empty: call `cancel-editing-transaction`. Shorten the
          named blocks yourself by roughly the reported pixel overrun, then call
          `prepare_resume` again with the job, your score, and the shortened tailored
          fields — this re-applies every guard (relevance, invented skills, entry
          coverage, length budget) to the redraft, exactly as the first attempt. Take
          the `operations` it returns and resume at step (e) using the SAME copied
          design from step (d) — do not call `copy-design` again. Retry at most
          {config.MAX_REDRAFT_ATTEMPTS} times total. After that, skip the job and
          record it.
        - `failed_operations` is non-empty: the edit itself was rejected for a reason
          unrelated to overflow. There is no block to shorten and no point retrying the
          same operations — call `cancel-editing-transaction`, record the failure, and
          move on to the next job.
      NEVER commit a design whose response was not `ok`.
   h. Call `commit-editing-transaction`.
   i. Call `get-export-formats` for this design first — `export-design` requires it and
      will not accept a guessed format. Then call `export-design` for PDF and poll until
      it completes.
   j. Call `save_pdf` with the export URL, the company, the title and the job id.
      You have no other way to write a file.
   k. Call `move-item-to-folder` to file the design in this run's folder.

   If `perform-editing-operations` is denied by the guard, call
   `cancel-editing-transaction` and skip the job — do not retry the same text.

5. Once every job has been judged, call `write_index` **once**, with one entry per
   résumé written — `company`, `title`, `fit_score`, `match_kind`, `reason`, `apply_url`,
   `pdf_filename`, `canva_edit_url`, `corrections` — plus the search `window` and the
   count of jobs judged but skipped. `write_index` is the only way to create that file.
   Then report the same summary in your final message.

--- Fit-scoring rubric (judgment point one — replaces scoring.py) ---
{SCORING_RUBRIC}

--- CV-editor rules (judgment point two — replaces tailoring.py) ---
{CV_EDITOR_RULES}
"""

INSTRUCTIONS = WORKFLOW


def build_options() -> "ClaudeAgentOptions":
    """Configure the one-shot autonomous session.

    - Monid MCP over remote HTTP with the bearer key (per docs/agent-sdk-reference.md
      section 4), plus the in-process `resume_tools` server (section 3) and the
      remote Canva MCP the agent uses to build the tailored PDF.
    - `allowed_tools` lists exactly the Monid tools the workflow needs (run/get_run,
      plus balance as a cheap sanity check), the four resume tools, and the Canva
      tools the copy/edit/export sequence in the workflow calls.
    - `permission_mode="dontAsk"`: auto-runs anything already in `allowed_tools` without
      an interactive prompt — the safer non-prompting mode called out in the reference
      doc, since this session runs headlessly with no human available to answer a
      prompt anyway. It does NOT restrict what the agent can use: `allowed_tools` only
      PRE-APPROVES those tools, it does not deny the rest. Every built-in tool (Bash,
      Grep, Read, Agent, ...) is still available unless separately denied. This was the
      origin of a real defect: when the reduction hook failed silently, the agent used
      Bash/Grep/Agent to hand-parse the raw payload instead — 256 tool calls, $7.19 in
      one run. `disallowed_tools` is what actually restricts them.
      NOTE: the SDK's own docstring (claude_agent_sdk/types.py:1817) claims dontAsk will
      "deny if not pre-approved". That is false for built-in tools — measured, not
      assumed. Do not revert this comment to match the SDK docs.
    - `max_turns` is generous: the reference doc's spike showed the CLI defers MCP tool
      schemas behind its own `ToolSearch`, burning extra turns before real tool calls even
      start, and this session judges/tailors many jobs in a loop.
    """
    return ClaudeAgentOptions(
        system_prompt=INSTRUCTIONS,
        mcp_servers={
            "monid": {
                "type": "http",
                "url": config.MONID_MCP_URL,
                "headers": {"Authorization": f"Bearer {os.environ['MONID_API_KEY']}"},
            },
            "resume_tools": resume_tools,
            "canva": {"type": "http", "url": "https://mcp.canva.com/mcp"},
        },
        allowed_tools=[
            "mcp__monid__monid_run",
            "mcp__monid__monid_get_run",
            "mcp__monid__monid_balance",
            "mcp__resume_tools__get_resume",
            "mcp__resume_tools__prepare_resume",
            "mcp__resume_tools__save_pdf",
            "mcp__resume_tools__write_index",
            "mcp__canva__copy-design",
            "mcp__canva__start-editing-transaction",
            "mcp__canva__perform-editing-operations",
            "mcp__canva__commit-editing-transaction",
            "mcp__canva__cancel-editing-transaction",
            "mcp__canva__export-design",
            "mcp__canva__get-export-formats",
            "mcp__canva__create-folder",
            "mcp__canva__move-item-to-folder",
        ],
        # allowed_tools only PRE-APPROVES; it does not restrict. Without this the
        # agent keeps every built-in tool and can route around a failed reduction by
        # grepping the offloaded payload by hand — which cost $7.19 in one run.
        disallowed_tools=[
            "Bash", "Grep", "Glob", "Read", "Write", "Edit", "NotebookEdit",
            "Agent", "Task", "PowerShell", "WebFetch", "WebSearch",
        ],
        env={"MAX_MCP_OUTPUT_TOKENS": config.MAX_MCP_OUTPUT_TOKENS},
        permission_mode="dontAsk",
        max_turns=200,
        # The Monid harvestapi result for a 'week' window (100+ jobs with full
        # text+HTML descriptions) can exceed the SDK's 1MB default message buffer;
        # raise it so the completed monid_get_run result fits through the pipe.
        max_buffer_size=10 * 1024 * 1024,  # 10MB
        # The raw harvestapi scrape (~1.1MB, ~284K tokens) never reaches the
        # model: this hook reduces it in-process to the jobs we actually need.
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="mcp__monid__monid_get_run",
                            hooks=[hooks.reduce_monid_output]),
                # start-editing-transaction and perform-editing-operations each
                # return the WHOLE design (~13-16KB); the reducer keeps the geometry
                # in-process and returns an overflow verdict. commit/cancel are NOT
                # reduced — their (small) responses pass through untouched; these two
                # matchers exist only so the hook can release the retained geometry
                # for that transaction_id.
                HookMatcher(matcher="mcp__canva__start-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__perform-editing-operations",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__commit-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__cancel-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
            ],
            "PreToolUse": [
                HookMatcher(matcher="mcp__canva__perform-editing-operations",
                            hooks=[hooks.guard_canva_write]),
            ],
        },
    )


GOAL_PROMPT = (
    "Run the full job-search-and-tailoring workflow described in your instructions, "
    "start to finish, then report the final summary."
)


async def main() -> int:
    load_dotenv()

    final_summary = None
    async for message in query(prompt=GOAL_PROMPT, options=build_options()):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[tool call] {block.name} {block.input}")
                elif isinstance(block, ToolResultBlock):
                    print(f"[tool result] {block.tool_use_id}: {block.content}")
                elif isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            final_summary = message.result
            print(f"\n[session finished] subtype={message.subtype} "
                  f"is_error={message.is_error} cost=${message.total_cost_usd}")

    print("\n=== Final summary ===")
    print(final_summary or "(no final summary returned)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
