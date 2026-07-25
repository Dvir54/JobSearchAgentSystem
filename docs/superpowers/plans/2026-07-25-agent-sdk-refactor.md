# Agent SDK Refactor (Phase R1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deterministic `main.py` pipeline (which calls the Claude API directly) with one autonomous Claude Agent SDK session that drives search → judge → tailor → write via tool calls, sourcing jobs through the Monid MCP. Truthfulness and the relevance threshold stay deterministic, enforced inside a `write_resume` tool.

**Architecture:** Deterministic logic lives in a pure, SDK-free `tooling.py` (read the résumé, clean job results, and the enforcement boundary that gates + guards + renders + writes). `tools.py` wraps those as in-process Agent-SDK tools. `agent.py` configures and runs the session (instructions + Monid MCP + the in-process tools). Old orchestration and the two `messages.parse` call sites are deleted.

**Tech Stack:** Python 3.11+, `claude-agent-sdk`, `pydantic`, `pytest`. Removes direct `anthropic` and `requests` usage.

**Spec:** `docs/superpowers/specs/2026-07-25-agent-sdk-refactor-design.md`

## Global Constraints

- Python interpreter: `.venv/Scripts/python.exe`. Tests import flat module names (`pyproject.toml` sets `pythonpath = ["src"]`).
- **The agent never calls the Claude API directly.** All model use is through the Agent SDK session; there are no `client.messages.parse` calls anywhere after this refactor.
- **`write_resume` is the single enforcement boundary.** Every résumé write goes through it, and it always runs, in code: the relevance gate (`is_junior_friendly and fit_score >= FIT_THRESHOLD`), `strip_invented_skills`, `repair_entry_coverage`, then `render_output`. The agent cannot bypass it.
- Deterministic tooling (`tooling.py`) imports **no** `claude_agent_sdk` — it stays unit-testable without the SDK or any agent run.
- Monid MCP: `https://mcp.monid.ai/v1`, authenticated with `Authorization: Bearer <MONID_API_KEY>` (verified stateless, no OAuth). Search is pinned to `MONID_PROVIDER="apify"`, `MONID_ENDPOINT="/harvestapi/linkedin-job-search"` with the config-defined filters.
- `strip_invented_skills`, `repair_entry_coverage`, `TailoredCV`, `TailoredEntry` (from `tailoring.py`), `parse_resume` (from `resume.py`), and `render_output` (from `render.py`) are reused unchanged.
- `.env` keeps `MONID_API_KEY` and `ANTHROPIC_API_KEY`. Commit directly on branch `agent-sdk-refactor`; no worktrees.
- The exact `claude-agent-sdk` API is pinned in Task 1 and every SDK-touching task conforms to that verified reference — no guessed SDK signatures.

## File Structure

| File | Change |
|---|---|
| `src/tooling.py` | **Create.** Pure, SDK-free: `build_resume_view`, `clean_jobs`, `write_tailored_resume`, `safe_filename`. |
| `src/tools.py` | **Create.** In-process Agent-SDK tools wrapping `tooling.py`, plus the SDK MCP server that holds them. |
| `src/agent.py` | **Create.** Session config (instructions + Monid MCP + in-process tools + permissions) and the run entry point. |
| `src/config.py` | **Modify.** Keep search/threshold constants + Monid provider/endpoint; add MCP URL; drop model-call constants no longer used only if truly unused. |
| `src/scoring.py`, `src/main.py`, `src/monid.py` | **Delete.** |
| `src/jobs.py` | **Modify.** Keep `JobPosting`/`normalize_posting`; remove `fetch_jobs`/`build_harvestapi_input` (logic moves to `tooling.clean_jobs`). |
| `src/tailoring.py` | **Modify.** Remove `tailor_cv`, its prompt, and `build_tailoring_prompt`; keep the guards + models. |
| tests | Add `test_tooling.py`; adapt `test_jobs.py`; remove `test_scoring.py`, `test_monid.py`, and the `tailor_cv` tests. `test_guards.py`, `test_render.py`, `test_resume.py` stay. |

---

## Task 1: Pin the Agent SDK API (setup spike)

**Throwaway spike — nothing from it ships except a verified reference and the dependency.** Its job is to nail the exact `claude-agent-sdk` API so later tasks don't guess.

**Files:**
- Modify: `requirements.txt`
- Create: `spike_agent.py` (deleted at end), `docs/agent-sdk-reference.md` (kept)

