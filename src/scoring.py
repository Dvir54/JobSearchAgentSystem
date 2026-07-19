"""Judgment point one: is this posting relevant to this candidate?

Called once per posting — the run's main Claude cost.
"""
from typing import Literal

from pydantic import BaseModel

from config import CLAUDE_MODEL, MAX_TOKENS
from jobs import JobPosting


class JobScore(BaseModel):
    is_junior_friendly: bool
    fit_score: int
    match_kind: Literal["direct", "stretch"]
    reason: str


SYSTEM_PROMPT = """You evaluate job postings for a junior software engineer in Israel who
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


def build_scoring_prompt(posting: JobPosting, base_cv: str) -> str:
    return f"""Candidate CV:
{base_cv}

Job posting:
Title: {posting.title}
Company: {posting.company}
Description:
{posting.description}"""


def score_job(client, posting: JobPosting, base_cv: str) -> JobScore:
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_scoring_prompt(posting, base_cv)}],
        output_format=JobScore,
    )
    return response.parsed_output
