"""Entry point: run one autonomous job-search + tailoring session.

The agent owns judgment and orchestration (job search, fit scoring, CV
tailoring). The deterministic core — the truthfulness guards, the Israel
filter/dedup, and the actual file write — stays code, enforced inside the
`write_resume` tool (see tools.py / tooling.py). The agent cannot produce an
unchecked or untruthful résumé; it can only ask the gate to try.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

import config
from tools import resume_tools

# Loaded at import time (not just inside main()) so that MONID_API_KEY is already
# in os.environ by the time build_options() runs — including when build_options()
# is exercised directly (e.g. the no-spend dry check: `import agent; agent.build_options()`).
load_dotenv()

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
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
   graduate? Judge from the requirements text, not the title. A "Junior" title demanding
   5 years is not junior-friendly; a plain title with entry-level requirements is. Treat a
   hard requirement of 3+ years of professional experience, or a "Senior"/"Staff"/"Lead"/
   "Experienced" role, as NOT junior-friendly. If it is not junior-friendly, the candidate
   should not apply, so score fit low regardless of skills overlap.

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
- experience: every Work Experience entry, referenced by its index, reordered so the
  most relevant entry comes first. For each entry, reword the bullets it already has to
  surface the skills this posting cares about. Return exactly the same number of bullets
  the entry already has, in the same one-to-one correspondence — reword each, but never
  add a new bullet, never split one bullet into two, never merge two into one. Reference
  every index exactly once — never drop, add, or duplicate an entry.
- projects: every Project entry, referenced by its index, reordered so the most
  relevant comes first. Projects have no bullets to rewrite; return an empty bullet
  list for each. Reference every index exactly once.

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
   run id until the run reports completion, then take its output (a list of raw postings).

3. Call `filter_jobs` on that raw output. It normalizes postings, dedupes by id, and keeps
   only Israel-located ones — treat its return value as the job list to work through.

4. For EACH job in that filtered list:
   a. Judge fit yourself using the rubric below (you replace `scoring.py`'s job): decide
      `is_junior_friendly`, `fit_score` (0-100), `match_kind` ("direct" or "stretch"), and a
      one-sentence `reason`.
   b. Only if the job is junior-friendly AND `fit_score >= {config.FIT_THRESHOLD}` (the
      pinned `config.FIT_THRESHOLD`), draft the tailored CV fields yourself using the
      CV-editor rules below (you replace `tailoring.py`'s job): `summary`, `skills`,
      `experience` (every Work Experience entry, referenced by its original index exactly
      once, with reworded bullets), and `projects` (every Project entry, referenced by its
      original index exactly once, with an empty bullet list). Then call `write_resume`
      with the job, your score, and your tailored fields.
   c. For jobs that are not junior-friendly or score below the threshold, do NOT call
      `write_resume` for them — just note them as skipped. `write_resume` itself also
      refuses below-threshold jobs as a backstop, but do not rely on that backstop: only
      call it for jobs you have already judged to clear the bar.

5. When every job has been judged, report a final summary: how many jobs were considered,
   how many résumés were written (and to which companies/titles), how many were skipped and
   why, and any corrections `write_resume` reported (e.g. stripped skills, repaired entry
   coverage).

--- Fit-scoring rubric (judgment point one — replaces scoring.py) ---
{SCORING_RUBRIC}

--- CV-editor rules (judgment point two — replaces tailoring.py) ---
{CV_EDITOR_RULES}
"""

INSTRUCTIONS = WORKFLOW


def build_options() -> "ClaudeAgentOptions":
    """Configure the one-shot autonomous session.

    - Monid MCP over remote HTTP with the bearer key (per docs/agent-sdk-reference.md
      section 4), plus the in-process `resume_tools` server (section 3).
    - `allowed_tools` lists exactly the Monid tools the workflow needs (run/get_run,
      plus balance as a cheap sanity check) and the three resume tools.
    - `permission_mode="dontAsk"`: auto-runs anything already in `allowed_tools` without
      an interactive prompt, but (unlike `bypassPermissions`) still denies anything not
      pre-approved — the safer non-prompting mode called out in the reference doc, since
      this session runs headlessly with no human available to answer a prompt anyway.
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
        },
        allowed_tools=[
            "mcp__monid__monid_run",
            "mcp__monid__monid_get_run",
            "mcp__monid__monid_balance",
            "mcp__resume_tools__get_resume",
            "mcp__resume_tools__filter_jobs",
            "mcp__resume_tools__write_resume",
        ],
        permission_mode="dontAsk",
        max_turns=200,
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