- [ ] **Step 1: Add and install the SDK**

Add to `requirements.txt`:
```
claude-agent-sdk>=0.1
```
Run: `.venv/Scripts/python.exe -m pip install -r requirements.txt`
Then confirm the import and inspect the real API surface:
```bash
.venv/Scripts/python.exe -c "import claude_agent_sdk as s; print(s.__version__); print([n for n in dir(s) if not n.startswith('_')])"
```

- [ ] **Step 2: Write a minimal spike proving the three things we depend on**

Create `spike_agent.py` that verifies, against the installed version: (a) defining an **in-process tool**, (b) connecting the **Monid MCP** over HTTP with a bearer header, and (c) running a **one-shot session** where the agent calls the local tool and reaches Monid. Use the SDK's own documented constructs (the exact names come from Step 1 — `query`/`ClaudeSDKClient`, `ClaudeAgentOptions`, the tool decorator, `create_sdk_mcp_server`, and the `mcp_servers` config shape). The spike prompt should ask the agent to (1) call a trivial local `ping` tool and (2) call `monid_balance` via the Monid MCP, then stop.

Load `MONID_API_KEY`/`ANTHROPIC_API_KEY` from `.env` (`python-dotenv`).

- [ ] **Step 3: Run it and record the verified patterns**

Run: `.venv/Scripts/python.exe spike_agent.py`
Expected: the agent calls the local tool and returns the Monid balance. This spends a small amount (one short session; `monid_balance` is free). If the Monid MCP config shape or tool decorator differs from expectation, this is where you learn the truth.

Write the confirmed, copy-pasteable snippets to `docs/agent-sdk-reference.md`: the exact import names, `ClaudeAgentOptions` fields used (system prompt, `mcp_servers`, allowed tools / permission mode, max turns), the in-process tool decorator signature and return shape, `create_sdk_mcp_server` usage, and the remote-MCP (`type`/`url`/`headers`) config that worked. **Tasks 4–6 copy from this file.**

- [ ] **Step 4: Delete the spike, commit the reference + dependency**

```bash
rm spike_agent.py
git add requirements.txt docs/agent-sdk-reference.md
git commit -m "chore: pin claude-agent-sdk API via spike; add SDK dependency"
```

---

## Task 2: Read-side tooling — `build_resume_view` + `clean_jobs`

Pure functions, no SDK. `build_resume_view` gives the agent the résumé with indexed entries; `clean_jobs` normalizes + dedupes + Israel-filters raw Monid results (the logic currently inside `jobs.fetch_jobs`).

**Files:**
- Create: `src/tooling.py`, `tests/test_tooling.py`

**Interfaces:**
- Consumes: `resume.parse_resume`, `jobs.normalize_posting`, `config` (section names, `LOCATION_KEYWORD`).
- Produces:
  - `build_resume_view(base_cv_text: str) -> dict` — `{summary, skills, experience: [{index, anchor, bullets}], projects: [{index, anchor, bullets}]}`.
  - `clean_jobs(raw_items: list[dict]) -> list[dict]` — normalized, deduped by id, Israel-only job dicts.

- [ ] **Step 1: Write the failing tests**

`tests/test_tooling.py`:
```python
from tooling import build_resume_view, clean_jobs

BASE_MD = """# Cand

test@example.com

## About Me

I build things.

## Work Experience

### Backend Developer | Acme
*2024 - now*

- Built APIs in Python.

### Intern | Beta
*2023*

- Wrote scripts.

## Projects

### Todo App
Python, Flask

## Skills

Python, SQL, Docker
"""


def _raw(job_id, location, title="Developer"):
    return {"id": job_id, "title": title, "company": {"name": "Acme"},
            "descriptionText": "desc", "linkedinUrl": "https://x", "postedDate": None,
            "location": {"linkedinText": location}}


def test_build_resume_view_exposes_indexed_entries():
    view = build_resume_view(BASE_MD)
    assert view["skills"].strip() == "Python, SQL, Docker"
    exp = view["experience"]
    assert [e["index"] for e in exp] == [0, 1]
    assert exp[0]["anchor"].startswith("### Backend Developer | Acme")
    assert exp[0]["bullets"] == ["Built APIs in Python."]
    assert [p["index"] for p in view["projects"]] == [0]


def test_clean_jobs_normalizes_dedups_and_filters_israel():
    raws = [_raw("1", "Tel Aviv, Israel"), _raw("1", "Tel Aviv, Israel"), _raw("2", "EMEA")]
    jobs = clean_jobs(raws)
    assert [j["id"] for j in jobs] == ["1"]
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["url"].startswith("http")
    assert "israel" in jobs[0]["location"].lower()


def test_clean_jobs_empty_input_returns_empty():
    assert clean_jobs([]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tooling'`.

