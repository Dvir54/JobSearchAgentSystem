# AI Job-Search Agent — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Find junior software job postings in Israel, score each for fit, and produce a tailored CV PDF for the best five.

**Architecture:** A deterministic Python pipeline. `main.py` runs a fixed sequence: search → dedupe → score → rank → tailor → render → report. Claude is called as a plain function at the only two points needing judgment (scoring a posting, writing tailored content). There is no agent loop. All job-source knowledge lives behind `sources/apify.py` so the source can be swapped without touching anything else.

**Tech Stack:** Python 3.11+, `anthropic` (Claude API), `apify-client` (LinkedIn scraper), `pydantic` (structured output), `python-dotenv` (secrets), `pytest` (tests). Rendering is decided by Spike B — either the Canva MCP server (via `mcp` + `npx mcp-remote`) or `jinja2` + `weasyprint`.

**Spec:** `docs/superpowers/specs/2026-07-17-job-search-agent-design.md`

## Global Constraints

- Python **3.11+** (required by `apify-client`).
- Claude model for both scoring and tailoring: **`claude-opus-4-8`** exactly. Never a date-suffixed variant.
- `max_tokens` **16000** on non-streaming calls. Neither call streams.
- `temperature`, `top_p`, `top_k`, and `budget_tokens` are **rejected with a 400** on `claude-opus-4-8`. Never pass them.
- Adaptive thinking is **off unless set explicitly**: pass `thinking={"type": "adaptive"}` where wanted.
- Structured output uses `client.messages.parse(..., output_format=Model)` → `response.parsed_output`. Never hand-parse JSON out of a text block.
- `.env` is gitignored and never committed. Secrets are read via `python-dotenv`.
- Location is Israel only. Six role queries, defined once in `config.py`.
- `COUNT_PER_QUERY = 25` and `TOP_N = 5` are constants in `config.py`, not literals scattered in code.
- No network calls in the test suite. Claude and Apify are stubbed from recorded fixtures.
- Tailored CV content must contain no technology absent from `base_cv.txt`. This is enforced by a test, not only by a prompt.

---

## File Structure

| File | Responsibility |
|---|---|
| `config.py` | Constants: role queries, `COUNT_PER_QUERY`, `TOP_N`, model ID, paths. |
| `models.py` | `JobPosting`, `JobScore`, `ScoredJob`, `TailoredCV`, `GeneratedCV`. The contracts between stages. |
| `sources/apify.py` | Build LinkedIn URLs, run the actor, normalize to `JobPosting`. **The swap seam.** |
| `state.py` | `state/seen_jobs.json` read/write (atomic) and dedupe. |
| `scoring.py` | `score_job()` — one Claude call per posting. |
| `tailoring.py` | `tailor_cv()` — one Claude call per selected job; plus the invented-tech check. |
| `render/canva.py` *or* `render/html.py` | `render()` — tailored content → PDF on disk. Chosen by Spike B. |
| `report.py` | Writes `output/YYYY-MM-DD/report.md`. |
| `main.py` | The pipeline. Orchestration only — no business logic. |
| `tests/` | Fixture-based tests, one module per source module. |

---

## Task 0: Project scaffolding

**Files:**
- Create: `requirements.txt`, `config.py`, `sources/__init__.py`, `render/__init__.py`, `tests/__init__.py`, `tests/fixtures/.gitkeep`
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
Expected: installs without error. Confirm Python is 3.11+ with `.venv/Scripts/python.exe --version`.

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

# Jobs scraped per role query. The primary cost control: Apify bills
# $1.00/1000 results, so 6 queries x 25 = 150 results = ~$0.15 per run.
COUNT_PER_QUERY = 25

# CVs generated per run, taken from the top of the fit ranking.
TOP_N = 5

CLAUDE_MODEL = "claude-opus-4-8"
MAX_TOKENS = 16000

ACTOR_ID = "curious_coder/linkedin-jobs-scraper"

PROJECT_ROOT = Path(__file__).parent
BASE_CV_PATH = PROJECT_ROOT / "base_cv.txt"
SEEN_JOBS_PATH = PROJECT_ROOT / "state" / "seen_jobs.json"
OUTPUT_DIR = PROJECT_ROOT / "output"
```

- [ ] **Step 4: Create empty package files**

Create `sources/__init__.py`, `render/__init__.py`, `tests/__init__.py` as empty files, and `tests/fixtures/.gitkeep` as an empty file.

- [ ] **Step 5: Verify `.gitignore` covers secrets and artifacts**

The existing `.gitignore` must contain `.env`, `output/`, `state/`, `.venv/`, and `__pycache__/`. Add any that are missing.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.py sources/__init__.py render/__init__.py tests/__init__.py tests/fixtures/.gitkeep .gitignore
git commit -m "chore: scaffold project structure and config"
```

---

## Task 1: Spike A — validate Apify Israel coverage

**Throwaway. Nothing from this task ships.** Its only output is a decision and a saved fixture.

**Files:**
- Create: `spike_apify.py` (deleted at the end of this task)
- Create: `tests/fixtures/actor_response.json` (kept — used by Task 3)

