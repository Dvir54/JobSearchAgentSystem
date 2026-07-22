# Job Source Swap: Apify → Monid (harvestapi) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the direct Apify job source with Monid's `apify/harvestapi/linkedin-job-search` endpoint, feeding the unchanged `JobPosting` shape into the existing scoring/tailoring pipeline.

**Architecture:** A new `monid.py` owns Monid HTTP transport (run + async poll) and knows nothing about jobs. `jobs.py` is rewritten to build the harvestapi input, call Monid through an injected `run` callable, and normalize results. `config.py` gains Monid + filter constants; `main.py` builds a `requests` session instead of an `ApifyClient`. Scoring, tailoring, and rendering are untouched.

**Tech Stack:** Python 3.11+, `requests`, `pydantic`, `pytest`. Removes `apify-client`.

**Spec:** `docs/superpowers/specs/2026-07-23-harvestapi-source-swap-design.md`

## Global Constraints

- Python interpreter: `.venv/Scripts/python.exe`. Tests import flat module names (`from jobs import ...`) because `pyproject.toml` sets `pythonpath = ["src"]`.
- Monid API base: `https://api.monid.ai/v1`. Provider: `"apify"`. Endpoint: `"/harvestapi/linkedin-job-search"`.
- Auth: `Authorization: Bearer <MONID_API_KEY>` on a `requests.Session`.
- harvestapi is **asynchronous**: `POST /v1/run` may return `202`/`RUNNING`; poll `GET /v1/runs/{runId}` until `status` is `COMPLETED` or `FAILED`.
- A provider error (`providerResponse.httpStatus >= 400`), a `FAILED` run, or a timeout must raise `RuntimeError`. Provider errors are not billed.
- Search filters come only from `config`: `locations = ["Israel"]`, `experienceLevel = ["internship","entry","associate"]`, `postedLimit = "week"`, `maxItems = 25`, `sortBy = "date"`. **All five role queries go in one call.**
- `JobPosting(id, title, company, description, url, posted_date)` is a frozen dataclass and its shape does not change.
- Zero results → `RuntimeError` (abort before any Claude spend).
- No network in the test suite; Monid is faked. `scoring.py`, `tailoring.py`, `render.py` and their tests are not modified.
- `.env` is gitignored. Secrets are read only in `main.py` / throwaway capture scripts, never committed.

---

## Task 1: Monid transport (`monid.py`)

Creates the reusable run-and-poll transport plus additive config constants. Nothing existing changes behavior, so the suite stays green.

**Files:**
- Modify: `src/config.py` (additive only)
- Create: `src/monid.py`, `tests/test_monid.py`

**Interfaces:**
- Consumes: `config.MONID_API_BASE`.
- Produces: `run_and_wait(session, provider: str, endpoint: str, run_input: dict) -> list[dict]` — raises `RuntimeError` on failed/timed-out run or provider `httpStatus >= 400`.

- [ ] **Step 1: Add Monid + filter constants to `config.py`**

Append to `src/config.py` (do not remove anything yet):

```python
# --- Monid job source ---
MONID_API_BASE = "https://api.monid.ai/v1"
MONID_PROVIDER = "apify"
MONID_ENDPOINT = "/harvestapi/linkedin-job-search"

# Search filters sent to harvestapi (Layer 1 — coarse). Claude scoring is Layer 2.
LOCATION = "Israel"
EXPERIENCE_LEVELS = ["internship", "entry", "associate"]
POSTED_LIMIT = "week"          # interim window for tuning; becomes "24h" in the daily phase
MAX_ITEMS_PER_QUERY = 25       # harvestapi bills per result; maxItems per jobTitle x location
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_monid.py`:

