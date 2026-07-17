# AI Job-Search Agent — Phase 1 Design

**Date**: 2026-07-17
**Status**: Approved, ready for planning

## Goal

Find relevant junior software job postings in Israel, judge which ones fit, and produce a resume tailored to each of the best matches. Phase 1 ends at the tailored PDF. Auto-apply, outreach, notifications, scheduling, and any UI are out of scope.

## Architecture

A deterministic Python pipeline. `main.py` runs a fixed sequence. Claude is called only at the two points that require judgment: scoring a posting for fit, and writing the tailored content. There is no agent loop.

This reverses the earlier plan's MCP agent-loop design. The reason: the flow (search → dedupe → score → rank → tailor → render → export → mark seen) is the same every run for every job. There is no branch point where a model choosing tool order adds value, so an agent loop would only add token cost, latency, nondeterminism, and the ability to skip or repeat steps like `mark_job_seen`. The two Claude calls are the Claude usage.

Interfaces stay clean enough that an agent loop could be layered on in a later phase if real branching ever appears.

### Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `sources/apify.py` | Build LinkedIn search URLs, run the actor, return normalized `JobPosting` objects. **The swap seam** — the only file that knows where jobs come from. | `apify-client` |
| `models.py` | `JobPosting`, `ScoredJob`, `TailoredCV` dataclasses — the contracts between stages. | — |
| `scoring.py` | `score_job(posting, base_cv) -> ScoredJob`. One Claude call, structured output: junior-friendly bool, fit score, reason. | `anthropic` |
| `tailoring.py` | `tailor_cv(posting, base_cv) -> TailoredCV`. One Claude call: summary, bullets, skills. | `anthropic` |
| `render/canva.py` | `render(tailored, folder_id) -> pdf_path`. Copy → edit → commit → export → move to folder. | Canva MCP |
| `state.py` | `seen_jobs.json` read/write, atomic. | — |
| `report.py` | Writes the per-run report. | — |

**Normalization at the seam**: `sources/apify.py` returns `JobPosting` with a stable shape (`id`, `title`, `company`, `description`, `url`, `posted_date`). No downstream module ever sees Apify's raw JSON. This is what makes the JSearch fallback a one-file change rather than a rewrite.

## Data flow (one run)

1. Load `base_cv.txt` and `state/seen_jobs.json`.
2. For each of 6 role queries, build a LinkedIn search URL (`location=Israel`, `f_E=2`), run the actor, collect postings.
3. Drop postings whose ID is already in `seen_jobs.json`. Dedupe happens **before** scoring — this is the main Claude cost control.
4. Score every remaining posting (one Claude call each). Non-junior postings are discarded with a logged reason.
5. Rank by fit score; take the top **N = 5** (a config constant, raise once real numbers are known).
6. Create one Canva run folder: `JobCVs YYYY-MM-DD`.
7. For each of the top N: tailor → `copy-design` the base → edit the copy → commit → export PDF to `output/YYYY-MM-DD/{Company}_{Role}.pdf` → move the copy into the run folder.
8. Mark every posting seen, including scored-and-skipped ones, so a rejected job is never re-scored.
9. Write the run report; print a one-line summary.

Step 4 drives cost: every new posting costs one Claude call whether or not it makes the cut. Start `count` at **25** per query, not 100, until real numbers exist. First run scores the most; later runs score far fewer as `seen_jobs.json` fills.

### Role queries

`software engineer` · `backend developer` · `fullstack developer` · `QA automation engineer` · `DevOps engineer` · `cybersecurity engineer`

The word "junior" stays out of the keywords. Seniority is handled by the `f_E=2` structured filter as a prefilter, and by Claude's per-posting judgment as the real decision — postings mislabel seniority in both directions.

## Canva rendering

Verified against the Canva MCP tool list on 2026-07-17:

- `copy-design` **exists**, all plans. The base design is never edited — every job edits a copy.
- **No delete/trash tool exists**, in either the MCP server or the Connect API's Designs endpoints (only assets and folders can be deleted). Copies cannot be auto-removed.
- Mitigation: `create-folder` and `move-item-to-folder` exist, so every copy is parked in one dated run folder. Cleanup is one manual trash action per run instead of one per job.
- `autofill-design` requires **Canva Enterprise**. The paid-plan trial does not cover the free plan. Confirmed — the earlier plan's claim was correct. Editing transactions are the only available route.

**Known risk**: editing transactions require correctly locating the right text elements in the design. This is less structured than autofill and is the single largest technical unknown in rendering. Spike B proves it before any render code is written. If it fails, the fallback is an HTML/Jinja2 template rendered with `weasyprint`.

## Tailoring logic

- Read the job description: top 5–8 required skills, seniority signals, the role's core focus.
- Rewrite from `base_cv.txt` as the factual foundation. Rewrite the summary for this specific role. Reorder and lightly reword experience bullets to surface relevant skills first and mirror the posting's language.
- Reorder the skills section to emphasize technologies the posting names.
- **Constraint**: output must remain factually accurate to `base_cv.txt`. No invented technologies or experience. This constraint is enforced by a test, not just a prompt instruction.

## Error handling

A failure on one job must never poison the run or corrupt state.