**Interfaces:**
- Consumes: `config.ACTOR_ID`
- Produces: `tests/fixtures/actor_response.json` — the raw actor dataset items, used to test normalization without network.

- [ ] **Step 1: Add the Apify token to `.env`**

Create `.env` at the project root (it is gitignored):
```
APIFY_API_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
```
Get the Apify token from Apify Console → Integrations (free plan, $5/month credit, no card). Get the Anthropic key from the Anthropic Console.

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

- [ ] **Step 3: Run it and record the answers**

Run: `.venv/Scripts/python.exe spike_apify.py`

Record these four answers — they are the point of the spike:
1. **How many results came back?** Zero or a handful means Israeli coverage is inadequate.
2. **How fresh are the postings?** Anything mostly older than ~30 days is a stale index.
3. **How many of the 25 are genuinely entry-level?** Read the titles. This validates that `f_E=2` means what we think.
4. **What are the actual field names** in the raw JSON? The normalization in Task 3 depends on these — the field names used there (`title`, `companyName`, `descriptionText`, `link`, `postedAt`, `id`) are the expected ones and **must be corrected against what you actually see**.

- [ ] **Step 4: Decide**

If coverage is adequate → continue to Task 2. If results are near-zero or nothing is entry-level → **stop and switch the plan to JSearch** before writing any pipeline code. This is the whole reason the spike runs first.

- [ ] **Step 5: Delete the spike and commit the fixture**

```bash
rm spike_apify.py
git add tests/fixtures/actor_response.json
git commit -m "test: add raw Apify actor response fixture from Spike A"
```

---

## Task 2: Spike B — validate Canva editing

**Throwaway.** Its output is a decision: Canva rendering (Task 7a) or HTML rendering (Task 7b).

**Requires from the user:** a Canva CV design must exist in their account, and `base_cv.txt` must exist at the project root. **Stop and ask for both if absent.**

- [ ] **Step 1: Confirm Node.js is available**

Run: `node --version`
Expected: v18 or later. `mcp-remote` runs via `npx`; without Node, the Canva path is not viable and you go straight to Task 7b.

- [ ] **Step 2: Authenticate to Canva MCP**

Run: `npx -y mcp-remote https://mcp.canva.com/mcp`
Expected: a browser opens for Canva login. Approve it. The token caches under `~/.mcp-auth`. Leave the process running for the next step, or re-run it later — the cached token is reused.

If this fails or the OAuth flow does not complete, **the Canva path is dead — go to Task 7b (HTML)**. Do not spend more than ~30 minutes here.

- [ ] **Step 3: Drive the tool sequence by hand**

Using Claude Code's own Canva MCP connection (or the `mcp-remote` session), perform these calls in order and confirm each returns success:
1. `search-designs` — find the CV design. Record its **design ID**.
2. `copy-design` — duplicate it. Record the **copy's ID**. Confirm in the Canva UI that the original is untouched.
3. `get-design-content` on the copy — **record the structure of the returned text elements**. This is the critical unknown: are text elements addressable by a stable ID?
4. `start-editing-transaction` → `perform-editing-operations` (change one text element to a known string) → `commit-editing-transaction`.
5. `export-design` on the copy as PDF. Download it and open it.

- [ ] **Step 4: Decide**

**Go to Task 7a (Canva)** only if: OAuth completed, `get-design-content` returned addressable text elements, the edit landed on the element you intended, and the exported PDF is correct.

**Go to Task 7b (HTML)** if any of those failed. Record which one failed and why, in the commit message.

- [ ] **Step 5: Record the decision**

```bash
git commit --allow-empty -m "spike: Spike B outcome — <canva|html>, because <reason>"
```

---

## Task 3: Models and the Apify source

**Files:**
- Create: `models.py`, `sources/apify.py`
- Test: `tests/test_apify.py`

**Interfaces:**
- Consumes: `config.ROLE_QUERIES`, `config.COUNT_PER_QUERY`, `config.ACTOR_ID`; `tests/fixtures/actor_response.json` from Task 1.
- Produces:
  - `JobPosting(id: str, title: str, company: str, description: str, url: str, posted_date: str | None)` — frozen dataclass.
  - `build_search_url(keyword: str) -> str`
  - `normalize_posting(raw: dict) -> JobPosting`
  - `fetch_jobs(client, keywords: list[str], count: int) -> list[JobPosting]`

- [ ] **Step 1: Write `models.py`**

```python
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None


class JobScore(BaseModel):
    """Claude's judgment about one posting. Structured output schema."""
    is_junior_friendly: bool
    fit_score: int
    reason: str


@dataclass(frozen=True)
class ScoredJob:
    posting: JobPosting
    score: JobScore


class TailoredCV(BaseModel):
    """Claude's tailored content for one job. Structured output schema."""
    summary: str
    bullets: list[str]
    skills: list[str]


@dataclass(frozen=True)
class GeneratedCV:
    job: ScoredJob
    pdf_path: Path
```

- [ ] **Step 2: Write the failing tests**

