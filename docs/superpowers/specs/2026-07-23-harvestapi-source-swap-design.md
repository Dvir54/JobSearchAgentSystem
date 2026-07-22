# Job Source Swap: Apify → Monid (harvestapi) — Design

**Date**: 2026-07-23
**Status**: Draft, awaiting review
**Builds on**: `2026-07-17-job-search-agent-design.md` (Phase 1)

## Problem

Phase 1 sources jobs by calling the Apify actor `curious_coder/linkedin-jobs-scraper`
directly via `apify-client`. We want to route job search through **Monid** (monid.ai), an
agent-native router that proxies provider tools under one balance, so the job source can be
swapped later without new API keys or client libraries.

A live investigation (2026-07-22, re-confirmed 2026-07-23) established the constraints this
design must respect:

- Monid is a **router with a markup**, not a cheaper scraper. It is chosen for convenience
  and swap-resilience, not price.
- The cheapest catalog endpoints (**TikHub** LinkedIn job search) are **broken upstream** —
  every variant returns HTTP 400 on valid input, including TikHub's own demo parameters,
  across two separate days. They are unusable and not part of this design.
- The one working LinkedIn job-search endpoint is **`apify/harvestapi/linkedin-job-search`**,
  which returned real Israeli entry-level jobs with rich, structured fields.

This design swaps the job source to Monid's `harvestapi` endpoint and nothing else. The
scoring and tailoring pipeline downstream is untouched.

## What changes, at a glance

- A new `monid.py` module owns Monid transport (run + async poll). It knows nothing about jobs.
- `jobs.py` is rewritten to build the `harvestapi` input, call Monid, and normalize results
  into the **unchanged** `JobPosting` shape. `build_search_url` is deleted.
- `config.py` gains Monid endpoint + search-filter constants; `ACTOR_ID` is removed.
- `main.py` builds a Monid HTTP session instead of an `ApifyClient`.
- `scoring.py`, `tailoring.py`, `render.py` are unchanged. Claude reasoning stays exactly
  where it is: judging each posting's fit (`scoring.py`) and writing the CV (`tailoring.py`).

## Deliberately out of scope

Deferred to later phases; do **not** build here, and do not add scaffolding for them:

- The daily "new jobs" logic (last-24h filter + persistent seen-ID store).
- Within-run deduplication of postings.
- Scheduling (the run stays a single CLI invocation).
- Dated output folders (output stays flat, as in Phase 1).
- The full failure-handling policy (see "Error handling" — an interim rule applies).
- Any TikHub swap, until that provider recovers.

## Integration mode

Direct HTTP against `https://api.monid.ai/v1` with a bearer key — **not** MCP (that targets
interactive agents), CLI, or the skill. There is no Monid Python SDK; the transport is plain
`requests`. This fits the existing headless pipeline, which already injects a client object
into `fetch_jobs`.

## New module: `monid.py`

Transport only — the reusable seam that makes a future endpoint swap (e.g. back to TikHub) a
config change rather than a rewrite. It owns the run-and-poll protocol and nothing about jobs.

- `run_and_wait(session, provider: str, endpoint: str, input: dict) -> list[dict]`
  - `POST /v1/run` with `{provider, endpoint, input}`.
  - If the response is a completed sync run, return its output items.
  - If it is `202` / `RUNNING`, poll `GET /v1/runs/{runId}` on a fixed interval until the run
    reaches `COMPLETED` or `FAILED`, or a timeout elapses.
  - Raise `RuntimeError` on run `FAILED`, on provider `providerResponse.httpStatus >= 400`, or
    on timeout. The message names the provider/endpoint so a broken source is obvious in logs.
  - Return the run's `output` as a list of raw dataset items.
- Poll interval and total timeout are module constants (harvestapi runs are asynchronous and
  can take tens of seconds).

The Monid API key is read by `main.py` and used to build the `requests` session; `monid.py`
receives the session, so it stays free of environment and secret handling.

## Job source rewrite: `jobs.py`

Still the only module that knows where jobs come from. `JobPosting` is unchanged:

```python
@dataclass(frozen=True)
class JobPosting:
    id: str
    title: str
    company: str
    description: str
    url: str
    posted_date: str | None
```

- `build_harvestapi_input(queries: list[str]) -> dict` — assembles the request body from
  config:

  ```json
  {"body": {
    "jobTitles": ["software developer","backend developer","fullstack developer","ai engineer","qa automation"],
    "locations": ["Israel"],
    "experienceLevel": ["internship","entry","associate"],
    "maxItems": 25,
    "postedLimit": "week",
    "sortBy": "date"
  }}
  ```

  All five role queries go in **one** call. harvestapi runs each `jobTitle` server-side and
  bills per query either way, so a single call means one run to poll at the same cost. The
  filter values (`experienceLevel`, `postedLimit`, `maxItems`, `locations`) come from config,
  never hardcoded here.