```python
import pytest

import monid


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Records nothing; returns canned payloads. get() walks the list, then
    repeats the last entry."""

    def __init__(self, post_payload, get_payloads=None):
        self._post_payload = post_payload
        self._get_payloads = list(get_payloads or [])
        self.get_calls = 0

    def post(self, url, json=None, timeout=None):
        return FakeResp(self._post_payload)

    def get(self, url, timeout=None):
        i = min(self.get_calls, len(self._get_payloads) - 1)
        self.get_calls += 1
        return FakeResp(self._get_payloads[i])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(monid.time, "sleep", lambda s: None)


def test_sync_completed_returns_items_without_polling():
    session = FakeSession({
        "runId": "r1", "status": "COMPLETED",
        "providerResponse": {"httpStatus": 200}, "output": [{"id": 1}, {"id": 2}],
    })
    items = monid.run_and_wait(session, "apify", "/x", {"body": {}})
    assert items == [{"id": 1}, {"id": 2}]
    assert session.get_calls == 0


def test_async_polls_then_returns_items():
    session = FakeSession(
        {"runId": "r1", "status": "RUNNING"},
        get_payloads=[
            {"runId": "r1", "status": "RUNNING"},
            {"runId": "r1", "status": "COMPLETED",
             "providerResponse": {"httpStatus": 200}, "output": [{"id": 9}]},
        ],
    )
    items = monid.run_and_wait(session, "apify", "/x", {"body": {}})
    assert items == [{"id": 9}]
    assert session.get_calls == 2


def test_provider_http_error_raises():
    session = FakeSession({
        "runId": "r1", "status": "COMPLETED",
        "providerResponse": {"httpStatus": 400}, "output": None,
    })
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "tikhub", "/x", {"queryParams": {}})


def test_failed_run_raises():
    session = FakeSession({"runId": "r1", "status": "FAILED"})
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "apify", "/x", {"body": {}})


def test_timeout_raises(monkeypatch):
    monkeypatch.setattr(monid, "RUN_TIMEOUT_SECONDS", -1)  # deadline already in the past
    session = FakeSession(
        {"runId": "r1", "status": "RUNNING"},
        get_payloads=[{"runId": "r1", "status": "RUNNING"}],
    )
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "apify", "/x", {"body": {}})
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monid.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'monid'`.

- [ ] **Step 4: Write `monid.py`**

Create `src/monid.py`:

```python
"""Monid (monid.ai) transport: run an endpoint and wait for its result.

Knows nothing about jobs. This is the swap seam — pointing the pipeline at a
different Monid endpoint (e.g. TikHub, once it recovers) is a config change,
not a rewrite. harvestapi runs are asynchronous, so run_and_wait polls.
"""
import time

from config import MONID_API_BASE

POLL_INTERVAL_SECONDS = 3
RUN_TIMEOUT_SECONDS = 180


def run_and_wait(session, provider, endpoint, run_input):
    """POST /v1/run, then poll GET /v1/runs/{id} until the run finishes.

    Returns the run's output as a list of raw items. Raises RuntimeError on a
    FAILED run, a provider httpStatus >= 400, or a timeout.
    """
    resp = session.post(
        f"{MONID_API_BASE}/run",
        json={"provider": provider, "endpoint": endpoint, "input": run_input},
        timeout=60,
    )
    data = resp.json()
    run_id = data["runId"]
    status = data.get("status")

    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while status not in ("COMPLETED", "FAILED"):
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Monid run {run_id} for {provider}{endpoint} timed out "
                f"after {RUN_TIMEOUT_SECONDS}s"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        data = session.get(f"{MONID_API_BASE}/runs/{run_id}", timeout=60).json()
        status = data.get("status")

    if status == "FAILED":
        raise RuntimeError(f"Monid run {run_id} for {provider}{endpoint} FAILED")

    provider_status = (data.get("providerResponse") or {}).get("httpStatus")
    if provider_status is not None and provider_status >= 400:
        raise RuntimeError(
            f"{provider}{endpoint} provider error: httpStatus {provider_status}"
        )

    output = data.get("output")
    return output if isinstance(output, list) else []
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_monid.py -v`
Expected: 5 passed.

