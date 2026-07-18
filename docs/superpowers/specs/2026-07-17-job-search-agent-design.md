# AI Job-Search Agent — Phase 1 Design

**Date**: 2026-07-17
**Status**: Approved, ready for implementation

## Goal

Scrape job postings, judge which are genuinely relevant to the candidate, and adapt their existing CV to each relevant one. Nothing else.

## Scope discipline

Phase 1 is deliberately minimal. Only three things are in scope: **scrape → judge relevance → adapt the CV**. Everything else waits.

Explicitly **not** built, and not scaffolded for either:

- Deduplication or run state
- Ranking, top-N caps
- Run reports
- HTML/PDF templating, PDF or Canva rendering
- Retry logic, scheduling, auto-apply, notifications, dashboards
- Any role or location outside Israel

A file, class, or directory whose only justification is a future feature does not belong in Phase 1. Adding it later is cheap; carrying it now is not.

## Architecture

A deterministic Python pipeline. `main.py` runs a fixed sequence — search → score → tailor → write. Claude is called as a plain function at the only two points needing judgment: *is this job relevant to me*, and *what should the tailored CV say*. There is no agent loop.

The flow never branches, so a model re-deriving the tool order every run would only add token cost, latency, nondeterminism, and the ability to skip steps. The two Claude calls are the Claude usage.

### Files

| File | Responsibility |
|---|---|
| `config.py` | The tunable values (`ROLE_QUERIES`, `COUNT_PER_QUERY`, `FIT_THRESHOLD`), the model ID, and paths. One place to change behavior. |
| `jobs.py` | `JobPosting`, LinkedIn URL construction, run the actor, normalize results. **The swap seam.** |
| `scoring.py` | `JobScore` + `score_job()`. One Claude call per posting. |
| `tailoring.py` | `TailoredCV` + `tailor_cv()` + the CV-editor system prompt + `find_invented_skills()`. |
| `main.py` | The pipeline. Orchestration only. |

Each type lives with the module that produces it — there is no shared `models.py`, and no package directories for single files.

**Normalization at the seam**: `jobs.py` returns `JobPosting` with a stable shape (`id`, `title`, `company`, `description`, `url`, `posted_date`). No other module sees Apify's raw JSON. This is what makes the JSearch fallback a one-file change.

## Data flow (one run)

1. Load `base_cv.txt`.
2. For each of 6 role queries: build a LinkedIn URL (`location=Israel`, `f_E=2`), run the actor, collect postings.
3. For each posting: score it (one Claude call) for junior-friendliness and fit (0–100).
4. If junior-friendly **and** fit ≥ **70**: tailor the CV to it (one Claude call).
5. Check the tailored output for invented skills. If any are found, drop it — do not write.
6. Write `output/{Company}_{Role}.md` with the tailored summary, bullets, and skills, plus the fit score and **the job URL** in a header. A CV with no link back to its posting is unusable.
7. Print a one-line summary.

Every posting costs one Claude call whether or not it clears the threshold — that is the run's main cost. Start `COUNT_PER_QUERY` at **25**, not 100.

### Role queries

`software engineer` · `backend developer` · `fullstack developer` · `QA automation engineer` · `DevOps engineer` · `cybersecurity engineer`

"Junior" stays out of the keywords. Seniority is handled by the `f_E=2` structured filter as a prefilter, and by Claude's per-posting judgment as the real decision — postings mislabel seniority in both directions.

## Relevance

A job earns a tailored CV when it is junior-friendly **and** scores ≥ 70 on fit. The scoring prompt is instructed that 70+ means the candidate could apply today and be taken seriously — not that the role is vaguely adjacent.

There is no ranking and no cap. If a run produces many CVs, that is information about the threshold; tune `FIT_THRESHOLD`.

## Tailoring

The system prompt is the heart of the project. It is built on patterns from published CV-tailoring prompts:

- **Evidence matrix first.** Extract the posting's real requirements, then find what the CV proves outright, proves partially, or does not prove at all. Rewrite only what the evidence supports; leave gaps as gaps.
- **Defensible in an interview.** Every claim must survive a follow-up question. This is a sharper constraint than "don't invent" — it also catches plausible-but-unearned inflation.
- **Never add** a technology, tool, employer, project, or metric absent from the CV. Do not imply, hint, or use adjacent phrasing to suggest one.
- **Never invent numbers.** Reuse real metrics or omit them.

