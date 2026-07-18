# AI Job-Search Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrape junior software jobs in Israel, judge which are genuinely relevant, and adapt the candidate's CV to each job scoring above the relevance threshold.

**Architecture:** A deterministic Python pipeline. `main.py` runs a fixed sequence: search → score → tailor → write. Claude is called as a plain function at the only two points needing judgment (is this job relevant, and what should the tailored CV say). No agent loop, no state, no dedup, no ranking. Job-source knowledge lives only in `jobs.py`.

**Tech Stack:** Python 3.11+, `anthropic`, `apify-client`, `pydantic`, `python-dotenv`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-07-17-job-search-agent-design.md`

## Global Constraints

- Python **3.11+** (required by `apify-client`).
- Claude model for both calls: **`claude-opus-4-8`** exactly. Never a date-suffixed variant.
- `max_tokens` **16000**. Neither call streams.
- `temperature`, `top_p`, `top_k`, and `budget_tokens` are **rejected with a 400** on `claude-opus-4-8`. Never pass them.
- Adaptive thinking is **off unless set explicitly**: pass `thinking={"type": "adaptive"}` where wanted.
- Structured output uses `client.messages.parse(..., output_format=Model)` → `response.parsed_output`. Never hand-parse JSON out of a text block.
- `.env` is gitignored and never committed. Secrets load via `python-dotenv`.
- Israel only. Six role queries, defined once in `config.py`.
- `COUNT_PER_QUERY = 25` and `FIT_THRESHOLD = 70` are constants in `config.py`, never literals in code.
- No network calls in the test suite. Claude and Apify are stubbed from recorded fixtures.
- Tailored content must contain no technology absent from `base_cv.txt`. Enforced by a test, not only by a prompt.

## Deliberately out of scope

Do **not** build these, and do not add scaffolding "for later": deduplication or run state, ranking or top-N caps, run reports, HTML/PDF templating, PDF or Canva rendering, dated output directories, retry logic, scheduling, auto-apply. Each is a later phase. A file whose only justification is a future feature does not belong in this plan.

---

## File Structure

| File | Responsibility |
|---|---|
| `config.py` | The four tunable values plus the model ID and paths. One place to change behavior. |
| `jobs.py` | `JobPosting`, LinkedIn URL construction, run the actor, normalize results. **The swap seam** — the only file that knows where jobs come from. |
| `scoring.py` | `JobScore` + `score_job()`. One Claude call per posting. |
| `tailoring.py` | `TailoredCV` + `tailor_cv()` + the CV-editor system prompt. One Claude call per relevant job. |
| `main.py` | The pipeline. Orchestration only, no business logic. |
| `tests/` | One test module per source module. |

Each type lives with the module that produces it. There is no shared `models.py`.

---

## Task 0: Scaffolding

**Files:**
- Create: `requirements.txt`, `config.py`, `tests/__init__.py`, `tests/fixtures/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Create `requirements.txt`**

```
anthropic>=0.69
apify-client>=1.7
pydantic>=2.7
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: Create the virtual environment and install**

Run:
```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
```
Expected: installs without error. Confirm Python 3.11+ with `.venv/Scripts/python.exe --version`.

- [ ] **Step 3: Create `config.py`**

```python
from pathlib import Path

ROLE_QUERIES = [
    "software engineer",
    "backend developer",
    "fullstack developer",
    "QA automation engineer",
    "DevOps engineer",
    "cybersecurity engineer",
]

# Jobs scraped per role query. The cost control: Apify bills $1.00/1000
# results, so 6 queries x 25 = 150 results = ~$0.15 per run.
COUNT_PER_QUERY = 25

# A job needs this fit score (0-100) to earn a tailored CV.
FIT_THRESHOLD = 70

CLAUDE_MODEL = "claude-opus-4-8"
MAX_TOKENS = 16000

ACTOR_ID = "curious_coder/linkedin-jobs-scraper"

PROJECT_ROOT = Path(__file__).parent
BASE_CV_PATH = PROJECT_ROOT / "base_cv.txt"
OUTPUT_DIR = PROJECT_ROOT / "output"
```

- [ ] **Step 4: Create test package files**

Create `tests/__init__.py` and `tests/fixtures/.gitkeep` as empty files.

- [ ] **Step 5: Verify `.gitignore`**

It must contain `.env`, `output/`, `.venv/`, and `__pycache__/`. Add any that are missing. Remove `state/` — there is no state directory.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.py tests/__init__.py tests/fixtures/.gitkeep .gitignore
git commit -m "chore: scaffold project structure and config"
```