- [ ] **Step 6: Run the full suite (nothing else should break)**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (previous count + 5).

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/monid.py tests/test_monid.py
git commit -m "feat: add Monid run-and-poll transport"
```

---

## Task 2: Rewrite the job source (`jobs.py`)

Swaps `jobs.py` from Apify to harvestapi-via-Monid, captures a real output fixture, and rewrites its tests. After this task `main.py` is temporarily broken (it still imports the deleted `build_search_url`) — that is expected and fixed in Task 3. The test suite does not import `main.py`, so it stays green.

**Files:**
- Modify: `src/config.py` (remove `ACTOR_ID`)
- Rewrite: `src/jobs.py`, `tests/test_jobs.py`
- Create: `tests/fixtures/harvestapi_response.json`
- Delete: `tests/fixtures/actor_response.json`

**Interfaces:**
- Consumes: `config.MONID_PROVIDER`, `config.MONID_ENDPOINT`, `config.LOCATION`, `config.EXPERIENCE_LEVELS`, `config.POSTED_LIMIT`, `config.MAX_ITEMS_PER_QUERY`; `monid.run_and_wait` (via an injected callable).
- Produces:
  - `JobPosting(id, title, company, description, url, posted_date)` — frozen dataclass, unchanged.
  - `build_harvestapi_input(queries: list[str]) -> dict`
  - `normalize_posting(raw: dict) -> JobPosting`
  - `fetch_jobs(run, queries: list[str]) -> list[JobPosting]` where `run(provider, endpoint, run_input) -> list[dict]`.

- [ ] **Step 1: Remove `ACTOR_ID` from `config.py`**

Delete this line from `src/config.py`:

```python
ACTOR_ID = "curious_coder/linkedin-jobs-scraper"
```

(Leave `COUNT_PER_QUERY` for now — `main.py` still reads it until Task 3.)

- [ ] **Step 2: Write the new failing tests**

Replace the entire contents of `tests/test_jobs.py` with:

```python
import json
from pathlib import Path

import pytest

from jobs import JobPosting, build_harvestapi_input, fetch_jobs, normalize_posting

FIXTURE = Path(__file__).parent / "fixtures" / "harvestapi_response.json"


def _items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_build_harvestapi_input_includes_filters_and_all_queries():
    body = build_harvestapi_input(["backend developer", "ai engineer"])["body"]
    assert body["jobTitles"] == ["backend developer", "ai engineer"]
    assert body["locations"] == ["Israel"]
    assert body["experienceLevel"] == ["internship", "entry", "associate"]
    assert body["maxItems"] == 25
    assert body["postedLimit"] == "week"
    assert body["sortBy"] == "date"


def test_normalize_posting_produces_stable_shape():
    posting = normalize_posting(_items()[0])
    assert isinstance(posting, JobPosting)
    assert posting.id
    assert posting.title
    assert posting.company
    assert posting.url.startswith("http")


def test_normalize_posting_reads_nested_company_name():
    raw = _items()[0]
    assert normalize_posting(raw).company == raw["company"]["name"]


def test_normalize_posting_tolerates_missing_posted_date():
    raw = dict(_items()[0])
    raw.pop("postedDate", None)
    assert normalize_posting(raw).posted_date is None


def test_fetch_jobs_normalizes_via_run_callable():
    captured = {}

    def fake_run(provider, endpoint, run_input):
        captured["args"] = (provider, endpoint, run_input)
        return _items()

    postings = fetch_jobs(fake_run, ["backend developer"])
    assert len(postings) == len(_items())
    assert all(isinstance(p, JobPosting) for p in postings)
    assert captured["args"][2]["body"]["jobTitles"] == ["backend developer"]


def test_fetch_jobs_raises_on_zero_results():
    with pytest.raises(RuntimeError):
        fetch_jobs(lambda provider, endpoint, run_input: [], ["backend developer"])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: FAIL — `ImportError` for `build_harvestapi_input` (and the fixture file does not exist yet).