- [ ] **Step 3: Write the read-side of `src/tooling.py`**

```python
"""Deterministic tooling behind the agent's tools. No claude_agent_sdk import —
this stays unit-testable without the SDK or any agent run.
"""
from config import (
    EXPERIENCE_SECTION,
    LOCATION_KEYWORD,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobs import normalize_posting
from resume import parse_resume


def _entries(parsed, section_name):
    section = parsed.get(section_name)
    if not section:
        return []
    return [{"index": i, "anchor": e.anchor, "bullets": list(e.bullets)}
            for i, e in enumerate(section.entries)]


def build_resume_view(base_cv_text):
    """Return the résumé as the agent needs it: summary, skills, and Work
    Experience / Project entries labelled by their original index."""
    parsed = parse_resume(base_cv_text)
    summary = parsed.get(SUMMARY_SECTION)
    skills = parsed.get(SKILLS_SECTION)
    return {
        "summary": summary.body if summary else "",
        "skills": skills.body if skills else "",
        "experience": _entries(parsed, EXPERIENCE_SECTION),
        "projects": _entries(parsed, PROJECTS_SECTION),
    }


def clean_jobs(raw_items):
    """Normalize raw Monid/harvestapi items, dedupe by id (first wins), and keep
    only Israel-located postings. Mirrors the old jobs.fetch_jobs post-processing."""
    seen = set()
    jobs = []
    for item in raw_items:
        posting = normalize_posting(item)
        if posting.id in seen:
            continue
        seen.add(posting.id)
        if posting.location and LOCATION_KEYWORD in posting.location.lower():
            jobs.append({
                "id": posting.id, "title": posting.title, "company": posting.company,
                "description": posting.description, "url": posting.url,
                "posted_date": posting.posted_date, "location": posting.location,
            })
    return jobs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/tooling.py tests/test_tooling.py
git commit -m "feat: read-side agent tooling (resume view + job cleaning)"
```

---

## Task 3: The enforcement boundary — `write_tailored_resume`

The safety-critical function: the deterministic gate the agent must go through to write anything.

**Files:**
- Modify: `src/tooling.py`
- Test: `tests/test_tooling.py`

**Interfaces:**
- Consumes: `tailoring.TailoredCV`/`TailoredEntry`/`strip_invented_skills`/`repair_entry_coverage`, `resume.parse_resume`, `render.render_output`, `config` (`FIT_THRESHOLD`, `BASE_CV_PATH`, `OUTPUT_DIR`).
- Produces: `write_tailored_resume(job, score, tailored, out_dir=None) -> dict` returning `{"written": path|None, "rejected": bool, "reason": str, "corrections": list[str]}`.

- [ ] **Step 1: Add failing tests to `tests/test_tooling.py`**

```python
import json
from pathlib import Path

from tooling import write_tailored_resume


def _job():
    return {"company": "Acme", "title": "Backend Developer", "url": "https://example.com/j"}


def _score(fit=82, junior=True):
    return {"is_junior_friendly": junior, "fit_score": fit,
            "reason": "Strong match.", "match_kind": "direct"}


def _tailored():
    return {"summary": "Backend dev.", "skills": ["Python", "Kubernetes"],
            "experience": [{"entry_index": 0, "bullets": ["Reworded."]}],
            "projects": [{"entry_index": 9, "bullets": []}]}


def test_write_rejects_below_threshold(tmp_path):
    out = write_tailored_resume(_job(), _score(fit=40), _tailored(), out_dir=tmp_path)
    assert out["rejected"] is True and out["written"] is None
    assert list(tmp_path.iterdir()) == []


def test_write_gates_guards_and_reports_corrections(tmp_path):
    # base_cv used by the guards is the real config.BASE_CV_PATH; this test asserts
    # the enforcement path runs. Kubernetes is invented; entry [1] and the bad
    # project index must be repaired.
    out = write_tailored_resume(_job(), _score(), _tailored(), out_dir=tmp_path)
    assert out["rejected"] is False
    path = Path(out["written"])
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "Kubernetes" not in body.split("---", 1)[1]   # stripped from the résumé body
    assert any("Kubernetes" in c for c in out["corrections"])
    assert "Auto-corrected" in body                       # surfaced in the banner
```

