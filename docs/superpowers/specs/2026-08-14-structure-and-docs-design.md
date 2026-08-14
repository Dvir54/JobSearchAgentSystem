# R4 — Structure and Documentation

**Date:** 2026-08-14
**Branch:** `r4-structure-and-docs`
**Status:** design approved, plan not yet written

Four improvements, none of which change what the agent does. Three are cosmetic or
structural; one is a small behavioural change to `jobs pdf`. The agent workflow, the
hooks, the truthfulness guards and the database schema are all untouched.

---

## 1. Canva design title — no code

Every CV copy shows up in Canva as `" CV — working copy"` (with a leading space). This
is not something the agent chooses.

**Measured, 2026-08-14:** `copy-design` accepts only `design_id` and `page_numbers` —
there is **no title parameter** — and the Canva MCP exposes no rename or update-design
tool at all. A copy inherits the source design's title, and `get-design` on the pinned
template `DAHQxzJVWM4` returns exactly `" CV — working copy"`.

So the only lever is the template's own name.

**Decision:** Dvir renames design `DAHQxzJVWM4` to **"Dvir Resume"** in Canva. Every
future copy inherits it. No code changes.

**Accepted consequence:** every CV in a run's folder then carries the same name, so the
designs remain distinguishable only by the Canva links in the digest email. This is the
same limitation R2 already deferred, and it is not made worse by this change.

The expected title is recorded in `src/jobsearch/resume/README.md` so a future reader
knows the name is deliberate and where it comes from.

## 2. `jobs pdf` writes to `output/<run date>/`

Today `command_pdf` writes into the current working directory, so exported CVs land in
the repo root.

**New behaviour:** write to `output/<YYYY-MM-DD>/<pdf_filename>`, creating the directory
if needed, then open it as before.

**The date is the run's date, not today's** — read from `matches.created_at`, the row
written when the CV was made. Exporting the same job twice therefore always lands in the
same folder, so a job can never be scattered across several dated directories, and the
folder names keep meaning "the CVs found that day". On the normal path — exporting the
morning the digest arrives — this is identical to today's date.

**Changes:**

- `config.OUTPUT_DIR = PROJECT_ROOT / "output"` returns. It must carry a comment saying
  this is **not** the R3 `OUTPUT_DIR` that was deleted: that one was the system of
  record. This one is only an export destination. Postgres remains the system of record.
- `db.fetch_pdf(job_id)` returns `(payload, filename, created_date)` instead of
  `(payload, filename)`. The date comes from `matches.created_at::date`.
- `delivery/cli.py::command_pdf` joins that date onto `OUTPUT_DIR`, creates the
  directory, writes, and prints the full path.

`output/` is already in `.gitignore`.

## 3. Package restructure

`src/` holds 14 flat modules whose names sit directly on `sys.path`: `config`, `tools`,
`jobs`, `db`, `render`, `resume`. Those are generic enough to collide with an installed
distribution, and the flat list gives a reader no map of the system.

### Target layout

```
src/jobsearch/
  __init__.py
  config.py            settings, measured limits, paths
  db.py                every SQL in the system
  agent/               the autonomous session
    session.py           (was agent.py) prompt + SDK options + hook registration
    tools.py             in-process MCP tool definitions
    tooling.py           what those tools do; the payload reducer
    hooks.py             reduction + the Canva write guard
    jobs.py              normalises raw scraper JSON
  resume/              the CV domain
    base_cv.py           (was resume.py) parses base_cv.md
    tailoring.py         truthfulness guards
    canva.py             geometry, capacity, overflow
    render.py            PDF filenames
  delivery/            getting results to the operator
    cli.py               the `jobs` command, preflight, run logging
    mailer.py            digest rendering + SMTP
    scheduling.py        Task Scheduler XML
```

Two files are renamed: `agent.py` → `agent/session.py` and `resume.py` →
`resume/base_cv.py`, because `agent.agent` and `resume.resume` read badly.

Imports become `from jobsearch.resume import tailoring`. The console script becomes
`jobs = "jobsearch.delivery.cli:main"`.