`tests/test_apify.py`:
```python
import json
from pathlib import Path

from models import JobPosting
from sources.apify import build_search_url, normalize_posting

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

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sources.apify'`.

- [ ] **Step 4: Write `sources/apify.py`**

**Correct the field names below against the real fixture from Spike A before running.**

```python
"""The only module that knows where jobs come from.

Everything downstream consumes JobPosting. Swapping to JSearch means
rewriting this file and nothing else.
"""
from urllib.parse import quote

from config import ACTOR_ID
from models import JobPosting


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

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_apify.py -v`
Expected: 4 passed. If normalization fails on a `KeyError`, the field names differ from Spike A's output — fix `normalize_posting`, not the test.

- [ ] **Step 6: Commit**

```bash
git add models.py sources/apify.py tests/test_apify.py
git commit -m "feat: add JobPosting model and Apify job source"
```

---

## Task 4: State and deduplication

**Files:**
- Create: `state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `models.JobPosting`
- Produces:
  - `load_seen(path: Path) -> set[str]`
  - `mark_seen(path: Path, job_id: str) -> None`
  - `filter_unseen(postings: list[JobPosting], seen: set[str]) -> list[JobPosting]`

- [ ] **Step 1: Write the failing tests**

`tests/test_state.py`:
```python
import json

from models import JobPosting
from state import filter_unseen, load_seen, mark_seen


def _posting(job_id: str) -> JobPosting:
    return JobPosting(
        id=job_id, title="Dev", company="Acme",
        description="", url="https://example.com", posted_date=None,
    )


def test_load_seen_returns_empty_set_when_file_absent(tmp_path):
    assert load_seen(tmp_path / "seen_jobs.json") == set()


def test_mark_seen_then_load_seen_round_trips(tmp_path):
    path = tmp_path / "seen_jobs.json"
    mark_seen(path, "job-1")
    mark_seen(path, "job-2")
    assert load_seen(path) == {"job-1", "job-2"}


def test_mark_seen_is_idempotent(tmp_path):
    path = tmp_path / "seen_jobs.json"
    mark_seen(path, "job-1")
    mark_seen(path, "job-1")
    assert json.loads(path.read_text(encoding="utf-8")) == ["job-1"]


def test_mark_seen_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "seen_jobs.json"
    mark_seen(path, "job-1")
    assert [p.name for p in tmp_path.iterdir()] == ["seen_jobs.json"]