---

## Task 1: Spike A — validate Apify Israel coverage

**Throwaway. Nothing from this task ships.** Its outputs are a decision and one fixture.

**Files:**
- Create: `spike_apify.py` (deleted at the end of this task)
- Create: `tests/fixtures/actor_response.json` (kept — used by Task 2)

**Interfaces:**
- Produces: `tests/fixtures/actor_response.json` — raw actor dataset items, so Task 2 can test normalization without a network call.

- [ ] **Step 1: Create `.env`**

At the project root (gitignored):
```
APIFY_API_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
```
Apify token: Apify Console → Integrations (free plan, $5/month credit, no card). Anthropic key: Anthropic Console.

- [ ] **Step 2: Write the spike script**

```python
# spike_apify.py — THROWAWAY. Validates Apify coverage of Israeli junior roles.
import json
import os
from apify_client import ApifyClient
from dotenv import load_dotenv

load_dotenv()

client = ApifyClient(os.environ["APIFY_API_TOKEN"])
url = "https://www.linkedin.com/jobs/search/?keywords=backend%20developer&location=Israel&f_E=2"

run = client.actor("curious_coder/linkedin-jobs-scraper").call(
    run_input={"urls": [url], "count": 25}
)
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

print(f"results: {len(items)}")
for item in items[:10]:
    print(f"  {item.get('title')!r} | {item.get('companyName')!r} | {item.get('postedAt')!r}")

with open("tests/fixtures/actor_response.json", "w", encoding="utf-8") as f:
    json.dump(items, f, indent=2, ensure_ascii=False)
print("saved raw response to tests/fixtures/actor_response.json")
```

- [ ] **Step 3: Run it and answer four questions**

Run: `.venv/Scripts/python.exe spike_apify.py`

1. **How many results came back?** Zero or a handful means Israeli coverage is inadequate.
2. **How fresh are the postings?** Mostly older than ~30 days means a stale index.
3. **How many are genuinely entry-level?** Read the titles. This validates that `f_E=2` means what we think.
4. **What are the real field names in the raw JSON?** Task 2's normalization uses `id`, `title`, `companyName`, `descriptionText`, `link`, `postedAt` — these are guesses and **must be corrected against what you actually see**.

- [ ] **Step 4: Decide**

Coverage adequate → continue. Near-zero results, or nothing entry-level → **stop and switch to JSearch** before writing pipeline code. This is why the spike runs first.

- [ ] **Step 5: Delete the spike, commit the fixture**

```bash
rm spike_apify.py
git add tests/fixtures/actor_response.json
git commit -m "test: add raw Apify actor response fixture from Spike A"
```

---

## Task 2: The job source

**Files:**
- Create: `jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `config.ACTOR_ID`; `tests/fixtures/actor_response.json` from Task 1.
- Produces:
  - `JobPosting(id: str, title: str, company: str, description: str, url: str, posted_date: str | None)` — frozen dataclass.
  - `build_search_url(keyword: str) -> str`
  - `normalize_posting(raw: dict) -> JobPosting`
  - `fetch_jobs(client, keywords: list[str], count: int) -> list[JobPosting]`

- [ ] **Step 1: Write the failing tests**

`tests/test_jobs.py`:
```python
import json
from pathlib import Path

from jobs import JobPosting, build_search_url, normalize_posting

FIXTURE = Path(__file__).parent / "fixtures" / "actor_response.json"


def test_build_search_url_targets_israel_entry_level():
    url = build_search_url("backend developer")
    assert "location=Israel" in url
    assert "f_E=2" in url
    assert "keywords=backend%20developer" in url


def test_build_search_url_omits_junior_keyword():
    # Seniority is handled by the f_E=2 filter, not by keyword-stuffing.
    assert "junior" not in build_search_url("software engineer").lower()


def test_normalize_posting_produces_stable_shape():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    posting = normalize_posting(raw)
    assert isinstance(posting, JobPosting)
    assert posting.id
    assert posting.title
    assert posting.company
    assert posting.url.startswith("http")