(Note: this test relies on the real `base_cv.md` at the repo root, matching how the pipeline runs. If absent, the implementer should skip-guard or point `BASE_CV_PATH` at a fixture — record which.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_tailored_resume'`.

- [ ] **Step 3: Add `write_tailored_resume` + `safe_filename` to `src/tooling.py`**

```python
import re
from dataclasses import dataclass
from pathlib import Path

import config
from render import render_output
from tailoring import (
    TailoredCV,
    TailoredEntry,
    repair_entry_coverage,
    strip_invented_skills,
)


@dataclass
class _Posting:
    company: str
    title: str
    url: str


@dataclass
class _Score:
    is_junior_friendly: bool
    fit_score: int
    reason: str
    match_kind: str


def safe_filename(company, title):
    """Company/title come from a scraper — never trust them as path components."""
    c = re.sub(r'[<>:"/\\|?*]', "", company).strip().replace(" ", "_")
    t = re.sub(r'[<>:"/\\|?*]', "", title).strip().replace(" ", "_")
    return f"{c}_{t}.md"


def write_tailored_resume(job, score, tailored, out_dir=None):
    """The enforcement boundary. Gates on relevance, strips invented skills, repairs
    entry coverage, renders, and writes. Returns what it wrote or why it refused.
    The agent cannot write a résumé any other way."""
    out_dir = Path(out_dir) if out_dir else config.OUTPUT_DIR
    s = _Score(**{k: score[k] for k in ("is_junior_friendly", "fit_score", "reason", "match_kind")})

    if not (s.is_junior_friendly and s.fit_score >= config.FIT_THRESHOLD):
        return {"written": None, "rejected": True,
                "reason": f"below threshold or not junior-friendly (fit {s.fit_score})",
                "corrections": []}

    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = __import__("resume").parse_resume(base_cv)
    tcv = TailoredCV(
        summary=tailored["summary"],
        skills=list(tailored["skills"]),
        experience=[TailoredEntry(entry_index=e["entry_index"], bullets=list(e["bullets"]))
                    for e in tailored["experience"]],
        projects=[TailoredEntry(entry_index=p["entry_index"], bullets=list(p["bullets"]))
                  for p in tailored["projects"]],
    )

    tcv, removed = strip_invented_skills(tcv, base_cv)
    tcv, notes = repair_entry_coverage(tcv, parsed)
    if removed:
        notes = [f"removed unverified skills: {', '.join(removed)}"] + notes

    posting = _Posting(company=job["company"], title=job["title"], url=job["url"])
    content = render_output(posting, s, parsed, tcv, notes)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / safe_filename(job["company"], job["title"])
    path.write_text(content, encoding="utf-8")
    return {"written": str(path), "rejected": False, "reason": "", "corrections": notes}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/tooling.py tests/test_tooling.py
git commit -m "feat: write_resume enforcement boundary (gate + guards + render)"
```

---

## Task 4: Expose the tooling as Agent-SDK tools (`tools.py`)

Wrap the three `tooling.py` functions as in-process SDK tools and bundle them into an SDK MCP server. **Follow `docs/agent-sdk-reference.md` from Task 1 for the exact decorator and server API.**

**Files:**
- Create: `src/tools.py`
- Test: none unit (thin adapters; verified end-to-end by the Task 7 smoke run). A lightweight import test is included.

**Interfaces:**
- Produces: `resume_tools` — an SDK MCP server exposing `get_resume`, `filter_jobs`, `write_resume`.

- [ ] **Step 1: Write `src/tools.py`**

Structure (fill decorator/return shapes from Task 1's reference):
```python
"""Agent-SDK adapters over tooling.py. Each tool is a thin wrapper; all logic and
enforcement live in tooling.py."""
import config
import tooling
# from claude_agent_sdk import tool, create_sdk_mcp_server   # names per Task 1 reference


def _get_resume_impl():
    return tooling.build_resume_view(config.BASE_CV_PATH.read_text(encoding="utf-8"))


def _filter_jobs_impl(raw_items):
    return tooling.clean_jobs(raw_items)


def _write_resume_impl(job, score, tailored):
    return tooling.write_tailored_resume(job, score, tailored)


# @tool wrappers around the three _impl functions, returning the SDK's expected
# content shape (per Task 1 reference), bundled via create_sdk_mcp_server(...)
# into `resume_tools`.
```

- [ ] **Step 2: Import smoke test**

Add `tests/test_tools_import.py`:
```python
def test_tools_module_exposes_server():
    import tools
    assert hasattr(tools, "resume_tools")
```
Run: `.venv/Scripts/python.exe -m pytest tests/test_tools_import.py -v` → passes.

- [ ] **Step 3: Commit**

```bash
git add src/tools.py tests/test_tools_import.py
git commit -m "feat: expose deterministic tooling as in-process agent tools"
```

---

## Task 5: The agent session (`agent.py`)

Configure and run one autonomous session: instructions (relocated rubric + CV-editor rules + pinned search recipe + workflow), the Monid MCP, and the in-process tools. **Follow `docs/agent-sdk-reference.md` for the options/run API.**

**Files:**
- Create: `src/agent.py`

- [ ] **Step 1: Write the agent instructions**

Compose the system prompt from: (a) the scoring rubric currently in `scoring.py`'s `SYSTEM_PROMPT` (junior-friendliness from requirements not title; fit 0–100; `is_junior_friendly`, `match_kind` direct/stretch), (b) the CV-editor rules from `tailoring.py`'s `SYSTEM_PROMPT` (evidence matrix, no invented tech/metrics/bullets, no verbatim mirroring, no hype, reword-don't-add, reference every entry by index once), and (c) the **workflow**: call `get_resume`; call `monid_run` with the pinned recipe below and poll `monid_get_run`; call `filter_jobs`; for each job, judge fit and, for good ones, draft tailored fields and call `write_resume`; report a summary. State the pinned search recipe verbatim: provider `apify`, endpoint `/harvestapi/linkedin-job-search`, body `{jobTitles: config.ROLE_QUERIES, locations: [config.LOCATION], experienceLevel: config.EXPERIENCE_LEVELS, maxItems: config.MAX_ITEMS_PER_QUERY, postedLimit: config.POSTED_LIMIT, sortBy: "date"}`, and the rule that a résumé is only warranted for jobs that are junior-friendly and score ≥ `config.FIT_THRESHOLD`.

- [ ] **Step 2: Write the session runner**

```python
"""Entry point: run one autonomous job-search + tailoring session."""
import os
import sys

from dotenv import load_dotenv

import config
from tools import resume_tools
# from claude_agent_sdk import query, ClaudeAgentOptions   # per Task 1 reference

INSTRUCTIONS = "..."  # from Step 1

def build_options():
    """ClaudeAgentOptions per Task 1 reference: system_prompt=INSTRUCTIONS;
    mcp_servers = {monid: http url https://mcp.monid.ai/v1 with Authorization
    Bearer MONID_API_KEY, resume_tools as the in-process server}; allowed tools =
    the monid_* + get_resume/filter_jobs/write_resume; permission mode auto-accept
    those; a sane max_turns cap."""
    ...

async def main():
    load_dotenv()
    # run one session with build_options() and the goal prompt; stream/collect the
    # result; print the agent's final summary. Return 0.
    ...

if __name__ == "__main__":
    sys.exit(...)
```

- [ ] **Step 3: Dry check (no spend)**

Run: `.venv/Scripts/python.exe -c "import agent; print(agent.build_options() is not None)"`
Expected: imports and builds options with no error (no session run, no spend).

- [ ] **Step 4: Commit**

```bash
git add src/agent.py
git commit -m "feat: autonomous agent session (instructions + Monid MCP + tools)"
```

---

## Task 6: Delete the old pipeline; slim config, deps, tests

Remove everything the agent replaces, with no dangling imports.

**Files:**
- Delete: `src/main.py`, `src/scoring.py`, `src/monid.py`, `tests/test_scoring.py`, `tests/test_monid.py`
- Modify: `src/jobs.py`, `src/tailoring.py`, `src/config.py`, `requirements.txt`, `tests/test_jobs.py`, `tests/test_tailoring.py`

- [ ] **Step 1: Trim `jobs.py`** — delete `fetch_jobs` and `build_harvestapi_input`; keep `JobPosting` and `normalize_posting`. Remove now-unused imports.

- [ ] **Step 2: Trim `tailoring.py`** — delete `tailor_cv`, its `SYSTEM_PROMPT`, `build_tailoring_prompt`, and `_format_entries`; keep `TailoredCV`, `TailoredEntry`, `strip_invented_skills`, `repair_entry_coverage`, and the alias helpers. Remove now-unused imports (`CLAUDE_MODEL`, `MAX_TOKENS`, etc. if unused).

- [ ] **Step 3: Trim tests** — delete `tests/test_scoring.py` and `tests/test_monid.py`; in `tests/test_jobs.py` remove the `fetch_jobs`/`build_harvestapi_input` tests, keep the `normalize_posting` tests; in `tests/test_tailoring.py` remove the `tailor_cv`/`build_tailoring_prompt` tests, keep the guard tests.

- [ ] **Step 4: Slim `config.py` and `requirements.txt`** — keep `ROLE_QUERIES`, `LOCATION`, `EXPERIENCE_LEVELS`, `POSTED_LIMIT`, `MAX_ITEMS_PER_QUERY`, `FIT_THRESHOLD`, `LOCATION_KEYWORD`, `MONID_PROVIDER`, `MONID_ENDPOINT`, section names, paths; add `MONID_MCP_URL = "https://mcp.monid.ai/v1"`. Remove `CLAUDE_MODEL`/`MAX_TOKENS` only if no remaining reference. In `requirements.txt`, remove `anthropic` and `requests` if nothing imports them (grep first).

- [ ] **Step 5: Verify no dangling references**

Run:
```bash
grep -rEn "import anthropic|messages\.parse|from monid|import monid|from scoring|fetch_jobs|build_harvestapi_input|tailor_cv" src/ tests/
```
Expected: no matches.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass (tooling, guards, render, resume, jobs-normalize, tools-import).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete deterministic pipeline; agent + tooling replace it"
```

---

## Task 7: First live agent run

No new code. **Requires `base_cv.md` at the repo root and `MONID_API_KEY`/`ANTHROPIC_API_KEY` in `.env`.** This spends money (agent/Claude session + one Monid harvestapi run).

- [ ] **Step 1: Check the Monid balance first** — `monid_balance` (or the earlier REST check) so the run doesn't fail mid-way on funds.

- [ ] **Step 2: Run the agent**

Run: `.venv/Scripts/python.exe src/agent.py`
Expected: the agent calls `get_resume`, runs the pinned Monid search and polls it, calls `filter_jobs`, then judges each job and calls `write_resume` for strong matches — writing tailored résumés to `output/`, with `Auto-corrected` banners where guards fired.

- [ ] **Step 3: Verify the run behaved**

Confirm: the written files are genuinely Israeli junior roles; senior/irrelevant jobs were skipped (the agent's judgment); every written résumé is truthful to `base_cv.md` (guards enforced by `write_resume`); the agent did not attempt to write below-threshold jobs (or they were rejected by the gate). Compare the *behaviour* to the old pipeline's output on a similar query.

- [ ] **Step 4: Commit what you learned**

```bash
git commit --allow-empty -m "chore: first live agent run — <N> résumés; <observations, tuning>"
```

**The workflow now runs as one autonomous Agent SDK session, with truthfulness and the relevance threshold enforced deterministically inside write_resume.**

---

## Self-Review

- **Spec coverage:** SDK API pinned before use (Task 1) ✓; deterministic read tooling (Task 2) and the write enforcement boundary (Task 3) ✓; tooling exposed as SDK tools (Task 4) and the autonomous session with Monid MCP + relocated rubric/CV-editor instructions (Task 5) ✓; old pipeline + both `messages.parse` sites deleted with no dangling refs (Task 6) ✓; guards/threshold enforced inside `write_resume`, never delegated to the agent ✓; `tooling.py` SDK-free and unit-tested ✓; live verification (Task 7) ✓; Canva/1B/Q4 remain out of scope.
- **Placeholder scan:** deterministic tasks (2, 3, 6) carry complete code and tests; SDK-glue tasks (1, 4, 5) are deliberately structured around Task 1's verified reference rather than guessed signatures — the one honest exception, called out in the Global Constraints.
- **Type consistency:** `write_tailored_resume(job, score, tailored, out_dir=None) -> dict` is used identically in `tests/test_tooling.py` and `tools._write_resume_impl`; `build_resume_view`/`clean_jobs` signatures match their tests and `tools.py` callers; the reused `strip_invented_skills`/`repair_entry_coverage`/`render_output`/`parse_resume` signatures are unchanged from the current code.