- **Actor fails or returns zero results** — abort before any Claude spend. A zero-result run signals the scraper broke; it is not a normal outcome to swallow.
- **Per-job isolation** — a tailoring, render, or export failure is caught, logged to the report with its reason, and the run continues. That job is **not** marked seen, so it retries next run.
- **Mark-seen ordering** — a job is marked seen only after its PDF exists on disk, or after it is definitively skipped as non-junior. A crash mid-render means redoing that job, never silently losing it. Duplicate work is the cheaper error.
- **Atomic state writes** — write temp, then replace. A crash mid-write must not corrupt `seen_jobs.json` into a run-blocker.
- **Canva transactions** — always `cancel-editing-transaction` on failure; an abandoned open transaction can lock the design.
- **Budget guard** — Apify's free plan hard-stops at $5 and blocks until the next cycle. `main.py` prints projected result count and cost before firing, and supports `--dry-run` (build URLs, stop).

## Testing

Fixture-based, no network in the suite.

- `tests/fixtures/` holds 2–3 real job descriptions captured on the first live run: one clearly junior, one clearly senior, one ambiguous.
- **`scoring`** — the senior fixture is rejected, the junior one accepted.
- **`tailoring`** — asserts the output contains no technology absent from `base_cv.txt`. This enforces the factual-accuracy constraint, which otherwise has nothing checking it. Claude responses are stubbed from recordings; a live test runs on demand only.
- **`sources/apify.py`** — normalization tested against a saved raw actor response, so a change in the actor's output shape fails a test instead of the pipeline.
- **`state.py`** — dedupe and atomic-write behavior.

Then: manually review the first run's PDFs before trusting the system.

## Build order

Both external dependencies are unverified and each can independently kill the design. They are proven with throwaway scripts **before** any pipeline code. Spikes A and B are independent and may run in either order. Nothing from them ships.

1. **Spike A — Apify / Israel coverage.** Run the actor once against one Israeli junior query. Record result count, posting freshness, how many are genuinely entry-level, and confirm the `f_E` mapping (expected: 1 = internship, 2 = entry level, 3 = associate). If coverage disappoints, fall back to JSearch now rather than after building on it.
2. **Spike B — Canva editing.** By hand: `copy-design` the base CV, `get-design-content`, edit one text element, commit, export. Proves whether text elements can be reliably located without autofill. **Requires `base_cv.txt` and an existing Canva CV design — neither exists yet; the user will supply them when this spike is reached.**
3. Then, in order: `models` → `sources` → `state` → `scoring` → `tailoring` → `render` → `report` → `main.py`.

## Job sourcing

- **Source**: Apify actor `curious_coder/linkedin-jobs-scraper`, driven via `apify-client`.
- **Input**: the actor takes LinkedIn job-search URLs, not keywords. `count` caps jobs scraped and is the primary cost control.
- **Cost model**: pay-per-result at $1.00/1,000. The $5/month free credit is ~5,000 results/month. At `count=25` across 6 queries a run costs ~150 results ≈ $0.15.
- **Fragility (accepted)**: the actor is community-maintained, has no SLA, can break on LinkedIn markup changes, and scraping LinkedIn is contrary to LinkedIn's ToS. Accepted in exchange for ~5,000 results/month versus JSearch's ~10 requests/month, direct LinkedIn sourcing, and a real entry-level filter. This is the largest fragility in Phase 1.
- **Fallback**: JSearch (documented API, ~10 req/month) if the actor breaks or Israeli coverage disappoints. SerpApi (~$50/month) only if quota — not coverage — is the binding constraint. JSearch and SerpApi are both Google for Jobs wrappers over the same index, so neither fixes a coverage problem.
- **Mitigation**: all source knowledge stays inside `sources/apify.py`.

## Output structure

```
output/
  YYYY-MM-DD/
    {CompanyName}_{RoleTitle}.pdf   ← exported from Canva
    report.md                       ← generated + skipped, with job URLs
state/
  seen_jobs.json                    ← processed job IDs, persisted across runs
base_cv.txt                         ← base resume text (source of truth for content)
main.py
models.py
scoring.py
tailoring.py
state.py
report.py
sources/apify.py
render/canva.py
tests/fixtures/
.env                                ← secrets, not committed
.gitignore
```

**Run report** lists each generated CV with company, role, fit score, the scoring rationale, and **the job URL to apply at** — a CV with no link back to its posting is unusable — alongside skipped jobs and their reasons.

## Tech stack

- Python 3.11+ (required by `apify-client`)
- `anthropic` — Claude API for scoring and tailoring
- `apify-client` — runs the LinkedIn jobs scraper
- `python-dotenv` — loads `.env`
- Canva MCP (`mcp.canva.com/mcp`) — OAuth, no API key

## Secrets

`.env` at project root, gitignored from day one:

```
APIFY_API_TOKEN=your_token_here
ANTHROPIC_API_KEY=your_key_here
```

Canva uses OAuth via browser login — no key stored.

## Out of scope for Phase 1

Auto-applying, email notifications, dashboard or UI, scheduling (runs are triggered manually), and any role or location outside Israel.

Note for a later phase: CV submission has no open API — every ATS (Greenhouse, Lever, Workday) requires a real browser filling a real form. No public "submit application" endpoint exists.