- [ ] **Step 4: Rewrite `jobs.py`**

Replace the entire contents of `src/jobs.py` with:

```python
"""The only module that knows where jobs come from.

Jobs are fetched through Monid (monid.ai), which routes to the Apify
harvestapi LinkedIn job-search actor. Everything downstream consumes
JobPosting; swapping the source means changing config.MONID_ENDPOINT and
this file's input/normalization, and nothing else.
"""
from dataclasses import dataclass

from config import (
    EXPERIENCE_LEVELS,
    LOCATION,
    MAX_ITEMS_PER_QUERY,
    MONID_ENDPOINT,
    MONID_PROVIDER,
    POSTED_LIMIT,
)


@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None


def build_harvestapi_input(queries):
    """Build the harvestapi request body. All queries go in one call; the
    actor runs each jobTitle server-side. Filters come from config."""
    return {
        "body": {
            "jobTitles": list(queries),
            "locations": [LOCATION],
            "experienceLevel": list(EXPERIENCE_LEVELS),
            "maxItems": MAX_ITEMS_PER_QUERY,
            "postedLimit": POSTED_LIMIT,
            "sortBy": "date",
        }
    }


def normalize_posting(raw):
    """Map one harvestapi item to JobPosting. Field names verified against a
    live harvestapi response (company is nested under 'company')."""
    company = raw.get("company") or {}
    return JobPosting(
        id=str(raw["id"]),
        title=raw["title"],
        company=company.get("name", ""),
        description=raw.get("descriptionText", ""),
        url=raw["linkedinUrl"],
        posted_date=raw.get("postedDate"),
    )


def fetch_jobs(run, queries):
    """Fetch and normalize postings via a Monid run callable.

    `run` is monid.run_and_wait bound to a session, called as
    run(provider, endpoint, run_input) -> list[dict].

    Raises RuntimeError on zero results: an empty run means the source is
    broken or blocked, not that Israel has no jobs today — abort before any
    Claude spend.
    """
    items = run(MONID_PROVIDER, MONID_ENDPOINT, build_harvestapi_input(queries))
    postings = [normalize_posting(item) for item in items]
    if not postings:
        raise RuntimeError(
            "Monid harvestapi returned zero results. The source is likely "
            "broken or blocked — check it before spending on Claude."
        )
    return postings
```

- [ ] **Step 5: Capture a real harvestapi fixture**

Requires `MONID_API_KEY` in `.env` (add the line `MONID_API_KEY=monid_live_...` if absent). This is the one paid step (~$0.19). Create a throwaway `capture_fixture.py` at the project root:

```python
# capture_fixture.py — THROWAWAY. Saves a real harvestapi response for tests.
import json
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, "src")

import config          # noqa: E402
import monid           # noqa: E402
from jobs import build_harvestapi_input  # noqa: E402

session = requests.Session()
session.headers["Authorization"] = f"Bearer {os.environ['MONID_API_KEY']}"

items = monid.run_and_wait(
    session, config.MONID_PROVIDER, config.MONID_ENDPOINT,
    build_harvestapi_input(config.ROLE_QUERIES),
)
with open("tests/fixtures/harvestapi_response.json", "w", encoding="utf-8") as f:
    json.dump(items, f, indent=2, ensure_ascii=False)
print(f"saved {len(items)} items to tests/fixtures/harvestapi_response.json")
```

Run: `.venv/Scripts/python.exe capture_fixture.py`
Expected: prints `saved N items` (N > 0) and writes the fixture. If it prints zero or raises, the source is down — stop and investigate before continuing.

Then delete the throwaway and the old fixture:

```bash
rm capture_fixture.py
git rm tests/fixtures/actor_response.json
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_jobs.py -v`
Expected: 6 passed. A `KeyError` means the harvestapi field names differ from the mapping — fix `normalize_posting`, not the fixture.

- [ ] **Step 7: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass. (`main.py` is not imported by any test; it is repaired in Task 3.)