def test_normalize_posting_tolerates_missing_posted_date():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    raw.pop("postedAt", None)
    assert normalize_posting(raw).posted_date is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobs'`.

- [ ] **Step 3: Write `jobs.py`**

**Correct the field names against the real fixture from Spike A before running.**

```python
"""The only module that knows where jobs come from.

Everything downstream consumes JobPosting. Swapping to JSearch means
rewriting this file and nothing else.
"""
from dataclasses import dataclass
from urllib.parse import quote

from config import ACTOR_ID


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None


def build_search_url(keyword: str) -> str:
    """Build a LinkedIn job-search URL. f_E=2 is LinkedIn's entry-level filter."""
    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote(keyword)}&location=Israel&f_E=2"
    )


def normalize_posting(raw: dict) -> JobPosting:
    """Map one raw actor item to JobPosting. Field names verified in Spike A."""
    return JobPosting(
        id=str(raw["id"]),
        title=raw["title"],
        company=raw["companyName"],
        description=raw.get("descriptionText", ""),
        url=raw["link"],
        posted_date=raw.get("postedAt"),
    )


def fetch_jobs(client, keywords: list[str], count: int) -> list[JobPosting]:
    """Run the actor once per keyword and return normalized postings.

    Raises RuntimeError on zero results across all queries: that means the
    community-maintained scraper broke, not that Israel has no jobs today.
    """
    postings: list[JobPosting] = []
    for keyword in keywords:
        run = client.actor(ACTOR_ID).call(
            run_input={"urls": [build_search_url(keyword)], "count": count}
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        postings.extend(normalize_posting(item) for item in items)

    if not postings:
        raise RuntimeError(
            "Apify actor returned zero results across all queries. "
            "The scraper is likely broken or blocked — check it before spending on Claude."
        )
    return postings
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: 4 passed. A `KeyError` means the field names differ from Spike A's output — fix `normalize_posting`, not the test.

- [ ] **Step 5: Commit**

```bash
git add jobs.py tests/test_jobs.py
git commit -m "feat: add Apify job source"
```

---

## Task 3: Relevance scoring

**Files:**
- Create: `scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `jobs.JobPosting`, `config.CLAUDE_MODEL`, `config.MAX_TOKENS`
- Produces:
  - `JobScore(is_junior_friendly: bool, fit_score: int, reason: str)` — pydantic model.
  - `build_scoring_prompt(posting: JobPosting, base_cv: str) -> str`
  - `score_job(client, posting: JobPosting, base_cv: str) -> JobScore`

- [ ] **Step 1: Capture job-description fixtures**

Create three files under `tests/fixtures/`, using **real descriptions** from `tests/fixtures/actor_response.json`:
- `jd_junior.txt` — clearly entry-level.
- `jd_senior.txt` — clearly senior (7+ years required).

If Spike A returned no senior posting, take one from any LinkedIn search. These exist so scoring is testable without a network call.

- [ ] **Step 2: Write the failing tests**

`tests/test_scoring.py`:
```python
from jobs import JobPosting
from scoring import JobScore, build_scoring_prompt, score_job


class FakeMessages:
    """Stands in for client.messages — records the call, returns a canned parse."""

    def __init__(self, parsed_output):
        self._parsed_output = parsed_output
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return type("Response", (), {"parsed_output": self._parsed_output})()


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _posting(description: str = "...") -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description=description, url="https://example.com", posted_date=None,
    )


def test_build_scoring_prompt_includes_description_and_cv():
    prompt = build_scoring_prompt(_posting("Django and Postgres"), "I know Python.")
    assert "Django and Postgres" in prompt
    assert "I know Python." in prompt


def test_score_job_returns_the_parsed_score():
    expected = JobScore(is_junior_friendly=True, fit_score=80, reason="Entry level.")
    client = FakeClient(expected)
    assert score_job(client, _posting(), "I know Python.") == expected


def test_score_job_requests_structured_output_from_the_right_model():
    client = FakeClient(JobScore(is_junior_friendly=False, fit_score=0, reason="Senior."))
    score_job(client, _posting(), "cv")

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is JobScore
    # These parameters are rejected with a 400 on claude-opus-4-8.
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scoring'`.

- [ ] **Step 4: Write `scoring.py`**

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: 3 passed.

- [ ] **Step 6: Verify against the real API once**