def test_filter_unseen_drops_known_ids():
    postings = [_posting("a"), _posting("b"), _posting("c")]
    result = filter_unseen(postings, {"b"})
    assert [p.id for p in result] == ["a", "c"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`.

- [ ] **Step 3: Write `state.py`**

```python
"""Cross-run memory of which jobs have already been processed.

Writes are atomic: a crash mid-write must never corrupt seen_jobs.json into
a file that blocks every future run.
"""
import json
import os
from pathlib import Path

from models import JobPosting


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def mark_seen(path: Path, job_id: str) -> None:
    seen = load_seen(path)
    if job_id in seen:
        return
    seen.add(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    os.replace(tmp, path)


def filter_unseen(postings: list[JobPosting], seen: set[str]) -> list[JobPosting]:
    """Dedupe before scoring — this is what keeps Claude costs down."""
    return [p for p in postings if p.id not in seen]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_state.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add state.py tests/test_state.py
git commit -m "feat: add atomic seen-jobs state and dedupe"
```

---

## Task 5: Scoring

**Files:**
- Create: `scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `models.JobPosting`, `models.JobScore`, `models.ScoredJob`, `config.CLAUDE_MODEL`, `config.MAX_TOKENS`
- Produces: `score_job(client, posting: JobPosting, base_cv: str) -> ScoredJob`

- [ ] **Step 1: Capture the job-description fixtures**

Create three files under `tests/fixtures/`, using **real descriptions** taken from the Spike A output (`tests/fixtures/actor_response.json`):
- `jd_junior.txt` — a clearly entry-level posting.
- `jd_senior.txt` — a clearly senior posting (7+ years required).
- `jd_ambiguous.txt` — one that is genuinely unclear.

If Spike A's results contain no senior posting, take one from any LinkedIn search. These files exist so scoring can be tested without a network call.

- [ ] **Step 2: Write the failing test**

`tests/test_scoring.py`:
```python
from pathlib import Path

from models import JobPosting, JobScore
from scoring import build_scoring_prompt, score_job

FIXTURES = Path(__file__).parent / "fixtures"


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


def _posting(description: str) -> JobPosting:
    return JobPosting(
        id="job-1", title="Backend Developer", company="Acme",
        description=description, url="https://example.com", posted_date=None,
    )


def test_build_scoring_prompt_includes_description_and_cv():
    prompt = build_scoring_prompt(_posting("Django and Postgres"), "I know Python.")
    assert "Django and Postgres" in prompt
    assert "I know Python." in prompt


def test_score_job_returns_scored_job_wrapping_the_posting():
    expected = JobScore(is_junior_friendly=True, fit_score=80, reason="Entry level.")
    client = FakeClient(expected)
    posting = _posting((FIXTURES / "jd_junior.txt").read_text(encoding="utf-8"))

    result = score_job(client, posting, "I know Python.")

    assert result.posting is posting
    assert result.score == expected


def test_score_job_requests_structured_output_from_the_right_model():
    client = FakeClient(JobScore(is_junior_friendly=False, fit_score=0, reason="Senior."))
    score_job(client, _posting("..."), "cv")

    kwargs = client.messages.last_kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["output_format"] is JobScore
    # These parameters are rejected with a 400 on claude-opus-4-8.
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scoring'`.

- [ ] **Step 4: Write `scoring.py`**

```python
"""Judgment point one: is this posting junior-friendly, and how well does it fit?

Called once per new posting. This is the run's main Claude cost, which is why
dedupe happens before it.
"""
from config import CLAUDE_MODEL, MAX_TOKENS
from models import JobPosting, JobScore, ScoredJob

SYSTEM_PROMPT = """You evaluate job postings for a junior software engineer in Israel.

Decide two things:

1. is_junior_friendly: would this role realistically consider a junior candidate?
   Judge from the requirements text, not the title. Postings mislabel seniority in
   both directions: a "Junior" title demanding 5 years is not junior-friendly, and a
   posting with no seniority in the title but entry-level requirements is.
   A hard requirement of 3+ years of professional experience means not junior-friendly.

2. fit_score (0-100): how well the candidate's actual background matches this
   posting's requirements. Base this only on the CV provided. A posting demanding
   technologies absent from the CV scores low even if it is junior-friendly.

Give a one-sentence reason citing the specific requirement that drove your decision."""


def build_scoring_prompt(posting: JobPosting, base_cv: str) -> str:
    return f"""Candidate CV:
{base_cv}

Job posting:
Title: {posting.title}
Company: {posting.company}
Description:
{posting.description}"""


def score_job(client, posting: JobPosting, base_cv: str) -> ScoredJob:
    response = client.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": build_scoring_prompt(posting, base_cv)}],
        output_format=JobScore,
    )
    return ScoredJob(posting=posting, score=response.parsed_output)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: 3 passed.

- [ ] **Step 6: Verify against the real API once**

Write a throwaway snippet that loads `tests/fixtures/jd_senior.txt` and `jd_junior.txt`, calls `score_job` with a real `anthropic.Anthropic()` client, and prints both results. Confirm the senior fixture returns `is_junior_friendly=False` and the junior one `True`. If the senior one passes as junior-friendly, tighten `SYSTEM_PROMPT` before moving on. Delete the snippet.

- [ ] **Step 7: Commit**

```bash
git add scoring.py tests/test_scoring.py tests/fixtures/jd_junior.txt tests/fixtures/jd_senior.txt tests/fixtures/jd_ambiguous.txt
git commit -m "feat: add Claude-based job scoring"
```

---

## Task 6: Tailoring

**Files:**
- Create: `tailoring.py`
- Test: `tests/test_tailoring.py`

**Interfaces:**
- Consumes: `models.JobPosting`, `models.TailoredCV`, `config.CLAUDE_MODEL`, `config.MAX_TOKENS`
- Produces:
  - `tailor_cv(client, posting: JobPosting, base_cv: str) -> TailoredCV`
  - `find_invented_skills(tailored: TailoredCV, base_cv: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_tailoring.py`:
```python
from models import JobPosting, TailoredCV
from tailoring import find_invented_skills, tailor_cv

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
"""Judgment point two: rewrite the CV content for one specific posting.

The factual-accuracy constraint is enforced twice: the prompt instructs it, and
find_invented_skills() checks it. The prompt alone is not a guarantee.
"""
from config import CLAUDE_MODEL, MAX_TOKENS
from models import JobPosting, TailoredCV

SYSTEM_PROMPT = """You tailor a candidate's CV to one specific job posting.

Method:
1. Read the posting and identify the top 5-8 required skills and the role's core focus.
2. Rewrite the summary line to speak to this specific role.
3. Reorder and lightly reword the experience bullets so the most relevant work comes
   first and mirrors the posting's language.
4. Order the skills list so technologies the posting names appear first.

Absolute constraint: everything you write must be factually true to the CV you are
given. Never add a technology, tool, employer, or accomplishment that is not already
in it. If the posting wants something the candidate lacks, leave it out — do not
imply it. Reordering and rewording are allowed; inventing is not."""


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
    """Return skills that appear in the tailored output but not in the base CV.

    A non-empty result means Claude invented experience and the CV must not ship.
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

## Task 7a: Canva rendering (only if Spike B chose Canva)

**Skip to Task 7b if Spike B chose HTML.**

**Files:**
- Create: `render/canva.py`
- Test: `tests/test_render_canva.py`

**Interfaces:**
- Consumes: `models.TailoredCV`, `models.JobPosting`
- Produces: `render(session, tailored: TailoredCV, posting: JobPosting, folder_id: str, out_dir: Path) -> Path`

- [ ] **Step 1: Add the MCP client dependency**

Append `mcp>=1.2` to `requirements.txt` and run `.venv/Scripts/python.exe -m pip install -r requirements.txt`.

- [ ] **Step 2: Write the failing test**

The test covers filename construction and the transaction-cancel-on-failure rule — the parts that are ours. The MCP calls themselves were validated by Spike B.

`tests/test_render_canva.py`:
```python
import pytest

from models import JobPosting
from render.canva import CanvaError, safe_filename, sanitize


def _posting(company: str, title: str) -> JobPosting:
    return JobPosting(
        id="job-1", title=title, company=company,
        description="", url="https://example.com", posted_date=None,
    )


def test_safe_filename_uses_company_and_role():
    assert safe_filename(_posting("Acme", "Backend Developer")) == "Acme_Backend_Developer.pdf"


def test_safe_filename_strips_path_separators():
    # Company names arrive from a scraper — never trust them as path components.
    name = safe_filename(_posting("Acme/Evil", "Dev\\Ops"))
    assert "/" not in name and "\\" not in name


def test_sanitize_removes_windows_reserved_characters():
    assert sanitize('a:b*c?d"e<f>g|h') == "abcdefgh"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render_canva.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'render.canva'`.

- [ ] **Step 4: Write `render/canva.py`**

Fill in the tool names and argument shapes **exactly as recorded in Spike B** — the calls below are the expected shape and must be corrected against what the spike observed.

```python
"""Render a tailored CV by copying the base Canva design and editing the copy.

The base design is never edited. Canva exposes no delete tool, so each copy is
parked in a dated run folder and the user trashes that one folder afterwards.
"""
import re
from pathlib import Path

import httpx

from models import JobPosting, TailoredCV


class CanvaError(RuntimeError):
    """A Canva operation failed for one job. The run continues without it."""


def sanitize(text: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", text)


def safe_filename(posting: JobPosting) -> str:
    company = sanitize(posting.company).strip().replace(" ", "_")
    title = sanitize(posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.pdf"


async def render(
    session, tailored: TailoredCV, posting: JobPosting,
    base_design_id: str, folder_id: str, out_dir: Path,
) -> Path:
    copy = await session.call_tool("copy-design", {"designId": base_design_id})
    copy_id = copy.content[0].text  # shape confirmed in Spike B

    transaction = None
    try:
        await session.call_tool(
            "move-item-to-folder", {"itemId": copy_id, "folderId": folder_id}
        )
        transaction = await session.call_tool("start-editing-transaction", {"designId": copy_id})
        await session.call_tool(
            "perform-editing-operations",
            {"designId": copy_id, "operations": _build_operations(tailored)},
        )
        await session.call_tool("commit-editing-transaction", {"designId": copy_id})
        transaction = None

        export = await session.call_tool(
            "export-design", {"designId": copy_id, "format": "pdf"}
        )
        pdf_url = export.content[0].text  # shape confirmed in Spike B
    except Exception as exc:
        if transaction is not None:
            # An abandoned open transaction can lock the design for later runs.
            await session.call_tool("cancel-editing-transaction", {"designId": copy_id})
        raise CanvaError(f"Canva render failed for {posting.company}: {exc}") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / safe_filename(posting)
    response = httpx.get(pdf_url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    pdf_path.write_bytes(response.content)
    return pdf_path


def _build_operations(tailored: TailoredCV) -> list[dict]:
    """Map tailored content onto the design's text elements.

    The element IDs come from get-design-content and were recorded in Spike B.
    Replace the placeholders below with the real IDs.
    """
    return [
        {"type": "replace_text", "elementId": "<summary-element-id>", "text": tailored.summary},
        {"type": "replace_text", "elementId": "<bullets-element-id>", "text": "\n".join(tailored.bullets)},
        {"type": "replace_text", "elementId": "<skills-element-id>", "text": ", ".join(tailored.skills)},
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render_canva.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add render/canva.py tests/test_render_canva.py requirements.txt
git commit -m "feat: add Canva rendering via copy-edit-export"
```

---

## Task 7b: HTML rendering (only if Spike B chose HTML)

**Skip if Spike B chose Canva.**

**Files:**
- Create: `render/html.py`, `render/cv_template.html`
- Test: `tests/test_render_html.py`

**Interfaces:**
- Consumes: `models.TailoredCV`, `models.JobPosting`
- Produces: `render(tailored: TailoredCV, posting: JobPosting, out_dir: Path) -> Path`

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:
```
jinja2>=3.1
weasyprint>=62
```
Run `.venv/Scripts/python.exe -m pip install -r requirements.txt`.

- [ ] **Step 2: Build the template**

Create `render/cv_template.html` as a Jinja2 template reproducing the user's CV layout. It must consume `summary`, `bullets`, `skills`, and the static header (name, contact details) taken from `base_cv.txt`.

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    @page { size: A4; margin: 18mm; }
    body { font-family: Georgia, serif; font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
    h1 { font-size: 20pt; margin: 0 0 2mm; }
    .contact { font-size: 9pt; color: #555; margin-bottom: 6mm; }
    h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 0.08em;
         border-bottom: 0.5pt solid #999; padding-bottom: 1mm; margin: 6mm 0 2mm; }
    ul { margin: 0; padding-left: 5mm; }
    li { margin-bottom: 1.5mm; }
  </style>
</head>
<body>
  <h1>{{ name }}</h1>
  <div class="contact">{{ contact }}</div>

  <h2>Summary</h2>
  <p>{{ summary }}</p>

  <h2>Experience</h2>
  <ul>{% for bullet in bullets %}<li>{{ bullet }}</li>{% endfor %}</ul>

  <h2>Skills</h2>
  <p>{{ skills | join(' · ') }}</p>
</body>
</html>
```

- [ ] **Step 3: Write the failing test**

`tests/test_render_html.py`:
```python
from models import JobPosting, TailoredCV
from render.html import render, render_html, safe_filename, sanitize


def _posting(company="Acme", title="Backend Developer") -> JobPosting:
    return JobPosting(
        id="job-1", title=title, company=company,
        description="", url="https://example.com", posted_date=None,
    )


def _tailored() -> TailoredCV:
    return TailoredCV(
        summary="Backend developer with Django experience.",
        bullets=["Built a REST API serving 10k requests/day."],
        skills=["Python", "Django"],
    )


def test_safe_filename_uses_company_and_role():
    assert safe_filename(_posting()) == "Acme_Backend_Developer.pdf"


def test_safe_filename_strips_path_separators():
    name = safe_filename(_posting(company="Acme/Evil", title="Dev\\Ops"))
    assert "/" not in name and "\\" not in name


def test_sanitize_removes_windows_reserved_characters():
    assert sanitize('a:b*c?d"e<f>g|h') == "abcdefgh"


def test_render_html_includes_tailored_content():
    html = render_html(_tailored(), name="Dvir", contact="dvir@example.com")
    assert "Backend developer with Django experience." in html
    assert "Built a REST API serving 10k requests/day." in html
    assert "Python" in html


def test_render_writes_a_real_pdf(tmp_path):
    path = render(_tailored(), _posting(), tmp_path, name="Dvir", contact="dvir@example.com")
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render_html.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'render.html'`.

- [ ] **Step 5: Write `render/html.py`**

```python
"""Render a tailored CV to PDF locally. No auth, no rate limits, no cleanup."""
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from models import JobPosting, TailoredCV

_TEMPLATE_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
)


def sanitize(text: str) -> str:
    """Strip characters that are illegal in Windows filenames."""
    return re.sub(r'[<>:"/\\|?*]', "", text)


def safe_filename(posting: JobPosting) -> str:
    company = sanitize(posting.company).strip().replace(" ", "_")
    title = sanitize(posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.pdf"


def render_html(tailored: TailoredCV, name: str, contact: str) -> str:
    template = _env.get_template("cv_template.html")
    return template.render(
        name=name, contact=contact,
        summary=tailored.summary, bullets=tailored.bullets, skills=tailored.skills,
    )


def render(
    tailored: TailoredCV, posting: JobPosting, out_dir: Path, name: str, contact: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / safe_filename(posting)
    HTML(string=render_html(tailored, name, contact)).write_pdf(pdf_path)
    return pdf_path
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render_html.py -v`
Expected: 5 passed. On Windows, WeasyPrint needs GTK — if it fails to import, install the GTK3 runtime and re-run before proceeding.

- [ ] **Step 7: Open the PDF and compare it to the real CV**

Render once and open the file. Adjust the template's fonts, spacing, and section order until it is close to the Canva original. This is a judgment call, not a test.

- [ ] **Step 8: Commit**

```bash
git add render/html.py render/cv_template.html tests/test_render_html.py requirements.txt
git commit -m "feat: add HTML/weasyprint CV rendering"
```

---

## Task 8: Run report

**Files:**
- Create: `report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `models.ScoredJob`, `models.GeneratedCV`
- Produces: `write_report(path: Path, generated: list[GeneratedCV], skipped: list[ScoredJob], failed: list[tuple[ScoredJob, str]]) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:
```python
from pathlib import Path

from models import GeneratedCV, JobPosting, JobScore, ScoredJob
from report import write_report


def _scored(job_id="job-1", company="Acme", junior=True, fit=80) -> ScoredJob:
    return ScoredJob(
        posting=JobPosting(
            id=job_id, title="Backend Developer", company=company,
            description="", url=f"https://linkedin.com/jobs/{job_id}", posted_date="2026-07-15",
        ),
        score=JobScore(is_junior_friendly=junior, fit_score=fit, reason="Entry level role."),
    )


def test_report_lists_generated_cvs_with_their_job_url(tmp_path):
    job = _scored()
    generated = [GeneratedCV(job=job, pdf_path=Path("output/Acme_Backend_Developer.pdf"))]
    path = tmp_path / "report.md"

    write_report(path, generated, skipped=[], failed=[])
    text = path.read_text(encoding="utf-8")

    # The URL is the point: a CV with no link back to its posting is unusable.
    assert "https://linkedin.com/jobs/job-1" in text
    assert "Acme" in text
    assert "80" in text
    assert "Entry level role." in text


def test_report_lists_skipped_jobs_with_reasons(tmp_path):
    skipped = [_scored(job_id="job-2", company="BigCorp", junior=False, fit=10)]
    path = tmp_path / "report.md"

    write_report(path, generated=[], skipped=skipped, failed=[])
    text = path.read_text(encoding="utf-8")

    assert "BigCorp" in text
    assert "Entry level role." in text


def test_report_lists_failed_jobs_with_their_error(tmp_path):
    failed = [(_scored(job_id="job-3", company="Flaky"), "Canva export timed out")]
    path = tmp_path / "report.md"

    write_report(path, generated=[], skipped=[], failed=failed)
    text = path.read_text(encoding="utf-8")

    assert "Flaky" in text
    assert "Canva export timed out" in text


def test_report_handles_an_empty_run(tmp_path):
    path = tmp_path / "report.md"
    write_report(path, generated=[], skipped=[], failed=[])
    assert path.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'report'`.

- [ ] **Step 3: Write `report.py`**

```python
"""The run's human-readable output: what was generated, skipped, and failed."""
from datetime import date
from pathlib import Path

from models import GeneratedCV, ScoredJob


def write_report(
    path: Path,
    generated: list[GeneratedCV],
    skipped: list[ScoredJob],
    failed: list[tuple[ScoredJob, str]],
) -> None:
    lines = [f"# Job search run — {date.today().isoformat()}", ""]

    lines += [f"## Generated ({len(generated)})", ""]
    if not generated:
        lines += ["_None._", ""]
    for item in generated:
        job = item.job
        lines += [
            f"### {job.posting.company} — {job.posting.title}",
            f"- **Fit score:** {job.score.fit_score}",
            f"- **Why:** {job.score.reason}",
            f"- **Apply at:** {job.posting.url}",
            f"- **CV:** `{item.pdf_path}`",
            "",
        ]

    lines += [f"## Skipped ({len(skipped)})", ""]
    if not skipped:
        lines += ["_None._", ""]
    for job in skipped:
        lines.append(
            f"- **{job.posting.company}** — {job.posting.title} "
            f"(fit {job.score.fit_score}): {job.score.reason}"
        )
    lines.append("")

    lines += [f"## Failed ({len(failed)})", ""]
    if not failed:
        lines += ["_None._", ""]
    for job, error in failed:
        lines.append(
            f"- **{job.posting.company}** — {job.posting.title}: {error} "
            f"(not marked seen; will retry next run)"
        )
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add report.py tests/test_report.py
git commit -m "feat: add run report with job URLs"
```

---

## Task 9: The pipeline

**Files:**
- Create: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: every module above.
- Produces: `select_top(scored: list[ScoredJob], top_n: int) -> tuple[list[ScoredJob], list[ScoredJob]]`, and `main()`.

- [ ] **Step 1: Write the failing test**

Only the selection logic is unit-tested. Orchestration is verified by the real run in Step 5.

`tests/test_main.py`:
```python
from main import select_top
from models import JobPosting, JobScore, ScoredJob


def _scored(job_id: str, junior: bool, fit: int) -> ScoredJob:
    return ScoredJob(
        posting=JobPosting(
            id=job_id, title="Dev", company="Acme",
            description="", url="https://example.com", posted_date=None,
        ),
        score=JobScore(is_junior_friendly=junior, fit_score=fit, reason="r"),
    )


def test_select_top_ranks_by_fit_score():
    jobs = [_scored("a", True, 50), _scored("b", True, 90), _scored("c", True, 70)]
    selected, _ = select_top(jobs, top_n=2)
    assert [j.posting.id for j in selected] == ["b", "c"]


def test_select_top_excludes_non_junior_jobs_regardless_of_fit():
    jobs = [_scored("a", False, 99), _scored("b", True, 40)]
    selected, rejected = select_top(jobs, top_n=5)
    assert [j.posting.id for j in selected] == ["b"]
    assert [j.posting.id for j in rejected] == ["a"]


def test_select_top_returns_unselected_junior_jobs_as_rejected():
    # Nothing is lost: a junior job that missed the cut is still reported.
    jobs = [_scored("a", True, 90), _scored("b", True, 80)]
    selected, rejected = select_top(jobs, top_n=1)
    assert [j.posting.id for j in selected] == ["a"]
    assert [j.posting.id for j in rejected] == ["b"]


def test_select_top_handles_fewer_jobs_than_the_cap():
    selected, rejected = select_top([_scored("a", True, 50)], top_n=5)
    assert len(selected) == 1
    assert rejected == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`.

- [ ] **Step 3: Write `main.py`**

The render import depends on Spike B: use `from render.canva import render, CanvaError` or `from render.html import render`. The version below assumes **HTML** — swap the import and the `render()` call if Spike B chose Canva.

```python
"""Entry point. Owns the sequence; every decision that needs judgment is a
call into scoring.py or tailoring.py.
"""
import argparse
import os
import sys
from datetime import date

import anthropic
from apify_client import ApifyClient
from dotenv import load_dotenv

import config
from models import GeneratedCV, ScoredJob
from render.html import render
from report import write_report
from scoring import score_job
from sources.apify import build_search_url, fetch_jobs
from state import filter_unseen, load_seen, mark_seen
from tailoring import find_invented_skills, tailor_cv


def select_top(scored: list[ScoredJob], top_n: int) -> tuple[list[ScoredJob], list[ScoredJob]]:
    """Rank junior-friendly jobs by fit and take the top N.

    Returns (selected, rejected). Rejected covers both non-junior jobs and
    junior jobs that missed the cut — both go in the report.
    """
    junior = [j for j in scored if j.score.is_junior_friendly]
    not_junior = [j for j in scored if not j.score.is_junior_friendly]
    ranked = sorted(junior, key=lambda j: j.score.fit_score, reverse=True)
    return ranked[:top_n], not_junior + ranked[top_n:]


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

    # 1-2. Search. Raises if the scraper returned nothing — abort before Claude spend.
    postings = fetch_jobs(apify, config.ROLE_QUERIES, config.COUNT_PER_QUERY)
    print(f"Fetched {len(postings)} postings.")

    # 3. Dedupe before scoring: this is the main Claude cost control.
    seen = load_seen(config.SEEN_JOBS_PATH)
    new_postings = filter_unseen(postings, seen)
    print(f"{len(new_postings)} are new ({len(postings) - len(new_postings)} already seen).")

    # 4. Score every new posting.
    scored = [score_job(claude, posting, base_cv) for posting in new_postings]

    # 5. Rank and cap.
    selected, rejected = select_top(scored, config.TOP_N)
    print(f"Selected top {len(selected)} of {len(scored)} for tailoring.")

    out_dir = config.OUTPUT_DIR / date.today().isoformat()
    generated: list[GeneratedCV] = []
    failed: list[tuple[ScoredJob, str]] = []

    # 7. Tailor and render. One failure must not poison the run.
    for job in selected:
        try:
            tailored = tailor_cv(claude, job.posting, base_cv)

            invented = find_invented_skills(tailored, base_cv)
            if invented:
                raise ValueError(f"invented skills not in base CV: {', '.join(invented)}")

            pdf_path = render(tailored, job.posting, out_dir, name=NAME, contact=CONTACT)
            generated.append(GeneratedCV(job=job, pdf_path=pdf_path))

            # 8. Mark seen only after the PDF exists on disk.
            mark_seen(config.SEEN_JOBS_PATH, job.posting.id)
            print(f"  generated {pdf_path.name}")
        except Exception as exc:
            # Not marked seen — it retries next run. Duplicate work beats a dropped job.
            failed.append((job, str(exc)))
            print(f"  FAILED {job.posting.company}: {exc}")

    # 8. Rejected jobs are marked seen so they are never re-scored.
    for job in rejected:
        mark_seen(config.SEEN_JOBS_PATH, job.posting.id)

    # 9. Report.
    write_report(out_dir / "report.md", generated, rejected, failed)
    print(f"\n{len(generated)} generated, {len(rejected)} skipped, {len(failed)} failed.")
    print(f"Report: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Add the CV header constants**

`render()` needs the candidate's name and contact line, which are not part of the tailored content. Read them from the top of `base_cv.txt` and add to `config.py`:

```python
NAME = "<the candidate's name, from base_cv.txt>"
CONTACT = "<the contact line, from base_cv.txt>"
```

Import them in `main.py` (`from config import CONTACT, NAME`). If Spike B chose Canva, skip this step — the design already carries the header.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/ -v`
Expected: every test passes, across all modules.

- [ ] **Step 6: Dry run**

Run: `.venv/Scripts/python.exe main.py --dry-run`
Expected: prints six LinkedIn URLs and a projected cost of ~$0.15, and spends nothing.

- [ ] **Step 7: Commit**

```bash
git add main.py config.py tests/test_main.py
git commit -m "feat: add pipeline entry point with ranking and dry-run"
```

---

## Task 10: First real run and manual review

No new code. This is where the system is judged.

- [ ] **Step 1: Run it**

Run: `.venv/Scripts/python.exe main.py`
Expected: fetches postings, scores them, generates up to 5 PDFs into `output/YYYY-MM-DD/`, and writes `report.md`.

- [ ] **Step 2: Read the report**

Open `output/YYYY-MM-DD/report.md`. Check that the fit scores and reasons are sensible, and that every generated entry has a working apply-at URL.

- [ ] **Step 3: Open every generated PDF**

For each one, verify: the layout is correct and not visibly broken; the summary speaks to that specific job; and **every claim is true to `base_cv.txt`**. The invented-skill check only covers the skills list — read the summary and bullets yourself. Any invented claim means `SYSTEM_PROMPT` in `tailoring.py` needs tightening.

- [ ] **Step 4: Confirm state persisted**

Run it a second time. Expected: far fewer new postings, because `state/seen_jobs.json` now filters them. This proves dedupe works across runs.

- [ ] **Step 5: If Spike B chose Canva, clean up**

Trash the `JobCVs YYYY-MM-DD` folder in Canva, and confirm the base CV design is unmodified.

- [ ] **Step 6: Record what you found**

```bash
git commit --allow-empty -m "chore: first live run — <N> generated, <M> skipped; <notes on quality>"
```