- `normalize_posting(raw: dict) -> JobPosting` — maps one harvestapi item to `JobPosting`:

  | `JobPosting` | harvestapi field |
  |---|---|
  | `id` | `str(raw["id"])` |
  | `title` | `raw["title"]` |
  | `company` | `raw["company"]["name"]` (nested; tolerate missing) |
  | `description` | `raw.get("descriptionText", "")` |
  | `url` | `raw["linkedinUrl"]` |
  | `posted_date` | `raw.get("postedDate")` |

- `fetch_jobs(run, queries: list[str]) -> list[JobPosting]` — builds the input, calls the
  Monid transport (`run` is `monid.run_and_wait` bound to the session), and normalizes each
  item. Raises `RuntimeError` if the result set is empty — a zero-result run means the source
  is broken or blocked, and the run must abort before any Claude spend.

- `build_search_url` is **deleted**; the `f_E=2` LinkedIn URL trick is replaced by the
  structured `experienceLevel` field.

## Config: `config.py`

Add:

```python
MONID_API_BASE = "https://api.monid.ai/v1"
MONID_PROVIDER = "apify"
MONID_ENDPOINT = "/harvestapi/linkedin-job-search"

LOCATION = "Israel"
EXPERIENCE_LEVELS = ["internship", "entry", "associate"]
POSTED_LIMIT = "week"          # interim; becomes "24h" when the daily phase lands
MAX_ITEMS_PER_QUERY = 25       # per jobTitle × location; harvestapi bills per result
```

Remove `ACTOR_ID`. Keep `ROLE_QUERIES` (the five queries) and the Claude/model constants.
`COUNT_PER_QUERY` is renamed to `MAX_ITEMS_PER_QUERY` to match harvestapi's `maxItems`.

## Pipeline: `main.py`

- Build a `requests.Session` with `Authorization: Bearer <MONID_API_KEY>` instead of
  `ApifyClient(os.environ["APIFY_API_TOKEN"])`. Pass `monid.run_and_wait` (bound to that
  session, provider, and endpoint) into `fetch_jobs`.
- Update the projected-cost print to harvestapi's math: roughly
  `queries × MAX_ITEMS_PER_QUERY` results at `$0.0015` per result plus `$0.001` per query,
  and drop the Apify free-tier message.
- The score → tailor → guard → write loop is unchanged.

## Config and secrets

- `.env` gains `MONID_API_KEY`. `APIFY_API_TOKEN` is retained but unused — a documented
  fallback reference, not read by the new code.
- `requirements.txt` drops `apify-client` and adds `requests`.

## Error handling (interim)

`fetch_jobs` raises `RuntimeError` on a provider error, a failed/timed-out run, or zero
results, aborting before any Claude spend — the Phase 1 "abort before spend" rule carried
forward. The fuller failure-handling policy (fail-loud vs. auto-fallback vs. notify) is an
explicit open decision, deferred, and not resolved by this design.

## Testing

No network in the suite; Monid transport is faked from a recorded fixture.

- Capture one **real harvestapi output** from a live run into
  `tests/fixtures/harvestapi_response.json`, so normalization is tested against the true
  output shape. A change in that shape fails a test rather than the pipeline. Capturing it is
  the one paid step (~$0.19), run once during implementation.
- `tests/test_jobs.py`: `normalize_posting` maps the new shape, including nested
  `company.name` and tolerating a missing `postedDate`; `build_harvestapi_input` includes the
  configured filters (location, experience levels, posted limit, max items) and all five
  queries; `fetch_jobs` raises on an empty result set.
- `tests/test_monid.py` (new): `run_and_wait` with a fake HTTP client — a synchronously
  completed run returns items; an async run polls then returns items; a provider `httpStatus
  >= 400`, a `FAILED` run, and a timeout each raise `RuntimeError`.
- `scoring.py`, `tailoring.py`, `render.py` and their tests are untouched and keep passing.

## Cost profile

harvestapi is per-result: `$0.0015` per job plus `$0.001` per query. A full run at
`MAX_ITEMS_PER_QUERY = 25` across five queries is roughly `125 × $0.0015 + 5 × $0.001 ≈
$0.19`. This is a small premium over the prior direct-Apify cost (~$0.125) — the accepted
price of routing through Monid. Provider errors are not charged.
