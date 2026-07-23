# Job Search Agent

A command-line tool that finds junior software jobs in Israel, uses Claude to judge
which ones genuinely fit *you*, and writes a complete, job-tailored version of your
résumé for each strong match — so you only ever review real candidates, and every
résumé is truthful to your actual CV.

It stops at "here's a tailored résumé worth sending." It does **not** apply for you.

---

## What it does, in one run

You run one command. The agent then:

1. **Searches** LinkedIn (via Monid, which routes to Apify's harvestapi scraper) for 5 role types in Israel, entry-level filter.
2. **Scores** every posting with one Claude call — is it junior-friendly, and how well does it fit your CV (0–100)?
3. **Keeps** only jobs that are junior-friendly *and* score ≥ 70.
4. **Tailors** your résumé to each surviving job with a second Claude call — reordering and rewording only what your CV already supports.
5. **Checks** the result for invented content, then **writes** a complete tailored résumé to `output/`.

Everything is deterministic Python except two points of judgment (scoring and tailoring),
which are the only places Claude is used.

---

## The pipeline

```
base_cv.md ─┐
            ▼
   ┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
   │  1. SEARCH   │──▶│  2. SCORE    │──▶│  3. TAILOR    │──▶│  4. WRITE     │
   │  jobs.py     │   │  scoring.py  │   │  tailoring.py │   │  render.py    │
   │  (Monid)     │   │  (Claude)    │   │  (Claude)     │   │  (→ output/)  │
   └──────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
                            │                    │
                       fit < 70?            invented skill or
                        → skip              dropped entry? → drop
```

- **Search** (`jobs.py`) — the only file that knows jobs come from Monid → harvestapi. Turns messy scraper JSON into clean `JobPosting` objects.
- **Score** (`scoring.py`) — one Claude call per job. Judges seniority from the *requirements*, not the title, and scores fit only against your CV.
- **Tailor** (`tailoring.py`) — one Claude call per match, with extended reasoning. Reorders your experience/projects most-relevant-first and rewords bullets. **Never invents** — job titles, dates, and project tech lines are passed through untouched; skills and bullets are only reworded, never added.
- **Write** (`render.py`) — assembles the full résumé and writes it to `output/`.
- **Orchestration** (`main.py`) — runs the fixed sequence and enforces the two safety guards.

### Two truthfulness guards

Before any résumé is written, it must pass both:

- **Invented-skills guard** — drops the résumé if it lists any skill not in your CV.
- **Entry-coverage guard** — drops it if any job or project was silently dropped or duplicated.

A prompt asks Claude to stay truthful; these guards enforce it in code. (They check the
skills list and entry structure — you should still read the final résumé once.)

---

## Your input: `base_cv.md`

Your real résumé, written as structured markdown. The structure is what lets the tool
copy your factual sections verbatim and tailor only the rest.

```markdown
# Your Name

Contact info here (this whole block above the first ## is copied verbatim)

## About Me
A short summary. (tailored per job)

## Work Experience
### Job Title | Company
*Dates*

- A bullet describing what you did.   (reworded per job; titles/dates untouched)

## Projects
### Project Name
Tech, stack, here                     (reordered per job; name + tech untouched)

## Skills
Python, SQL, Git                      (reordered per job)

## Education
...                                   (copied verbatim)

## Languages
...                                   (copied verbatim)
```

- **Tailored sections:** About Me, Skills, Work Experience, Projects.
- **Copied verbatim:** the contact block and every other section (Education, Languages, Military Service, Volunteering, …).
- `## Section`, `### Entry`, and `- bullet` are the markers the parser relies on.

`base_cv.md` is git-ignored — it never leaves your machine.

---

## Your output: a complete tailored résumé

For each job scoring ≥ 70, the tool writes `output/{Company}_{Role}.md`:

```
- **Fit:** 82/100 — <one-line reason>
- **Apply at:** <LinkedIn URL>

---
<your complete résumé, all sections in order, only the relevant parts tailored>
```

The block above the `---` is metadata for your review; the sendable résumé starts after it.

**Output for now:** résumés land flat in `output/` — one `{Company}_{Role}.md` per job that scores
≥ 70 and passes the Israel-location filter. Re-running overwrites same-named files; there are no
dated per-day folders yet (those arrive with the daily-run phase).

---

## Setup

**Requirements:** Python 3.11+ and two API keys.

```bash
# 1. Create the virtual environment and install dependencies
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# (on macOS/Linux: .venv/bin/python -m pip install -r requirements.txt)

# 2. Add your API keys to a .env file at the repo root (git-ignored):
#    ANTHROPIC_API_KEY=sk-ant-...
#    MONID_API_KEY=monid_live_...

# 3. Put your résumé at the repo root as base_cv.md (see format above).
```

- **Anthropic key** — from the Anthropic Console (pay-per-use; a run costs cents).
- **Monid key** — from `app.monid.ai/access/api-keys`; pay-as-you-go, billed against a prepaid balance.

---

## Running it

```bash
# See the harvestapi search input and projected cost without spending anything:
.venv/Scripts/python.exe src/main.py --dry-run

# Run for real (scrapes jobs, scores each, writes tailored résumés):
.venv/Scripts/python.exe src/main.py
```

Each posting costs one Claude scoring call; each match costs one more tailoring call.
A full run scrapes ~125 jobs (~$0.19 on Monid) plus Claude usage.

**Tuning** (all in `src/config.py`): the 5 role queries, `MAX_ITEMS_PER_QUERY` (jobs per
query), `FIT_THRESHOLD` (the score a job needs to earn a résumé), and the search filters
`EXPERIENCE_LEVELS`, `POSTED_LIMIT`, and `LOCATION_KEYWORD` (keeps only Israel-located jobs).

---

## Project structure

```
src/
  config.py       tunable settings, model id, paths
  jobs.py         job source (Monid → harvestapi) — the swap seam
  monid.py        Monid API transport (run + poll) — used by jobs.py
  scoring.py      relevance scoring (Claude)
  tailoring.py    résumé tailoring + truthfulness guards (Claude)
  resume.py       parses base_cv.md into sections and entries
  render.py       assembles the complete tailored résumé
  main.py         the pipeline entry point
tests/            test suite (runs with no network — Claude/Monid are stubbed)
docs/             design specs and implementation plans
pyproject.toml    pytest config
requirements.txt  dependencies
```

Run the tests with: `.venv/Scripts/python.exe -m pytest`

---

## What it does NOT do (yet)

Deliberately out of scope for now: cross-run/daily deduplication (a seen-jobs memory across
days), ranking/top-N, PDF export, auto-apply, and scheduling. Within a single run it already
dedupes repeated postings and keeps only Israel-located jobs. The tool hands you a tailored
draft — you decide whether to send it.

---

## Tech stack

Python 3.11+ · `anthropic` (Claude, model `claude-opus-4-8`) · `requests` (Monid →
harvestapi LinkedIn scraper) · `pydantic` (structured output) · `python-dotenv` · `pytest`.