- [ ] **Step 8: Commit**

```bash
git add src/config.py src/jobs.py tests/test_jobs.py tests/fixtures/harvestapi_response.json
git commit -m "feat: fetch jobs via Monid harvestapi endpoint"
```

---

## Task 3: Wire the pipeline (`main.py`) and dependencies

Points `main.py` at Monid, swaps the dependency, and restores a working end-to-end dry run.

**Files:**
- Rewrite: `src/main.py`
- Modify: `requirements.txt`, `src/config.py` (remove `COUNT_PER_QUERY`)

**Interfaces:**
- Consumes: `monid.run_and_wait`, `jobs.fetch_jobs`, `jobs.build_harvestapi_input`, `config.MONID_PROVIDER`, `config.MONID_ENDPOINT`, `config.MAX_ITEMS_PER_QUERY`.

- [ ] **Step 1: Update `requirements.txt`**

Replace the whole file with:

```
anthropic>=0.69
requests>=2.31
pydantic>=2.7
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2: Install `requests`**

Run: `.venv/Scripts/python.exe -m pip install -r requirements.txt`
Expected: installs `requests` without error.

- [ ] **Step 3: Remove `COUNT_PER_QUERY` from `config.py`**

Delete the `COUNT_PER_QUERY` constant and its comment from `src/config.py` (its replacement, `MAX_ITEMS_PER_QUERY`, was added in Task 1).

- [ ] **Step 4: Rewrite `main.py`**

Replace the entire contents of `src/main.py` with:

```python
"""Entry point. Owns the sequence; every decision needing judgment is a call
into scoring.py or tailoring.py. Jobs come from Monid (see jobs.py / monid.py).
"""
import argparse
import json
import os
import re
import sys

import anthropic
import requests
from dotenv import load_dotenv

import config
import monid
from jobs import JobPosting, build_harvestapi_input, fetch_jobs
from render import render_output
from resume import parse_resume
from scoring import score_job
from tailoring import find_entry_coverage_errors, find_invented_skills, tailor_cv


def safe_filename(posting: JobPosting) -> str:
    """Company names come from a scraper — never trust them as path components."""
    company = re.sub(r'[<>:"/\\|?*]', "", posting.company).strip().replace(" ", "_")
    title = re.sub(r'[<>:"/\\|?*]', "", posting.title).strip().replace(" ", "_")
    return f"{company}_{title}.md"


