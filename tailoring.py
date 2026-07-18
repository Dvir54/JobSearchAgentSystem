"""Judgment point two: adapt the CV to one specific posting.

Truthfulness is enforced twice — the prompt instructs it, and
find_invented_skills() checks it. A prompt alone is not a guarantee.
"""
from pydantic import BaseModel

from config import CLAUDE_MODEL, MAX_TOKENS
from jobs import JobPosting


class TailoredCV(BaseModel):
    summary: str
    bullets: list[str]
    skills: list[str]


SYSTEM_PROMPT = """You are a CV editor. You adapt one candidate's existing CV to one
specific job posting.

Work through this before writing anything:
1. Extract the posting's real requirements — the 5-8 skills and technologies that
   actually matter, and the role's core focus. Ignore boilerplate.
2. Build an evidence matrix. For each requirement, find the candidate's proof in the
   CV: what proves it outright, what partially proves it, and what is missing entirely.
3. Rewrite only what the evidence supports. Leave the gaps as gaps.

Produce:
- summary: 2-3 sentences positioning the candidate for this specific role, built only
  from evidence in the CV.
- bullets: the candidate's experience bullets, reordered so the most relevant work
  comes first, reworded to surface the skills this posting cares about.
- skills: the candidate's skills, ordered so the ones this posting names come first.

Hard constraints on truth:
- Every claim must be one the candidate could defend in an interview. If they could
  not answer a follow-up question about it, do not write it.
- Never add a technology, tool, employer, project, or metric that is not already in
  the CV. If the posting wants something the candidate lacks, leave it out. Do not
  imply it, hint at it, or use adjacent phrasing to suggest it.
- Never invent numbers. Reuse the candidate's real metrics, or omit metrics entirely.

Hard constraints on sounding natural — these matter as much as accuracy:
- Do not copy the posting's phrasing verbatim. Echoing its exact sentences is the
  clearest sign a CV was machine-tailored. Use the ordinary vocabulary of the field.
- Never force a keyword into a bullet where it does not belong. A technology appears
  only where the underlying work genuinely involved it.
- No hype or buzzwords: no "leverage", "synergy", "spearheaded", "passionate",
  "results-driven", "cutting-edge", "ninja", "rockstar".
- Keep the candidate's own voice and register. The result must read like they rewrote
  it themselves with this job in mind — not like a template with slots filled."""


def build_tailoring_prompt(posting: JobPosting, base_cv: str) -> str:
    return f"""Base CV (the only source of truth about this candidate):
{base_cv}

Target job posting:
Title: {posting.title}
Company: {posting.company}
Description:
{posting.description}"""


def tailor_cv(client, posting: JobPosting, base_cv: str) -> TailoredCV:
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_tailoring_prompt(posting, base_cv)}],
        output_format=TailoredCV,
    )
    return response.parsed_output


def find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]:
    """Return skills present in the tailored output but absent from the base CV.

    A non-empty result means Claude invented experience — the CV must not ship.
    """
    haystack = base_cv.lower()
    return [skill for skill in tailored.skills if skill.lower() not in haystack]