Sounding natural matters as much as accuracy. The tells of a machine-tailored CV are mirroring the posting's phrasing verbatim and forcing keywords into bullets where the underlying work does not support them. The prompt forbids both, bans hype vocabulary, and requires the candidate's own voice.

**Enforcement is doubled**: the prompt instructs truthfulness, and `find_invented_skills()` checks the skills list against `base_cv.txt` before anything is written. A prompt alone is not a guarantee — but the check only covers skills, so the first run requires reading the summary and bullets by hand.

## Error handling

- **Actor returns zero results** — abort before any Claude spend. A zero-result run means the community scraper broke, not that Israel has no jobs today.
- **Invented skills detected** — drop that CV, print why, continue. Never write a CV with a claim the candidate cannot defend.
- **Budget guard** — Apify's free plan hard-stops at $5 and blocks until the next cycle. `main.py` prints the projected result count and cost before firing, and supports `--dry-run` (build URLs, stop).

## Testing

Fixture-based, no network in the suite:

- `tests/fixtures/actor_response.json` — a real raw actor response, captured in Spike A. Tests normalization, so a change in the actor's output shape fails a test instead of the pipeline.
- `tests/fixtures/jd_junior.txt`, `jd_senior.txt` — real job descriptions for scoring tests.
- **`find_invented_skills`** — the important one. Asserts that a technology absent from the base CV is flagged. This enforces the factual-accuracy constraint, which otherwise has nothing checking it.
- Claude calls are stubbed with a fake client that records the request and returns a canned parse. Tests assert the model ID is `claude-opus-4-8` and that no 400-triggering parameters are passed.

Then read the first run's output by hand before trusting it.

## Build order

1. **Spike A — Apify / Israel coverage.** Run the actor once against one Israeli junior query. Record result count, freshness, how many are genuinely entry-level, and the real field names. If coverage disappoints, fall back to JSearch now rather than after building on it.
2. `config.py` → `jobs.py` → `scoring.py` → `tailoring.py` → `main.py`.
3. **First live run.** Read every CV. Tune the prompts. **The agent is complete here.**

**Dependency on the user**: `base_cv.txt` at the project root before the first run.

## Job sourcing

- **Source**: Apify actor `curious_coder/linkedin-jobs-scraper` via `apify-client`.
- **Input**: the actor takes LinkedIn job-search URLs, not keywords. `count` caps jobs scraped and is the primary cost control.
- **Cost model**: pay-per-result at $1.00/1,000. The $5/month free credit is ~5,000 results/month. At `count=25` across 6 queries a run costs ~150 results ≈ $0.15.
- **Fragility (accepted)**: the actor is community-maintained, has no SLA, can break on LinkedIn markup changes, and scraping LinkedIn is contrary to LinkedIn's ToS. Accepted in exchange for ~5,000 results/month versus JSearch's ~10 requests/month, direct LinkedIn sourcing, and a real entry-level filter. This is the largest fragility in Phase 1.
- **Fallback**: JSearch (documented API, ~10 req/month) if the actor breaks or Israeli coverage disappoints. SerpApi (~$50/month) only if quota — not coverage — is the binding constraint. JSearch and SerpApi are both Google for Jobs wrappers over the same index, so neither fixes a coverage problem.
- **Mitigation**: all source knowledge stays inside `jobs.py`.

## Output

```
output/
  {CompanyName}_{RoleTitle}.md    ← tailored summary, bullets, skills + fit score + job URL
base_cv.txt                       ← source of truth for CV content
config.py
jobs.py
scoring.py
tailoring.py
main.py
tests/fixtures/
.env                              ← secrets, not committed
.gitignore
```

## Tech stack

- Python 3.11+ (required by `apify-client`)
- `anthropic` — Claude API for scoring and tailoring, `claude-opus-4-8`
- `apify-client` — runs the LinkedIn jobs scraper
- `pydantic` — structured output schemas
- `python-dotenv` — loads `.env`
- `pytest` — tests

## Secrets

`.env` at project root, gitignored from day one:

```
APIFY_API_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
```

## Note for a later phase

CV submission has no open API — every ATS (Greenhouse, Lever, Workday) requires a real browser filling a real form. No public "submit application" endpoint exists.