Write a throwaway snippet loading `jd_senior.txt` and `jd_junior.txt`, call `score_job` with a real `anthropic.Anthropic()` client, print both. Confirm the senior fixture returns `is_junior_friendly=False` and the junior one `True`, and that the scores are not both pinned near 100. Tighten `SYSTEM_PROMPT` if not. Delete the snippet.

- [ ] **Step 7: Commit**

```bash
git add scoring.py tests/test_scoring.py tests/fixtures/jd_junior.txt tests/fixtures/jd_senior.txt
git commit -m "feat: add Claude-based relevance scoring"
```

---

## Task 4: CV tailoring

The system prompt is the heart of this project. It is built on patterns from published CV-tailoring prompts: build an evidence matrix before writing, constrain to claims defensible in an interview, and avoid the verbatim-mirroring that makes machine-tailored CVs obvious.

**Files:**
- Create: `tailoring.py`
- Test: `tests/test_tailoring.py`

**Interfaces:**
- Consumes: `jobs.JobPosting`, `config.CLAUDE_MODEL`, `config.MAX_TOKENS`
- Produces:
  - `TailoredCV(summary: str, bullets: list[str], skills: list[str])` — pydantic model.
  - `tailor_cv(client, posting: JobPosting, base_cv: str) -> TailoredCV`
  - `find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_tailoring.py`:
```python
from jobs import JobPosting
from tailoring import TailoredCV, find_invented_skills, tailor_cv

BASE_CV = """Dvir — Software Engineer
Skills: Python, Django, PostgreSQL, Docker, Git
Experience: Built a REST API in Django serving 10k requests/day.
"""


class FakeMessages:
    def __init__(self, parsed_output):
        self._parsed_output = parsed_output
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.last_kwargs = kwargs
        return type("Response", (), {"parsed_output": self._parsed_output})()


class FakeClient:
    def __init__(self, parsed_output):
        self.messages = FakeMessages(parsed_output)


def _posting() -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description="Looking for Python and Docker experience.",
        url="https://example.com", posted_date=None,
    )


def test_find_invented_skills_accepts_skills_present_in_base_cv():
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["Python", "Docker"])
    assert find_invented_skills(tailored, BASE_CV) == []


def test_find_invented_skills_is_case_insensitive():
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["python", "DOCKER"])
    assert find_invented_skills(tailored, BASE_CV) == []


def test_find_invented_skills_flags_technology_absent_from_base_cv():
    # This is the constraint the whole system rests on: no invented experience.
    tailored = TailoredCV(summary="s", bullets=["b"], skills=["Python", "Kubernetes"])
    assert find_invented_skills(tailored, BASE_CV) == ["Kubernetes"]


def test_tailor_cv_returns_parsed_output():
    expected = TailoredCV(summary="Backend dev", bullets=["Built an API"], skills=["Python"])
    client = FakeClient(expected)
    assert tailor_cv(client, _posting(), BASE_CV) == expected


def test_tailor_cv_requests_structured_output_from_the_right_model():
    client = FakeClient(TailoredCV(summary="s", bullets=["b"], skills=["Python"]))
    tailor_cv(client, _posting(), BASE_CV)

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is TailoredCV
    assert "temperature" not in kwargs
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tailoring'`.

- [ ] **Step 3: Write `tailoring.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tailoring.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tailoring.py tests/test_tailoring.py
git commit -m "feat: add CV tailoring with invented-skill detection"
```

---

## Task 5: The pipeline

**Files:**
- Create: `main.py`
- Test: none. Every piece of logic here is one line of orchestration; Task 6 verifies it by running it.

**Interfaces:**
- Consumes: `config`, `jobs`, `scoring`, `tailoring`
- Produces: `write_cv(posting, score, tailored, out_dir) -> Path`, `main() -> int`

- [ ] **Step 1: Write `main.py`**

