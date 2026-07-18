"""Judgment point one: is this posting relevant to this candidate?

Called once per posting — the run's main Claude cost.
"""
from pydantic import BaseModel

from config import CLAUDE_MODEL, MAX_TOKENS
from jobs import JobPosting


class JobScore(BaseModel):
    is_junior_friendly: bool
    fit_score: int
    reason: str


SYSTEM_PROMPT = """You evaluate job postings for a junior software engineer in Israel.

Decide two things:

1. is_junior_friendly: would this role realistically consider a junior candidate?
   Judge from the requirements text, not the title. Postings mislabel seniority in
   both directions: a "Junior" title demanding 5 years is not junior-friendly, and a
   posting with no seniority in the title but entry-level requirements is.
   A hard requirement of 3+ years of professional experience means not junior-friendly.

2. fit_score (0-100): how well the candidate's actual background matches this
   posting's requirements. Base this only on the CV provided. A posting demanding
   technologies absent from the CV scores low even if it is junior-friendly. Do not
   inflate: a score above 70 means the candidate could apply today and be taken
   seriously, not that the role is vaguely adjacent to their skills.

Give a one-sentence reason citing the specific requirement that drove your decision."""


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