def write_cv(posting, score, tailored, parsed, out_dir):
    """Write one complete tailored resume: metadata block above the resume."""
    content = render_output(posting, score, parsed, tailored)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(posting)
    path.write_text(content, encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Find junior jobs in Israel and tailor CVs.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the harvestapi search input and projected cost, then exit.",
    )
    args = parser.parse_args()

    # Windows consoles default to cp1252, which cannot encode the output below.
    sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv()

    projected = len(config.ROLE_QUERIES) * config.MAX_ITEMS_PER_QUERY
    cost = projected * 0.0015 + len(config.ROLE_QUERIES) * 0.001
    print(f"Queries: {len(config.ROLE_QUERIES)} x {config.MAX_ITEMS_PER_QUERY} results")
    print(f"Projected: ~{projected} harvestapi results ≈ ${cost:.2f} via Monid")

    if args.dry_run:
        print(json.dumps(build_harvestapi_input(config.ROLE_QUERIES), indent=2))
        return 0

    if not config.BASE_CV_PATH.exists():
        print(f"error: {config.BASE_CV_PATH} not found. It is the source of truth for CV content.")
        return 1
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {os.environ['MONID_API_KEY']}"
    claude = anthropic.Anthropic()

    def run(provider, endpoint, run_input):
        return monid.run_and_wait(session, provider, endpoint, run_input)

    # 1. Search. Raises if the source returned nothing — abort before Claude spend.
    postings = fetch_jobs(run, config.ROLE_QUERIES)
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
        tailored = tailor_cv(claude, posting, parsed)

        problems = find_invented_skills(tailored, base_cv) + find_entry_coverage_errors(tailored, parsed)
        if problems:
            print(f"  DROP  {posting.company}: {'; '.join(problems)}")
            continue

        # 4. Write.
        path = write_cv(posting, score, tailored, parsed, config.OUTPUT_DIR)
        written += 1
        print(f"  WRITE [{score.fit_score:3}] ({score.match_kind:7}) {path.name}")

    print(f"\n{written} tailored CVs in {config.OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass across jobs, monid, scoring, resume, tailoring, render.

- [ ] **Step 6: Dry run (no spend)**

Run: `.venv/Scripts/python.exe src/main.py --dry-run`
Expected: prints the projected result count and cost, then the harvestapi input JSON (jobTitles, locations `["Israel"]`, experienceLevel `["internship","entry","associate"]`, postedLimit `"week"`, maxItems `25`). No import errors, spends nothing.

- [ ] **Step 7: Commit**

```bash
git add src/main.py src/config.py requirements.txt
git commit -m "feat: wire pipeline to Monid job source; drop apify-client"
```

---

## Task 4: First live run of the new source

No new code. This spends money (one harvestapi search + one Claude scoring call per posting, plus tailoring for each job clearing the threshold). **Requires `base_cv.md` at the project root and `MONID_API_KEY` in `.env`.**

- [ ] **Step 1: Run it**

Run: `.venv/Scripts/python.exe src/main.py`
Expected: fetches postings via Monid, scores each, writes a tailored `.md` for every job clearing `FIT_THRESHOLD`.

- [ ] **Step 2: Judge the results — this is the point of the swap**

For the skip lines and written files, confirm: the postings are genuinely Israeli and mostly junior/entry (harvestapi's `experienceLevel` filter plus Claude's scoring). LinkedIn mislabels seniority, so expect the odd senior role in the fetch — it should be scored low and skipped, not written. If nothing clears the threshold, or the fetch is clearly non-Israeli or non-junior, that is the finding to record.

- [ ] **Step 3: Tune the coarse filters if needed**

Adjust `config.EXPERIENCE_LEVELS`, `config.POSTED_LIMIT`, `config.MAX_ITEMS_PER_QUERY`, or `config.ROLE_QUERIES` and re-run. This is expected iteration, not a failure.

- [ ] **Step 4: Commit what you learned**

```bash
git commit --allow-empty -m "chore: first live run on Monid harvestapi — <N> CVs; <what you observed and tuned>"
```

**The job source now runs end-to-end through Monid. TikHub remains the cheaper target to revisit once that provider recovers.**

---

## Self-Review

- **Spec coverage:** `monid.py` transport (Task 1) ✓; `jobs.py` rewrite with input builder + normalization + zero-result guard (Task 2) ✓; config constants added and `ACTOR_ID`/`COUNT_PER_QUERY` removed (Tasks 1–3) ✓; `main.py` session wiring + cost print (Task 3) ✓; `.env` `MONID_API_KEY` + `requirements.txt` swap (Tasks 2–3) ✓; interim zero/error abort-before-spend ✓; fixture-based tests with faked Monid, scoring/tailoring/render untouched ✓; deferred items (daily/seen-ID, dedup, scheduling, dated output, full failure policy, TikHub) left unbuilt ✓.
- **Placeholder scan:** every code and command step contains concrete content; no TBD/TODO/"handle errors" placeholders.
- **Type consistency:** `run_and_wait(session, provider, endpoint, run_input) -> list[dict]` is used identically in `jobs.fetch_jobs`'s injected `run` and in `main.py`'s `run` closure; `build_harvestapi_input`, `normalize_posting`, and `fetch_jobs(run, queries)` signatures match across `jobs.py`, `tests/test_jobs.py`, and `main.py`; `MAX_ITEMS_PER_QUERY` replaces `COUNT_PER_QUERY` everywhere it is read.