```python
"""Entry point. Owns the sequence; every decision needing judgment is a call
into scoring.py or tailoring.py.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import anthropic
from apify_client import ApifyClient
from dotenv import load_dotenv

import config
from jobs import JobPosting, build_search_url, fetch_jobs
from scoring import JobScore, score_job
from tailoring import TailoredCV, find_invented_skills, tailor_cv


def safe_filename(posting: JobPosting) -> str:
    """Company names come from a scraper — never trust them as path components."""
    company = re.sub(r'[<>:"/\\|?*]', "", posting.company).strip().replace(" ", "_")
    title = re.sub(r'[<>:"/\\|?*]', "", posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.md"


def write_cv(posting: JobPosting, score: JobScore, tailored: TailoredCV, out_dir: Path) -> Path:
    """Write one tailored CV. The job URL is in the header: a CV with no link
    back to its posting is unusable."""
    lines = [
        f"# {posting.company} — {posting.title}",
        "",
        f"- **Fit:** {score.fit_score}/100 — {score.reason}",
        f"- **Apply at:** {posting.url}",
        "",
        "## Summary",
        "",
        tailored.summary,
        "",
        "## Experience",
        "",
        *[f"- {bullet}" for bullet in tailored.bullets],
        "",
        "## Skills",
        "",
        " · ".join(tailored.skills),
        "",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find junior jobs in Israel and tailor CVs.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the search URLs and projected cost, then exit without spending.",
    )
    args = parser.parse_args()

    load_dotenv()

    projected = len(config.ROLE_QUERIES) * config.COUNT_PER_QUERY
    print(f"Queries: {len(config.ROLE_QUERIES)} x {config.COUNT_PER_QUERY} results")
    print(f"Projected: ~{projected} Apify results ≈ ${projected / 1000:.2f}")
    print("Apify's free plan hard-stops at $5/month and blocks until the next cycle.")

    if args.dry_run:
        for keyword in config.ROLE_QUERIES:
            print(f"  {build_search_url(keyword)}")
        return 0

    if not config.BASE_CV_PATH.exists():
        print(f"error: {config.BASE_CV_PATH} not found. It is the source of truth for CV content.")
        return 1
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")

    apify = ApifyClient(os.environ["APIFY_API_TOKEN"])
    claude = anthropic.Anthropic()

    # 1. Search. Raises if the scraper returned nothing — abort before Claude spend.
    postings = fetch_jobs(apify, config.ROLE_QUERIES, config.COUNT_PER_QUERY)
    print(f"Fetched {len(postings)} postings.\n")

    written = 0
    for posting in postings:
        # 2. Score.
        score = score_job(claude, posting, base_cv)
        relevant = score.is_junior_friendly and score.fit_score >= config.FIT_THRESHOLD
        if not relevant:
            print(f"  skip  [{score.fit_score:3}] {posting.company} — {posting.title}")
            continue

        # 3. Tailor.
        tailored = tailor_cv(claude, posting, base_cv)

        invented = find_invented_skills(tailored, base_cv)
        if invented:
            print(f"  DROP  {posting.company}: invented skills {', '.join(invented)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, config.OUTPUT_DIR)
        written += 1
        print(f"  WRITE [{score.fit_score:3}] {path.name}")

    print(f"\n{written} tailored CVs in {config.OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: all tests pass across every module.

- [ ] **Step 3: Dry run**

Run: `.venv/Scripts/python.exe main.py --dry-run`
Expected: prints six LinkedIn URLs and a projected cost of ~$0.15, and spends nothing.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add pipeline entry point"
```

---

## Task 6: First real run

No new code. This is where the agent is judged. **Requires `base_cv.txt` at the project root — stop and ask the user for it if absent.**

- [ ] **Step 1: Run it**

Run: `.venv/Scripts/python.exe main.py`
Expected: fetches postings, scores each one, writes a `.md` for every job scoring 70+.

- [ ] **Step 2: Sanity-check the scores**

Read the skip lines. Are the scores discriminating, or clustered near one value? If nothing clears 70, or if everything does, the scoring prompt needs work — that is the finding, and it matters more than the CVs.

- [ ] **Step 3: Read every generated CV**

For each file: is the summary specific to that job, or generic? Does it read like a person wrote it, or like a template? Does it echo the posting's phrasing? And most importantly — **is every claim true to `base_cv.txt`?** `find_invented_skills` only guards the skills list; you must read the summary and bullets yourself.

- [ ] **Step 4: Tune the prompts against what you found**

This step is expected, not a failure. Adjust `SYSTEM_PROMPT` in `scoring.py` or `tailoring.py`, re-run, re-read. Repeat until the output is worth sending.

- [ ] **Step 5: Commit what you learned**

```bash
git commit -am "chore: first live run — <N> CVs written; <what you tuned and why>"
```

**The agent is now working end-to-end. Phase 1 is complete.**