### Decisions taken during design

- **`tests/` stays flat.** 17 files are discovered fine, and mirroring the package
  doubles the churn for no navigational gain.
- **`cli`'s import of the agent session stays lazy** — it already is, inside
  `_drive_session()` — which is what keeps the CLI free of an import cycle.
- **The move is its own commit**, with no behavioural change in it. A commit that only
  moves files is reviewable; one that moves and changes behaviour is not. Item 2 lands
  on top of the new layout.

### Two ways this breaks silently, and the guards

| Risk | Guard |
|---|---|
| `config.PROJECT_ROOT = Path(__file__).parent.parent` is now one level short, silently relocating `base_cv.md`, `schema.sql` and `logs/` into `src/` | A test asserting `BASE_CV_PATH.exists()` and `SCHEMA_PATH.exists()`. It fails loudly rather than letting the agent read a CV that isn't there. |
| The `jobs` console script still points at `cli:main`, so `jobs.exe` breaks and the 09:00 task dies | `pip install -e .` and then invoke the real `jobs.exe` as an explicit verification gate, in the same session as the move. |

The second is the one that matters operationally: the scheduled task runs `jobs.exe` by
absolute path every morning, and a stale entry point fails with no warning until 09:00.

## 4. Documentation

The root `README.md` currently carries everything: install, daily use, data layout,
truthfulness enforcement, project structure, tuning, troubleshooting. It is accurate and
long, and it is the first thing a visitor sees.

**Root `README.md`** becomes short and visual:

- what this is, in two sentences, and what it deliberately does not do
- a **mermaid** flow diagram of one morning — GitHub renders mermaid natively, so it
  needs no committed image that can drift out of sync with the code
- quickstart (`pip install -e .`, `.env`, `jobs setup`)
- the daily contract: exactly one email every morning, so **silence is the alarm**
- a table linking onward to the directory READMEs

**Six further READMEs**, each describing its own directory and, more importantly, the
invariant that directory exists to protect:

| File | Covers | Invariant it records |
|---|---|---|
| `src/jobsearch/README.md` | `config.py`, `db.py`, map of the subpackages | Postgres is the system of record; no SQL lives outside `db.py` |
| `src/jobsearch/agent/README.md` | the autonomous session | the enforcement boundary is the **hooks**, not the prompt |
| `src/jobsearch/resume/README.md` | the CV domain | truthfulness guards run in code, never delegated; the Canva template is named "Dvir Resume" |
| `src/jobsearch/delivery/README.md` | CLI, email, scheduling | one email every morning; silence means the scheduler itself is broken |
| `tests/README.md` | how to run, the real-Postgres fixture | a green suite is necessary but not sufficient — every real defect on this project surfaced in a live run |
| `docs/README.md` | the spec/plan record | one spec + one plan per phase, kept as the decision record |

Operational depth (data layout, tuning, troubleshooting, the `powercfg` and Docker
autostart facts from the 2026-08-14 fix) moves out of the root README into the relevant
directory README rather than being deleted.

---

## Order of work

1. Restructure (pure moves) → reinstall → **verify `jobs.exe` runs** → full suite green
2. `jobs pdf` destination change, on the new layout
3. Documentation, written last so it describes the tree that actually exists

Item 1 is a manual Canva action by Dvir and blocks nothing.

## Verification

- All 239 existing tests stay green; no test is deleted to make the move easier.
- New tests: `PROJECT_ROOT` resolves to the real repo root; `fetch_pdf` returns the run
  date; `command_pdf` writes under `output/<run date>/`.
- `jobs.exe` invoked for real after reinstalling.
- No live agent run is required — nothing in this phase touches the agent's behaviour,
  and a live run costs money. The next scheduled 09:00 run is the real check.

## Out of scope

- Any change to the agent workflow, hooks, guards, scoring or schema.
- Per-job Canva design names (impossible via MCP — see section 1).
- `requirements.txt`, which duplicates `pyproject.toml` dependencies. Noted, not touched.
- Mirroring `tests/` onto the new package layout.
