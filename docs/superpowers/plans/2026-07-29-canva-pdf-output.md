# Canva PDF Output (Phase R2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every job scoring at or above the fit threshold, produce a tailored **PDF rendered from the real Canva résumé** instead of a markdown file.

**Architecture:** `prepare_resume` (in-process, deterministic) runs the relevance gate and truthfulness guards and returns a per-slot edit plan. The agent then drives the Canva MCP — `copy-design` → `start-editing-transaction` → `perform-editing-operations` → `commit` → `export-design` → `save_pdf`. Two hooks bracket the write: a `PreToolUse` guard on what is actually sent, and a `PostToolUse` reducer that keeps the ~13-16KB element dumps out of context and decides overflow in code. `canva.py` holds the deterministic logic (operation building, capacity computation, overflow detection) and is unit-tested without network.

**Tech Stack:** Python 3.11, `claude-agent-sdk` 0.2.128, Canva MCP (`https://mcp.canva.com/mcp`, OAuth), pytest 8 (`pythonpath = ["src"]`, `testpaths = ["tests"]`).

**Spec:** `docs/superpowers/specs/2026-07-29-canva-pdf-output-design.md`

## Global Constraints

- **Only jobs that are junior-friendly AND `fit_score >= config.FIT_THRESHOLD` (70)** get a copy, edits, or a PDF. Everything else is judged and skipped.
- **`base_cv.md` stays the content source of truth.** The truthfulness guards (`strip_invented_skills`, `repair_entry_coverage`) are unchanged and still diff against it.
- **One write mode: `replace_text`.** Every mapped element — summary, skills, each entry's bullets — is written with a single `replace_text` carrying the full new text.
- **The agent's tailored output is unchanged from today.** The summary is a plain paragraph connected to the base CV and the job, exactly as now. No new structure, no markup. `CV_EDITOR_RULES` must not gain any Canva-specific instruction.
- **Accepted cost:** `replace_text` flattens inline formatting, so the two bolded phrases inside the About Me paragraph render at normal weight. Block-level formatting (bullets, font, size, colour, alignment) is preserved — verified live.
- **Verified live and not to be re-litigated:** PDF export works on Free; element ids are stable and survive `copy-design`; `find_and_replace_text` preserves inline bold, including when the bold region itself is rewritten; batched operations on one element succeed; the Agent SDK reaches the Canva MCP headlessly; `perform-editing-operations` returns recomputed element heights **before** commit.
- **Overflow is enforced by the height check**, not by the length budget, and the check runs **in the `PostToolUse` reducer, not in the agent**. Canva does not reflow; three blocks have under 10px of slack.
- **Only elements we edited are overflow-checked.** Two elements (the IBM title and its date) already exceed their capacity in the untouched design — overlapping boxes are normal layout — so checking everything would report a false overflow on every job.
- **The agent cannot write files.** `Bash`, `Read`, `Write` and `WebFetch` stay denied, so saving a PDF and writing `index.md` go through the in-process `save_pdf` and `write_index` tools.
- **`canva.py`, `tooling.py`, `hooks.py` stay SDK-free** (no `claude_agent_sdk` import). Only `agent.py` imports it.
- **Never publish on guard failure or unresolved overflow** — `cancel-editing-transaction`, skip the job, record it in the index.
- **`max_buffer_size = 10 * 1024 * 1024`, `env={"MAX_MCP_OUTPUT_TOKENS": ...}`, `disallowed_tools`, and the Monid reduction hook all stay** exactly as they are.
- Run tests with `.venv/Scripts/python.exe -m pytest -q` from the repo root. On Windows prefix `PYTHONIOENCODING=utf-8` if output encoding errors appear.
- **Do NOT run `src/agent.py`** in Tasks 1-7. It spends real money.

---

### Task 1: Canva config + element parsing + capacity

**Files:**
- Modify: `src/config.py`
- Create: `src/canva.py`
- Test: `tests/test_canva.py` (create)

**Interfaces:**
- Produces:
  - `config.CANVA_TEMPLATE_DESIGN_ID`, `config.CANVA_PAGE_ID`, `config.CANVA_ELEMENT_MAP`, `config.CANVA_VALIDATE_ONLY_IDS`, `config.CANVA_FOLDER_PREFIX`, `config.MAX_REDRAFT_ATTEMPTS`, `config.LENGTH_BUDGET_RATIO`
  - `canva.parse_elements(richtexts) -> dict[str, dict]` — each value `{"top", "left", "width", "height", "regions": [str]}`
  - `canva.compute_capacity(elements) -> dict[str, float]` — vertical space each element may occupy before colliding with the next element below it
  - `canva.validate_map(elements, element_map, validate_only_ids) -> list[str]` — list of problems; empty means valid

- [ ] **Step 1: Write the failing tests**

Create `tests/test_canva.py`:

```python
import pytest

from canva import compute_capacity, parse_elements, validate_map

# Trimmed from a real start-editing-transaction response (design DAHQxzJVWM4).
# Coordinates are the real measured values — the capacity assertions below depend
# on them, so do not "tidy" these numbers.
PAGE = "PB5prZGGYdD17M0v"


def _rt(suffix, top, left, width, height, regions):
    return {"page_index": 1,
            "regions": [{"type": "character", "text": t} for t in regions],
            "containerElement": {"type": "TEXT",
                                 "position": {"top": top, "left": left},
                                 "dimension": {"width": width, "height": height}},
            "element_id": f"{PAGE}-{suffix}"}


def sample_richtexts():
    return [
        _rt("LBrJ8LlFHVgPZm7d", 119.31452880277371, 314.77470420257737, 470.3843897171405, 69.33332,
            ["Final-semester Computer Science student at Ben-Gurion University, "
             "graduating February 2027, with hands-on experience in Python, Java, "
             "and automation through an ",
             "IBM Research internship", ". Seeking a ",
             "junior software engineering", " role to start contributing immediately."]),
        _rt("LB1PgdLj3TKLYW0G", 229.64784880277372, 339.83113704457054, 205.811160900411, 26.46668,
            ["Work Experience"]),
        _rt("LBk2rXZgbWWq75bp", 298.0547458981735, 309.3731157833545, 418.2637740372855, 177.33332,
            ["\n", "Built an automated Python pipeline...\n", "\n", "Mapped vulnerabilities...\n"]),
        _rt("LBy14hl84Yxspf65", 485.38806589817347, 314.77470420257737, 343.99913724117414, 17.06666,
            ["Technical Advisor | Ness Technologies"]),
        _rt("LBzpBGcBgpx9yCWC", 512.9265225196851, 310.09330372114823, 452.193283416416, 69.33332,
            ["Provided technical support to multiple IDF intelligence units...\n"]),
        _rt("LBVXZQmSm0qqbjDp", 589.2346053467181, 337.5662897723453, 205.811160900411, 26.46668,
            ["Projects"]),
        _rt("LBkVtV7y5fKZMm0H", 403.8631782089754, 4.655356610722606, 178.20128833637068, 208.959984,
            ["Java\nPython\nC++\nJavaScript\nSQL\nGit\nMCP\nClaude Code\nReact"]),
        _rt("LBg8GQtPpRxyCqhn", 621.9831512089756, 42.47257495887612, 178.9831125347481, 26.46668,
            [" Volunteering"]),
        # These two ALREADY exceed their capacity in the untouched design - the title
        # and date boxes overlap the bullets block slightly by design. They are here so
        # the scoping test below is grounded in the real layout, not a contrived case.
        _rt("LB6dWjhqhy865bfK", 283.6005963411868, 315.9218369365245, 343.99913724117414, 17.06666,
            ["Software Developer Internship | IBM Research"]),
        _rt("LBm83fB0jYRwNXp0", 285.3339363411868, 666.2945256101174, 122.68472842104507, 15.33332,
            ["Mar 2026 - present"]),
    ]


def test_parse_elements_extracts_geometry_and_regions():
    els = parse_elements(sample_richtexts())
    summary = els[f"{PAGE}-LBrJ8LlFHVgPZm7d"]
    assert summary["top"] == pytest.approx(119.3145, abs=1e-3)
    assert summary["height"] == pytest.approx(69.33332, abs=1e-3)
    assert len(summary["regions"]) == 5
    assert summary["regions"][1] == "IBM Research internship"


def test_parse_elements_skips_non_text_elements():
    rts = sample_richtexts() + [{"page_index": 1, "regions": [],
                                 "containerElement": {"type": "SHAPE",
                                                      "position": {"top": 0, "left": 0},
                                                      "dimension": {"width": 296, "height": 1122}},
                                 "element_id": f"{PAGE}-LBfQHtX4rFXWPVmp"}]
    els = parse_elements(rts)
    assert f"{PAGE}-LBfQHtX4rFXWPVmp" not in els


def test_capacity_matches_measured_slack():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)

    # capacity = top of the next element below in the same column - own top.
    # Slack = capacity - current height. These are the real measured values.
    summary = f"{PAGE}-LBrJ8LlFHVgPZm7d"
    ibm = f"{PAGE}-LBk2rXZgbWWq75bp"
    ness = f"{PAGE}-LBzpBGcBgpx9yCWC"
    skills = f"{PAGE}-LBkVtV7y5fKZMm0H"

    assert caps[summary] - els[summary]["height"] == pytest.approx(41.00, abs=0.05)
    assert caps[ibm] - els[ibm]["height"] == pytest.approx(10.00, abs=0.05)
    assert caps[ness] - els[ness]["height"] == pytest.approx(6.97, abs=0.05)
    assert caps[skills] - els[skills]["height"] == pytest.approx(9.16, abs=0.05)


def test_capacity_ignores_elements_in_the_other_column():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    # Skills is in the left column; the right column's "Projects" header sits
    # below it vertically but must not constrain it.
    skills = f"{PAGE}-LBkVtV7y5fKZMm0H"
    assert caps[skills] == pytest.approx(621.9831512089756 - 403.8631782089754, abs=0.05)


def test_capacity_is_infinite_for_the_lowest_element():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    assert caps[f"{PAGE}-LBg8GQtPpRxyCqhn"] == float("inf")


def test_validate_map_passes_when_all_ids_present():
    els = parse_elements(sample_richtexts())
    element_map = {"summary": f"{PAGE}-LBrJ8LlFHVgPZm7d",
                   "skills": f"{PAGE}-LBkVtV7y5fKZMm0H"}
    assert validate_map(els, element_map, [f"{PAGE}-LB1PgdLj3TKLYW0G"]) == []


def test_validate_map_reports_missing_element_id():
    els = parse_elements(sample_richtexts())
    problems = validate_map(els, {"skills": f"{PAGE}-GONE"}, [])
    assert len(problems) == 1
    assert "GONE" in problems[0] and "skills" in problems[0]


def test_validate_map_reports_missing_validate_only_id():
    els = parse_elements(sample_richtexts())
    problems = validate_map(els, {}, [f"{PAGE}-NOPE"])
    assert len(problems) == 1
    assert "NOPE" in problems[0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canva.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'canva'`

- [ ] **Step 3: Add the Canva config**

Append to `src/config.py`:

```python
# --- Canva (Phase R2) ---
# Copies are made from a pinned TEMPLATE, not the live master résumé, so edits to
# the master cannot break a run mid-flight. Re-duplicate the master and re-validate
# the map when the résumé changes; run-start validation surfaces the drift.
CANVA_TEMPLATE_DESIGN_ID = "DAHQxzJVWM4"
CANVA_PAGE_ID = "PB5prZGGYdD17M0v"
CANVA_FOLDER_PREFIX = "Job CVs"          # folder per run: "Job CVs — 2026-07-29"


def _el(suffix):
    return f"{CANVA_PAGE_ID}-{suffix}"


# Slots the pipeline writes, each overwritten wholesale with replace_text.
CANVA_ELEMENT_MAP = {
    "summary": _el("LBrJ8LlFHVgPZm7d"),
    "skills": _el("LBkVtV7y5fKZMm0H"),
    "experience.0.bullets": _el("LBk2rXZgbWWq75bp"),
    "experience.1.bullets": _el("LBzpBGcBgpx9yCWC"),
}

# Never written — mapped only so run-start validation detects layout drift.
CANVA_VALIDATE_ONLY_IDS = [
    _el("LB6dWjhqhy865bfK"), _el("LBm83fB0jYRwNXp0"),   # experience[0] title, date
    _el("LBy14hl84Yxspf65"), _el("LBDfDPSFmCscLJyk"),   # experience[1] title, date
    _el("LBSw3MPln78BRrNQ"), _el("LBBn7RTVpPvK72YS"),   # project[0] title, tech
    _el("LBQSRXttJ86dgQdP"), _el("LBWRLc5NXj6GqzXz"),   # project[1] title, tech
    _el("LBCWJ2xXXDKgHbZT"), _el("LBY9Fc1br0rnxvPL"),   # project[2] title, tech
]

MAX_REDRAFT_ATTEMPTS = 2       # overflow redrafts per job before skipping it
# Cheap prevention only. The authoritative overflow check is the post-edit height
# comparison — skills reordering is length-preserving, so a sub-1.0 ratio would
# reject every run.
LENGTH_BUDGET_RATIO = 1.05
```

- [ ] **Step 4: Implement `canva.py`**

Create `src/canva.py`:

```python
"""Deterministic Canva logic. No claude_agent_sdk import and no network calls —
everything here is a pure function over payloads the agent's MCP calls return, so
it stays unit-testable without an agent run or a Canva account.
"""

_MIN_X_OVERLAP = 20.0     # px of horizontal overlap before two elements share a column
_MIN_Y_GAP = 1.0          # px; guards against float noise when comparing tops


def parse_elements(richtexts):
    """Index a start-editing-transaction `richtexts` array by element_id.

    Only TEXT elements are kept: shapes and image fills are not editable text and
    must not constrain the layout maths.
    """
    elements = {}
    for item in richtexts or []:
        container = item.get("containerElement") or {}
        if container.get("type") != "TEXT":
            continue
        position = container.get("position") or {}
        dimension = container.get("dimension") or {}
        elements[item["element_id"]] = {
            "top": position.get("top", 0.0),
            "left": position.get("left", 0.0),
            "width": dimension.get("width", 0.0),
            "height": dimension.get("height", 0.0),
            "regions": [r.get("text", "") for r in (item.get("regions") or [])],
        }
    return elements


def _x_overlap(a, b):
    return min(a["left"] + a["width"], b["left"] + b["width"]) - max(a["left"], b["left"])


def compute_capacity(elements):
    """Vertical space each element may occupy before colliding with the next one.

    Canva does not reflow: elements are absolutely positioned, so growing text
    overlaps whatever sits below it. Capacity is the distance to the top of the
    nearest element below that shares horizontal space; an element overflows when
    its height exceeds it.
    """
    capacity = {}
    for eid, element in elements.items():
        below = [
            other for oid, other in elements.items()
            if oid != eid
            and other["top"] > element["top"] + _MIN_Y_GAP
            and _x_overlap(element, other) > _MIN_X_OVERLAP
        ]
        capacity[eid] = (min(o["top"] for o in below) - element["top"]) if below else float("inf")
    return capacity


def validate_map(elements, element_map, validate_only_ids):
    """Check the pinned element map still matches the template. Returns problems.

    Run this before any copy or spend. A missing id means the template drifted and
    content would land in the wrong box — abort rather than guess.
    """
    problems = []
    for slot, eid in (element_map or {}).items():
        if eid not in elements:
            problems.append(f"slot {slot!r}: element_id {eid!r} not found in the design")
    for eid in validate_only_ids or []:
        if eid not in elements:
            problems.append(f"validate-only element_id {eid!r} not found in the design")
    return problems
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canva.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 85 existing + 10 new = 95 pass.

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/canva.py tests/test_canva.py
git commit -m "feat: Canva element parsing, capacity maths, and map validation

Capacity is the distance to the next element below sharing horizontal space;
Canva does not reflow, so an element overflows when its height exceeds it.
Assertions use the real measured geometry - three blocks have under 10px slack."
```

---

### Task 2: Operation building + overflow detection

**Files:**
- Modify: `src/canva.py`
- Test: `tests/test_canva.py`

**Interfaces:**
- Consumes: `canva.parse_elements`, `canva.compute_capacity` (Task 1).
- Produces:
  - `canva.build_operations(edits, element_map) -> list[dict]` — Canva `perform-editing-operations` operations
  - `canva.find_overflows(elements_after, capacity, only_ids) -> dict[str, dict]` — `{element_id: {"height", "capacity", "overflow_px"}}`, restricted to `only_ids`

`edits` maps a slot name to a **single string** of new text. Every slot is written with `replace_text`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_canva.py`:

```python
from canva import build_operations, find_overflows

SUMMARY = f"{PAGE}-LBrJ8LlFHVgPZm7d"
SKILLS = f"{PAGE}-LBkVtV7y5fKZMm0H"

MAP = {"summary": SUMMARY, "skills": SKILLS}


def test_build_operations_emits_one_replace_text_per_slot():
    ops = build_operations({"skills": "Python\nJava\nSQL"}, MAP)
    assert ops == [{"type": "replace_text", "element_id": SKILLS, "text": "Python\nJava\nSQL"}]


def test_build_operations_handles_several_slots():
    ops = build_operations({"summary": "A tailored paragraph.", "skills": "Python"}, MAP)
    assert len(ops) == 2
    assert all(o["type"] == "replace_text" for o in ops)
    assert {o["element_id"] for o in ops} == {SUMMARY, SKILLS}
    assert next(o["text"] for o in ops if o["element_id"] == SUMMARY) == "A tailored paragraph."


def test_unknown_slot_is_rejected():
    with pytest.raises(KeyError):
        build_operations({"nonexistent": "x"}, MAP)


EDITED = [SUMMARY, SKILLS]


def test_find_overflows_flags_an_edited_element_past_capacity():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    after = {k: dict(v) for k, v in els.items()}
    after[SKILLS]["height"] = caps[SKILLS] + 12.5        # 12.5px too tall

    overflows = find_overflows(after, caps, EDITED)
    assert set(overflows) == {SKILLS}
    assert overflows[SKILLS]["overflow_px"] == pytest.approx(12.5, abs=1e-6)


def test_find_overflows_empty_when_the_edited_elements_fit():
    els = parse_elements(sample_richtexts())
    assert find_overflows(els, compute_capacity(els), EDITED) == {}


def test_find_overflows_ignores_elements_we_did_not_edit():
    # The IBM title and its date ALREADY exceed their capacity in the untouched
    # design - overlapping boxes are normal layout. Checking every element would
    # report a false overflow on every single job, so only edited ids are checked.
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    title = f"{PAGE}-LB6dWjhqhy865bfK"
    assert els[title]["height"] > caps[title]            # genuinely over, by design
    assert title not in find_overflows(els, caps, EDITED)


def test_find_overflows_tolerates_infinite_capacity():
    els = parse_elements(sample_richtexts())
    caps = compute_capacity(els)
    lowest = f"{PAGE}-LBg8GQtPpRxyCqhn"
    after = {k: dict(v) for k, v in els.items()}
    after[lowest]["height"] = 9999
    assert find_overflows(after, caps, [lowest]) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canva.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_operations' from 'canva'`

- [ ] **Step 3: Implement both functions**

Append to `src/canva.py`:

```python
def build_operations(edits, element_map):
    """Turn a slot→text edit plan into Canva editing operations.

    Every slot is overwritten wholesale with replace_text. That flattens inline
    formatting inside the element — accepted, because only the About Me paragraph
    has any, and block-level formatting (bullets, font, colour) is preserved.
    """
    operations = []
    for slot, text in edits.items():
        eid = element_map[slot]             # KeyError on an unknown slot is correct
        operations.append({"type": "replace_text", "element_id": eid, "text": text})
    return operations


def find_overflows(elements_after, capacity, only_ids):
    """Edited elements whose post-edit height exceeds the space available to them.

    `perform-editing-operations` returns recomputed heights BEFORE commit, so this
    runs on the draft and the transaction can still be cancelled.

    `only_ids` is required, not optional: some elements overlap their neighbours in
    the untouched design (a title box and its bullets, for instance), so checking
    everything would report a false overflow on every job. Only what we wrote is
    our responsibility.
    """
    overflows = {}
    for eid in only_ids:
        element = elements_after.get(eid)
        if element is None:
            continue
        available = capacity.get(eid, float("inf"))
        if element["height"] > available:
            overflows[eid] = {"height": element["height"], "capacity": available,
                              "overflow_px": element["height"] - available}
    return overflows
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_canva.py -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add src/canva.py tests/test_canva.py
git commit -m "feat: build Canva edit operations and detect overflow

Every slot is overwritten wholesale with replace_text. Overflow is measured
against the recomputed heights the API returns before commit, so the transaction
can still be cancelled."
```

---

### Task 3: `prepare_resume` replaces `write_tailored_resume`

**Files:**
- Modify: `src/tooling.py` (`write_tailored_resume` at `tooling.py:105`)
- Modify: `tests/test_tooling.py`

**Interfaces:**
- Consumes: `config.CANVA_ELEMENT_MAP`, `config.LENGTH_BUDGET_RATIO` (Task 1); existing `strip_invented_skills`, `repair_entry_coverage`, `parse_resume`.
- Produces: `tooling.prepare_resume(job, score, tailored) -> dict` with keys `rejected` (bool), `reason` (str), `corrections` (list[str]), `edits` (dict slot→content).

`edits` uses the same slot names as `config.CANVA_ELEMENT_MAP`, and every value is a **single string**: `"summary"` → the tailored paragraph as written, `"skills"` → newline-joined, `"experience.N.bullets"` → newline-joined.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tooling.py`:

```python
from tooling import prepare_resume


def _tailored_ok():
    return {"summary": "Final-semester CS student with backend Python experience. "
                       "Seeking a junior backend role.",
            "skills": ["Python", "SQL"],
            "experience": [{"entry_index": 0, "bullets": ["Reworded."]}],
            "projects": [{"entry_index": 0, "bullets": []}]}


def test_prepare_rejects_below_threshold():
    out = prepare_resume(_job(), _score(fit=40), _tailored_ok())
    assert out["rejected"] is True
    assert out["edits"] == {}


def test_prepare_returns_slot_keyed_edits():
    out = prepare_resume(_job(), _score(), _tailored_ok())
    assert out["rejected"] is False
    assert out["edits"]["summary"] == _tailored_ok()["summary"]
    assert "\n" in out["edits"]["skills"]
    assert "experience.0.bullets" in out["edits"]


def test_prepare_rejects_a_summary_that_is_not_a_string():
    tailored = _tailored_ok()
    tailored["summary"] = ["not", "a", "paragraph"]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert "string" in out["reason"].lower()


def test_prepare_still_strips_invented_skills():
    tailored = _tailored_ok()
    tailored["skills"] = ["Python", "Kubernetes"]      # Kubernetes is not in base_cv
    out = prepare_resume(_job(), _score(), tailored)
    assert "Kubernetes" not in out["edits"]["skills"]
    assert any("Kubernetes" in c for c in out["corrections"])


def test_prepare_rejects_text_over_the_length_budget():
    tailored = _tailored_ok()
    tailored["experience"] = [{"entry_index": 0, "bullets": ["x" * 5000]}]
    out = prepare_resume(_job(), _score(), tailored)
    assert out["rejected"] is True
    assert "length" in out["reason"].lower() or "budget" in out["reason"].lower()


def test_prepare_writes_no_file():
    before = set(config.OUTPUT_DIR.iterdir()) if config.OUTPUT_DIR.exists() else set()
    prepare_resume(_job(), _score(), _tailored_ok())
    after = set(config.OUTPUT_DIR.iterdir()) if config.OUTPUT_DIR.exists() else set()
    assert before == after
```

Add `import config` to the test module's imports if it is not already present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: FAIL — `ImportError: cannot import name 'prepare_resume' from 'tooling'`

- [ ] **Step 3: Replace `write_tailored_resume` with `prepare_resume`**

In `src/tooling.py`, delete `write_tailored_resume` entirely (it starts at line 105) and add:

```python
def _budget_exceeded(new_text, original_text):
    return len(new_text) > len(original_text) * config.LENGTH_BUDGET_RATIO


def prepare_resume(job, score, tailored):
    """The deterministic half of the enforcement boundary.

    Gates on relevance, strips invented skills, repairs entry coverage, and checks
    the length budget, then returns a slot-keyed edit plan for the Canva writer.
    It writes nothing: the PreToolUse hook on perform-editing-operations is what
    actually holds the line, because the agent makes the Canva calls itself.
    """
    def _reject(reason):
        return {"rejected": True, "reason": reason, "corrections": [], "edits": {}}

    s = _Score(**{k: score[k] for k in ("is_junior_friendly", "fit_score", "reason", "match_kind")})
    if not (s.is_junior_friendly and s.fit_score >= config.FIT_THRESHOLD):
        return _reject(f"below threshold or not junior-friendly (fit {s.fit_score})")

    summary = tailored.get("summary")
    if not isinstance(summary, str):
        return _reject(f"summary must be a single paragraph string, got "
                       f"{type(summary).__name__}")

    skills = tailored.get("skills")
    if isinstance(skills, (list, tuple)):
        skills = list(skills)
    elif isinstance(skills, str):
        skills = [part.strip() for part in skills.split(",") if part.strip()]
    else:
        return _reject(f"skills must be a list or comma-separated string, got "
                       f"{type(skills).__name__}")

    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")
    parsed = parse_resume(base_cv)
    try:
        tcv = TailoredCV(
            summary=summary,
            skills=skills,
            experience=[TailoredEntry(entry_index=e["entry_index"], bullets=list(e["bullets"]))
                        for e in tailored["experience"]],
            projects=[TailoredEntry(entry_index=p["entry_index"], bullets=list(p["bullets"]))
                      for p in tailored["projects"]],
        )
    except KeyError as exc:
        return _reject(f"experience/project entry missing required key {exc}")

    tcv, removed = strip_invented_skills(tcv, base_cv)
    tcv, notes = repair_entry_coverage(tcv, parsed)
    if removed:
        notes = [f"removed unverified skills: {', '.join(removed)}"] + notes

    # Skills may have been stripped; rebuild the summary regions only if a skill
    # name was removed from them is NOT attempted — the guards own skills, not prose.
    edits = {"summary": summary, "skills": "\n".join(tcv.skills)}

    experience_section = parsed.get(EXPERIENCE_SECTION)
    base_entries = experience_section.entries if experience_section else []
    for entry in tcv.experience:
        slot = f"experience.{entry.entry_index}.bullets"
        if slot not in config.CANVA_ELEMENT_MAP:
            continue                      # entry exists in base_cv but not in the design
        edits[slot] = "\n".join(entry.bullets)
        original = "\n".join(base_entries[entry.entry_index].bullets)
        if _budget_exceeded(edits[slot], original):
            return _reject(
                f"slot {slot!r} exceeds the length budget "
                f"({len(edits[slot])} chars vs {len(original)} original)")

    summary_section = parsed.get(SUMMARY_SECTION)
    if summary_section and _budget_exceeded(summary, summary_section.body.strip()):
        return _reject("slot 'summary' exceeds the length budget")

    return {"rejected": False, "reason": "", "corrections": notes, "edits": edits}
```

- [ ] **Step 4: Fix the imports, and break the cycle**

In `src/tooling.py`:

- **delete** `from render import render_output`. Nothing in `tooling.py` renders markdown any more, and leaving it would create an import cycle once Task 6 makes `render.py` import `safe_filename` **from** `tooling`.

Then run `git grep -n "write_tailored_resume\|render_output" -- src/ tests/` and delete every remaining reference. Keep `safe_filename` — Task 6 reuses it for PDF filenames. Leave `render.py` itself untouched here.

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tooling.py -v`
Expected: PASS. Tests referencing the deleted `write_tailored_resume` must be deleted, not skipped.

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/tooling.py tests/test_tooling.py
git commit -m "feat: prepare_resume returns a Canva edit plan instead of writing markdown

Same gate and same guards; the output is now slot-keyed edits for the Canva
writer. Adds the length budget as cheap prevention - the authoritative overflow
check is the post-edit height comparison in canva.find_overflows."
```

---

### Task 4: `PreToolUse` guard hook on the Canva write

**Files:**
- Modify: `src/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `tooling.prepare_resume` guards indirectly via `strip_invented_skills`; `config.CANVA_ELEMENT_MAP`.
- Produces: `hooks.guard_canva_write(input, tool_use_id, context) -> dict` — async; returns `{}` to allow, or a `permissionDecision: "deny"` payload.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks.py`:

```python
import config
from tooling import strip_invented_skills  # noqa: F401  (proves the guard path is shared)


def _canva_input(operations):
    return {"hook_event_name": "PreToolUse",
            "tool_name": "mcp__canva__perform-editing-operations",
            "tool_input": {"transaction_id": "1", "page_index": 1, "operations": operations},
            "tool_use_id": "toolu_test"}


def _call_guard(operations):
    return asyncio.run(hooks.guard_canva_write(_canva_input(operations), "toolu_test", {}))


SKILLS_ID = config.CANVA_ELEMENT_MAP["skills"]["element_id"]


def test_guard_allows_skills_present_in_the_base_cv():
    out = _call_guard([{"type": "replace_text", "element_id": SKILLS_ID,
                        "text": "Python\nJava\nSQL"}])
    assert out == {}


def test_guard_denies_a_skill_absent_from_the_base_cv():
    out = _call_guard([{"type": "replace_text", "element_id": SKILLS_ID,
                        "text": "Python\nKubernetes"}])
    decision = out["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "Kubernetes" in decision["permissionDecisionReason"]


def test_guard_ignores_operations_on_unmapped_elements():
    out = _call_guard([{"type": "replace_text", "element_id": "PAGE-unmapped",
                        "text": "anything at all"}])
    assert out == {}


def test_guard_allows_a_non_canva_tool_untouched():
    other = {"hook_event_name": "PreToolUse", "tool_name": "mcp__monid__monid_run",
             "tool_input": {}, "tool_use_id": "t"}
    assert asyncio.run(hooks.guard_canva_write(other, "t", {})) == {}


def test_guard_never_raises_on_a_malformed_payload():
    bad = {"hook_event_name": "PreToolUse",
           "tool_name": "mcp__canva__perform-editing-operations",
           "tool_input": None, "tool_use_id": "t"}
    assert asyncio.run(hooks.guard_canva_write(bad, "t", {})) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: FAIL — `AttributeError: module 'hooks' has no attribute 'guard_canva_write'`

- [ ] **Step 3: Implement the guard**

Append to `src/hooks.py`:

```python
import config
from tailoring import TailoredCV, strip_invented_skills

_CANVA_WRITE_TOOL = "mcp__canva__perform-editing-operations"


def _skills_element_id():
    return config.CANVA_ELEMENT_MAP["skills"]["element_id"]


async def guard_canva_write(input, tool_use_id, context):
    """Deny a Canva write that would publish a skill the base CV does not support.

    prepare_resume computes the correct text, but the AGENT makes the Canva calls —
    an in-process tool cannot call an MCP server — so this hook is the real
    enforcement boundary. It inspects what is actually about to be written.

    Returns {} to allow. Anything it cannot fully parse is allowed through
    unchanged: this hook exists to catch untruthful content, not to become a new
    way for the run to fail.
    """
    if input.get("tool_name") != _CANVA_WRITE_TOOL:
        return {}

    tool_input = input.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    operations = tool_input.get("operations")
    if not isinstance(operations, list):
        return {}

    skills_id = _skills_element_id()
    base_cv = config.BASE_CV_PATH.read_text(encoding="utf-8")

    for operation in operations:
        if not isinstance(operation, dict) or operation.get("element_id") != skills_id:
            continue
        text = operation.get("text") or operation.get("replace_text") or ""
        claimed = [line.strip() for line in text.split("\n") if line.strip()]
        if not claimed:
            continue
        probe = TailoredCV(summary="", skills=claimed, experience=[], projects=[])
        _, removed = strip_invented_skills(probe, base_cv)
        if removed:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"These skills are not supported by base_cv.md and must not be "
                        f"published: {', '.join(removed)}. Cancel the transaction, drop "
                        f"them, and try again."),
                }
            }
    return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/hooks.py tests/test_hooks.py
git commit -m "feat: PreToolUse guard on the Canva write

The agent makes the Canva calls itself, so prepare_resume alone cannot hold the
line. This hook inspects the text actually being sent and denies a write that
would publish skills the base CV does not support."
```

---

### Task 5: `PostToolUse` reducer for the Canva responses

**Files:**
- Modify: `src/hooks.py`
- Test: `tests/test_hooks.py`

**Why:** `start-editing-transaction` and `perform-editing-operations` each return the *entire*
design — every element, every text region, every coordinate, roughly 13-16 KB. That is two
full dumps per job, and because context is re-sent every turn they compound exactly the way
the Monid payload did. The agent needs none of it: the element map is pinned in config.

This hook keeps the geometry in Python and hands the model a few hundred bytes. It also
moves the **overflow decision out of the agent** — an LLM comparing floats is precisely the
kind of check that fails silently. `canva.find_overflows` decides, in code, every time.

**Interfaces:**
- Consumes: `canva.parse_elements`, `canva.compute_capacity`, `canva.find_overflows` (Tasks 1-2).
- Produces: `hooks.reduce_canva_output(input, tool_use_id, context) -> dict` — async `PostToolUse` callback.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks.py` (add `import json` and `import pytest` to the module imports if not already present):

```python
PAGE = "PB5prZGGYdD17M0v"
SUMMARY_EL = f"{PAGE}-LBrJ8LlFHVgPZm7d"
SKILLS_EL = f"{PAGE}-LBkVtV7y5fKZMm0H"


def _post(tool_name, tool_response, tool_input=None):
    return {"hook_event_name": "PostToolUse", "tool_name": tool_name,
            "tool_input": tool_input or {}, "tool_response": tool_response,
            "tool_use_id": "toolu_test"}


def _reduce(tool_name, tool_response, tool_input=None):
    return asyncio.run(hooks.reduce_canva_output(
        _post(tool_name, tool_response, tool_input), "toolu_test", {}))


def _rt_el(eid, top, left, width, height, texts):
    return {"regions": [{"type": "character", "text": t} for t in texts],
            "containerElement": {"type": "TEXT",
                                 "position": {"top": top, "left": left},
                                 "dimension": {"width": width, "height": height}},
            "element_id": eid}


def _start_payload(summary_height=69.33332, skills_height=208.959984):
    return json.dumps({
        "transaction": {"status": "open", "transaction_id": "TX1"},
        "richtexts": [
            _rt_el(SUMMARY_EL, 119.31, 314.77, 470.38, summary_height, ["a"]),
            _rt_el(f"{PAGE}-header", 229.64, 339.83, 205.81, 26.46, ["Work Experience"]),
            _rt_el(SKILLS_EL, 403.86, 4.65, 178.20, skills_height, ["Java"]),
            _rt_el(f"{PAGE}-vol", 621.98, 42.47, 178.98, 26.46, [" Volunteering"]),
        ],
        "pages": [{"page_id": PAGE, "page_number": 1, "is_responsive": False}],
    })


def test_start_transaction_is_reduced_to_ids_only():
    out = _reduce("mcp__canva__start-editing-transaction", _start_payload())
    text = out["hookSpecificOutput"]["updatedToolOutput"][0]["text"]
    payload = json.loads(text)
    assert payload["transaction_id"] == "TX1"
    assert payload["page_id"] == PAGE
    # the element dump must NOT reach the model
    assert "richtexts" not in payload
    assert "containerElement" not in text
    assert len(text) < 400


def test_perform_operations_reports_ok_when_everything_fits():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    out = _reduce("mcp__canva__perform-editing-operations", _start_payload(),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "Python"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is True
    assert payload["overflow"] == {}


def test_perform_operations_reports_overflow_with_the_pixel_amount():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    # skills capacity is 621.98 - 403.86 = 218.12; make it 20px taller than that
    out = _reduce("mcp__canva__perform-editing-operations",
                  _start_payload(skills_height=238.12),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "far too many skills"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is False
    assert payload["overflow"][SKILLS_EL] == pytest.approx(20.0, abs=0.05)


def test_perform_operations_only_judges_the_elements_it_edited():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    # the summary is wildly over its capacity, but we only edited skills
    out = _reduce("mcp__canva__perform-editing-operations",
                  _start_payload(summary_height=9999),
                  {"transaction_id": "TX1",
                   "operations": [{"type": "replace_text", "element_id": SKILLS_EL,
                                   "text": "Python"}]})
    payload = json.loads(out["hookSpecificOutput"]["updatedToolOutput"][0]["text"])
    assert payload["ok"] is True


def test_commit_releases_the_stored_geometry():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    assert "TX1" in hooks._CAPACITY_BY_TRANSACTION
    _reduce("mcp__canva__commit-editing-transaction", "{}", {"transaction_id": "TX1"})
    assert "TX1" not in hooks._CAPACITY_BY_TRANSACTION


def test_cancel_releases_the_stored_geometry():
    _reduce("mcp__canva__start-editing-transaction", _start_payload())
    _reduce("mcp__canva__cancel-editing-transaction", "{}", {"transaction_id": "TX1"})
    assert "TX1" not in hooks._CAPACITY_BY_TRANSACTION


def test_perform_without_a_known_transaction_passes_through():
    # No start seen for this id - do not invent an overflow verdict.
    assert _reduce("mcp__canva__perform-editing-operations", _start_payload(),
                   {"transaction_id": "UNKNOWN", "operations": []}) == {}


def test_unparseable_canva_payload_passes_through():
    assert _reduce("mcp__canva__start-editing-transaction", "not json") == {}


def test_non_canva_tool_is_untouched():
    assert _reduce("mcp__monid__monid_run", "{}") == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: FAIL — `AttributeError: module 'hooks' has no attribute 'reduce_canva_output'`

- [ ] **Step 3: Implement the reducer**

Append to `src/hooks.py`:

```python
import canva

_CANVA_START = "mcp__canva__start-editing-transaction"
_CANVA_PERFORM = "mcp__canva__perform-editing-operations"
_CANVA_END = ("mcp__canva__commit-editing-transaction",
              "mcp__canva__cancel-editing-transaction")

# transaction_id -> {element_id: capacity_px}, held here so the geometry never
# reaches the model. Released when the transaction commits or cancels.
_CAPACITY_BY_TRANSACTION = {}


def _slim(payload):
    return {"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "updatedToolOutput": [{"type": "text",
                               "text": json.dumps(payload, ensure_ascii=False)}],
    }}


def _parse_payload(tool_response):
    if isinstance(tool_response, dict):
        return tool_response
    try:
        return json.loads(tool_response)
    except (TypeError, ValueError):
        return None


async def reduce_canva_output(input, tool_use_id, context):
    """Replace the Canva element dumps with the few facts the agent needs.

    start-editing-transaction and perform-editing-operations each return the whole
    design (~13-16KB, twice per job). The agent needs a transaction id and an
    overflow verdict; the element map is already pinned in config. Holding the
    geometry here also lets `canva.find_overflows` make the overflow call in code,
    rather than the model comparing floats.

    Anything it cannot fully vouch for passes through untouched.
    """
    name = input.get("tool_name")

    if name in _CANVA_END:
        transaction_id = (input.get("tool_input") or {}).get("transaction_id")
        _CAPACITY_BY_TRANSACTION.pop(transaction_id, None)
        return {}

    if name == _CANVA_START:
        run = _parse_payload(input.get("tool_response"))
        if not isinstance(run, dict) or "richtexts" not in run:
            print("[canva] DECLINED: start-editing-transaction payload not understood; "
                  "passing through unreduced", file=sys.stderr)
            return {}
        transaction_id = (run.get("transaction") or {}).get("transaction_id")
        elements = canva.parse_elements(run["richtexts"])
        _CAPACITY_BY_TRANSACTION[transaction_id] = canva.compute_capacity(elements)
        pages = run.get("pages") or [{}]
        print(f"[canva] transaction {transaction_id}: {len(elements)} elements, "
              f"geometry retained in-process", file=sys.stderr)
        return _slim({"transaction_id": transaction_id,
                      "page_id": pages[0].get("page_id"),
                      "element_count": len(elements)})

    if name == _CANVA_PERFORM:
        tool_input = input.get("tool_input") or {}
        transaction_id = tool_input.get("transaction_id")
        capacity = _CAPACITY_BY_TRANSACTION.get(transaction_id)
        run = _parse_payload(input.get("tool_response"))
        if capacity is None or not isinstance(run, dict) or "richtexts" not in run:
            print(f"[canva] DECLINED: no retained geometry for transaction "
                  f"{transaction_id!r}, or unreadable payload; passing through",
                  file=sys.stderr)
            return {}

        edited = [op.get("element_id") for op in (tool_input.get("operations") or [])
                  if isinstance(op, dict) and op.get("element_id")]
        after = canva.parse_elements(run["richtexts"])
        overflows = canva.find_overflows(after, capacity, edited)

        results = run.get("edit_operation_results") or []
        failed = [r for r in results if r.get("status") != "success"]
        if overflows:
            summary = {eid: round(info["overflow_px"], 1)
                       for eid, info in overflows.items()}
            print(f"[canva] OVERFLOW in transaction {transaction_id}: {summary}",
                  file=sys.stderr)
        return _slim({
            "ok": not overflows and not failed,
            "overflow": {eid: round(info["overflow_px"], 1)
                         for eid, info in overflows.items()},
            "failed_operations": failed,
        })

    return {}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_hooks.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/hooks.py tests/test_hooks.py
git commit -m "feat: reduce the Canva element dumps and decide overflow in code

start-editing-transaction and perform-editing-operations each return the whole
design, twice per job. The agent needs a transaction id and an overflow verdict;
the element map is already pinned in config. Holding the geometry in-process also
means find_overflows makes the overflow call deterministically, instead of the
model comparing floats."
```

---

### Task 6: Per-run index writer + the two output tools

**Files:**
- Modify: `src/render.py`
- Modify: `src/tooling.py`
- Test: `tests/test_render.py`
- Test: `tests/test_output_tools.py` (create)

**Why the two tools:** `disallowed_tools` blocks `Bash`, `Read`, `Write` and `WebFetch` — deliberately, since unrestricted built-ins are what turned a failed reduction into a $7.19 run. That means **the agent physically cannot save the exported PDF or write `index.md`**. Both need an in-process tool, the same way `prepare_resume` does. `export-design` returns a URL; something has to fetch it and put it on disk.

**Interfaces:**
- Consumes: `tooling.safe_filename` (unchanged).
- Produces:
  - `render.pdf_filename(company, title, job_id) -> str`
  - `render.render_index(entries, window, skipped_count) -> str` — `entries` is a list of dicts with keys `company, title, fit_score, match_kind, reason, apply_url, pdf_filename, canva_edit_url, corrections`
  - `tooling.run_dir(today=None) -> Path` — `output/<YYYY-MM-DD>/`, created on demand
  - `tooling.save_pdf(export_url, company, title, job_id, today=None) -> dict` — downloads to the run dir; returns `{"saved": <path or None>, "error": <str>, "filename": <str>}`
  - `tooling.write_index(entries, window, skipped_count, today=None) -> dict` — writes `index.md`; returns `{"written": <path>}`

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_render.py` entirely:

```python
from render import pdf_filename, render_index


def _entry(**overrides):
    entry = {"company": "Alignerr", "title": "Software Engineer (AI Training)",
             "fit_score": 88, "match_kind": "direct",
             "reason": "Python and LLM tooling line up with the internship.",
             "apply_url": "https://www.linkedin.com/jobs/view/4446167840",
             "pdf_filename": "Alignerr_Software_Engineer_(AI_Training)_4446167840.pdf",
             "canva_edit_url": "https://www.canva.com/d/abc123",
             "corrections": []}
    entry.update(overrides)
    return entry


def test_pdf_filename_uses_job_id_and_pdf_extension():
    name = pdf_filename("Alignerr", "Software Engineer", "4446167840")
    assert name.endswith(".pdf")
    assert "4446167840" in name


def test_pdf_filename_disambiguates_same_company_and_title():
    a = pdf_filename("Alignerr", "Software Engineer", "111")
    b = pdf_filename("Alignerr", "Software Engineer", "222")
    assert a != b


def test_pdf_filename_strips_path_characters():
    assert "/" not in pdf_filename("A/B", "C:D", "../../evil")
    assert "\\" not in pdf_filename("A\\B", "C", "1")


def test_index_lists_every_entry_with_its_apply_url_and_pdf():
    out = render_index([_entry(), _entry(company="Fives", title="Junior QA", fit_score=90,
                                        pdf_filename="Fives_Junior_QA_1.pdf",
                                        apply_url="https://example.com/j/1")],
                       window="24h", skipped_count=16)
    assert "Alignerr" in out and "Fives" in out
    assert "https://www.linkedin.com/jobs/view/4446167840" in out
    assert "Fives_Junior_QA_1.pdf" in out
    assert "88" in out and "90" in out


def test_index_reports_the_window_and_the_skipped_count():
    out = render_index([_entry()], window="24h", skipped_count=16)
    assert "24h" in out
    assert "16" in out


def test_index_surfaces_guard_corrections():
    out = render_index([_entry(corrections=["removed unverified skills: Kubernetes"])],
                       window="24h", skipped_count=0)
    assert "Kubernetes" in out


def test_index_handles_an_empty_run():
    out = render_index([], window="24h", skipped_count=9)
    assert "9" in out
    assert "No résumés" in out or "no résumés" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'pdf_filename' from 'render'`

- [ ] **Step 3: Replace `render.py`**

Replace the contents of `src/render.py` with:

```python
"""The per-run index. The résumé itself is now a Canva-rendered PDF; this file
carries the operator information that cannot live inside a CV sent to an employer —
fit score, reasoning, the apply URL, and any guard corrections.
"""
from tooling import safe_filename

_MATCH_LABEL = {"direct": "Direct fit", "stretch": "Learnable stretch"}


def pdf_filename(company, title, job_id):
    """PDF counterpart of safe_filename. Company/title/job_id come from a scraper
    and are never trusted as path components."""
    return safe_filename(company, title, job_id).rsplit(".md", 1)[0] + ".pdf"


def render_index(entries, window, skipped_count):
    lines = [f"# Tailored résumés — {window} window", ""]

    if not entries:
        lines += ["No résumés were written this run.", ""]
    for entry in entries:
        label = _MATCH_LABEL.get(entry["match_kind"], entry["match_kind"])
        lines += [
            f"## {entry['company']} — {entry['title']}",
            "",
            f"- **Fit:** {entry['fit_score']}/100 — {entry['reason']}",
            f"- **Match:** {label}",
            f"- **Apply at:** {entry['apply_url']}",
            f"- **PDF:** `{entry['pdf_filename']}`",
            f"- **Edit in Canva:** {entry['canva_edit_url']}",
        ]
        if entry.get("corrections"):
            lines.append(
                f"- **⚠️ Auto-corrected:** {'; '.join(entry['corrections'])} — "
                f"review before sending.")
        lines.append("")

    lines += ["---", "", f"{len(entries)} résumé(s) written. "
                         f"{skipped_count} other job(s) judged and skipped.", ""]
    return "\n".join(lines)
```

- [ ] **Step 3b: Write the failing tests for the two output tools**

Create `tests/test_output_tools.py`:

```python
import pathlib

import pytest

import tooling


def test_run_dir_is_dated_and_created(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    d = tooling.run_dir(today="2026-07-30")
    assert d == tmp_path / "2026-07-30"
    assert d.is_dir()


def test_save_pdf_writes_the_downloaded_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"%PDF-1.7 fake")

    out = tooling.save_pdf("https://example.com/x.pdf", "Alignerr",
                           "Software Engineer", "4446167840", today="2026-07-30")

    saved = pathlib.Path(out["saved"])
    assert saved.exists()
    assert saved.read_bytes() == b"%PDF-1.7 fake"
    assert saved.suffix == ".pdf"
    assert "4446167840" in saved.name
    assert out["error"] == ""


def test_save_pdf_disambiguates_same_company_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"x")
    a = tooling.save_pdf("u", "Alignerr", "SW Engineer", "111", today="2026-07-30")
    b = tooling.save_pdf("u", "Alignerr", "SW Engineer", "222", today="2026-07-30")
    assert a["saved"] != b["saved"]
    assert len(list((tmp_path / "2026-07-30").iterdir())) == 2


def test_save_pdf_reports_a_download_failure_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)

    def boom(url):
        raise OSError("connection reset")

    monkeypatch.setattr(tooling, "_fetch_bytes", boom)
    out = tooling.save_pdf("u", "Acme", "Dev", "1", today="2026-07-30")
    assert out["saved"] is None
    assert "connection reset" in out["error"]


def test_save_pdf_rejects_an_empty_download(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(tooling, "_fetch_bytes", lambda url: b"")
    out = tooling.save_pdf("u", "Acme", "Dev", "1", today="2026-07-30")
    assert out["saved"] is None
    assert "empty" in out["error"].lower()
    assert not list((tmp_path / "2026-07-30").iterdir())


def test_write_index_writes_the_rendered_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(tooling.config, "OUTPUT_DIR", tmp_path)
    entries = [{"company": "Alignerr", "title": "SW Engineer", "fit_score": 88,
                "match_kind": "direct", "reason": "Good overlap.",
                "apply_url": "https://example.com/j", "pdf_filename": "a.pdf",
                "canva_edit_url": "https://canva.com/d/x", "corrections": []}]
    out = tooling.write_index(entries, window="24h", skipped_count=16,
                              today="2026-07-30")
    written = pathlib.Path(out["written"])
    assert written.name == "index.md"
    body = written.read_text(encoding="utf-8")
    assert "Alignerr" in body and "https://example.com/j" in body and "16" in body
```

- [ ] **Step 3c: Run them and verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_output_tools.py -v`
Expected: FAIL — `AttributeError: module 'tooling' has no attribute 'run_dir'`

- [ ] **Step 3d: Implement the two output tools**

Append to `src/tooling.py`:

```python
def _fetch_bytes(url):
    """Isolated so tests can substitute it without touching the network."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def run_dir(today=None):
    """output/<YYYY-MM-DD>/ for this run, created on demand."""
    from datetime import date
    stamp = today or date.today().isoformat()
    path = config.OUTPUT_DIR / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_pdf(export_url, company, title, job_id, today=None):
    """Download an exported Canva PDF into this run's output directory.

    The agent has no Bash/Read/Write/WebFetch - those are denied so a failure
    cannot degrade into hand-parsing - so downloading has to happen here.
    Returns rather than raises: one job's failed download must not end the run.
    """
    from render import pdf_filename
    filename = pdf_filename(company, title, job_id)
    try:
        payload = _fetch_bytes(export_url)
    except Exception as exc:                     # noqa: BLE001 - report, never abort the run
        print(f"[save_pdf] {filename}: download failed ({exc})", file=sys.stderr)
        return {"saved": None, "error": str(exc), "filename": filename}

    if not payload:
        message = "download was empty; nothing written"
        print(f"[save_pdf] {filename}: {message}", file=sys.stderr)
        return {"saved": None, "error": message, "filename": filename}

    path = run_dir(today) / filename
    path.write_bytes(payload)
    print(f"[save_pdf] wrote {path} ({len(payload):,} bytes)", file=sys.stderr)
    return {"saved": str(path), "error": "", "filename": filename}


def write_index(entries, window, skipped_count, today=None):
    """Write this run's index.md - the operator view that cannot live inside a CV."""
    from render import render_index
    path = run_dir(today) / "index.md"
    path.write_text(render_index(entries, window, skipped_count), encoding="utf-8")
    print(f"[write_index] wrote {path} ({len(entries)} entries, "
          f"{skipped_count} skipped)", file=sys.stderr)
    return {"written": str(path)}
```

Both import from `render` **inside** the function bodies: `render` imports `safe_filename` from `tooling`, so a module-level import would be circular.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_render.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/render.py tests/test_render.py
git commit -m "feat: per-run index replaces the per-job markdown résumé

The résumé is now a Canva PDF; the index carries what cannot go inside a CV -
fit score, reasoning, apply URL, Canva edit link, and guard corrections."
```

---

### Task 7: Wire the agent to Canva

**Files:**
- Modify: `src/agent.py` (`CV_EDITOR_RULES`, `WORKFLOW`, `build_options`)
- Modify: `src/tools.py` (`write_resume` → `prepare_resume`)
- Modify: `tests/test_tools_import.py`

**Interfaces:**
- Consumes: `tooling.prepare_resume` (Task 3), `hooks.guard_canva_write` (Task 4), `hooks.reduce_canva_output` (Task 5), `tooling.save_pdf` / `tooling.write_index` (Task 6).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools_import.py`:

```python
def test_prepare_resume_tool_replaced_write_resume(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    import tools
    assert hasattr(tools, "prepare_resume")
    assert not hasattr(tools, "write_resume")


def test_agent_registers_canva_mcp_and_the_write_guard(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    import agent
    opts = agent.build_options()

    assert "canva" in opts.mcp_servers
    assert any(t.startswith("mcp__canva__") for t in opts.allowed_tools)
    assert "mcp__resume_tools__prepare_resume" in opts.allowed_tools

    assert "mcp__resume_tools__save_pdf" in opts.allowed_tools
    assert "mcp__resume_tools__write_index" in opts.allowed_tools

    pre = opts.hooks["PreToolUse"]
    assert any(m.matcher == "mcp__canva__perform-editing-operations" for m in pre)

    post = opts.hooks["PostToolUse"]
    matchers = [m.matcher for m in post]
    # the Monid reduction hook must survive
    assert "mcp__monid__monid_get_run" in matchers
    # and the Canva element dumps must be reduced too
    assert "mcp__canva__start-editing-transaction" in matchers
    assert "mcp__canva__perform-editing-operations" in matchers

    assert opts.max_buffer_size == 10 * 1024 * 1024
    assert opts.env["MAX_MCP_OUTPUT_TOKENS"] == config.MAX_MCP_OUTPUT_TOKENS


def test_agent_still_denies_the_builtin_tools(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    import agent
    opts = agent.build_options()
    # save_pdf/write_index exist precisely because these stay denied
    for denied in ("Bash", "Read", "Write", "WebFetch", "Agent"):
        assert denied in opts.disallowed_tools
```

Add `import config` at the top of that test file.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_tools_import.py -v`
Expected: FAIL — `tools` still exposes `write_resume`; `opts.mcp_servers` has no `canva`.

- [ ] **Step 3: Swap the tool in `tools.py`**

In `src/tools.py`, replace `_write_resume_impl` and the `write_resume` tool with:

```python
def _prepare_resume_impl(job, score, tailored):
    return tooling.prepare_resume(job, score, tailored)


@tool("prepare_resume", "Gate a tailored résumé on relevance, strip invented skills, "
      "repair entry coverage, check the length budget, and return the Canva edit plan.",
      {"job": dict, "score": dict, "tailored": dict})
async def prepare_resume(args: dict) -> dict:
    return _json_result(_prepare_resume_impl(args["job"], args["score"], args["tailored"]))
```

Then add the two output tools — the agent cannot write files, because `Bash`, `Read`,
`Write` and `WebFetch` are all denied:

```python
@tool("save_pdf", "Download an exported Canva PDF into this run's output directory.",
      {"export_url": str, "company": str, "title": str, "job_id": str})
async def save_pdf(args: dict) -> dict:
    return _json_result(tooling.save_pdf(args["export_url"], args["company"],
                                         args["title"], args["job_id"]))


@tool("write_index", "Write this run's index.md describing every résumé that was created.",
      {"entries": list, "window": str, "skipped_count": int})
async def write_index(args: dict) -> dict:
    return _json_result(tooling.write_index(args["entries"], args["window"],
                                            args["skipped_count"]))
```

and change the server registration to
`tools=[get_resume, prepare_resume, save_pdf, write_index]`.

- [ ] **Step 4: Register Canva in `build_options`**

In `src/agent.py`'s `build_options()`, add to `mcp_servers`:

```python
            "canva": {"type": "http", "url": "https://mcp.canva.com/mcp"},
```

Add to `allowed_tools` (replacing the `write_resume` entry):

```python
            "mcp__resume_tools__prepare_resume",
            "mcp__resume_tools__save_pdf",
            "mcp__resume_tools__write_index",
            "mcp__canva__copy-design",
            "mcp__canva__start-editing-transaction",
            "mcp__canva__perform-editing-operations",
            "mcp__canva__commit-editing-transaction",
            "mcp__canva__cancel-editing-transaction",
            "mcp__canva__export-design",
            "mcp__canva__get-export-formats",
            "mcp__canva__create-folder",
            "mcp__canva__move-item-to-folder",
```

Add the `PreToolUse` matcher alongside the existing `PostToolUse` one:

```python
        hooks={
            "PostToolUse": [
                HookMatcher(matcher="mcp__monid__monid_get_run",
                            hooks=[hooks.reduce_monid_output]),
                # Each of these returns the WHOLE design (~13-16KB); the reducer
                # keeps the geometry in-process and returns an overflow verdict.
                HookMatcher(matcher="mcp__canva__start-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__perform-editing-operations",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__commit-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
                HookMatcher(matcher="mcp__canva__cancel-editing-transaction",
                            hooks=[hooks.reduce_canva_output]),
            ],
            "PreToolUse": [
                HookMatcher(matcher="mcp__canva__perform-editing-operations",
                            hooks=[hooks.guard_canva_write]),
            ],
        },
```

Canva OAuth is handled by the CLI's stored credential — verified working headlessly — so no auth header is set here.

- [ ] **Step 5: Update `CV_EDITOR_RULES`**

In `src/agent.py`, leave the `summary` bullet inside `CV_EDITOR_RULES` **exactly as it is** — the agent keeps writing one plain tailored paragraph, built only from evidence in the CV and positioned for the specific role. No Canva-specific instruction is added.

Delete only the "reordered so the most relevant entry comes first" clause from both
the `experience` and `projects` bullets — R2 keeps entries in their original order,
because the design's slots are fixed positions with fixed heights.

- [ ] **Step 6: Rewrite `WORKFLOW` step 4**

Replace step 4 of `WORKFLOW` in `src/agent.py` with:

```
4. For EACH job in `jobs`:
   a. Judge fit yourself using the rubric below: `is_junior_friendly`, `fit_score`
      (0-100), `match_kind` ("direct" or "stretch"), and a one-sentence `reason`.
   b. If the job is NOT junior-friendly or `fit_score` < {config.FIT_THRESHOLD},
      skip it — no Canva copy, no PDF. Just note it as skipped.
   c. Otherwise draft the tailored fields using the CV-editor rules below, then call
      `prepare_resume` with the job, your score, and those fields. If it returns
      `rejected: true`, note the reason and move on — do NOT touch Canva.
   d. Call `copy-design` with design_id {config.CANVA_TEMPLATE_DESIGN_ID!r}. Keep the
      new design's id and its edit URL.
   e. Call `start-editing-transaction` on that new design. Keep the transaction id.
   f. Call `perform-editing-operations` with the operations for the `edits` returned
      by `prepare_resume` - one `replace_text` per slot, carrying the full new text.
   g. The response is `{{"ok": true}}` or `{{"ok": false, "overflow": {{element_id: px}}}}`.
      The overflow check is done for you in code — do NOT try to compare element
      heights yourself. If `ok` is false: call `cancel-editing-transaction`, shorten
      the named block by roughly the reported pixel overrun, and retry from step (d)
      — at most {config.MAX_REDRAFT_ATTEMPTS} times. After that, skip the job and
      record it. NEVER commit a design whose response was not `ok`.
   h. Call `commit-editing-transaction`.
   i. Call `export-design` for PDF and poll until it completes.
   j. Call `save_pdf` with the export URL, the company, the title and the job id.
      You have no other way to write a file.
   k. Call `move-item-to-folder` to file the design in this run's folder.

   If `perform-editing-operations` is denied by the guard, call
   `cancel-editing-transaction` and skip the job — do not retry the same text.
```

Insert a new step before it:

```
3b. Create this run's Canva folder with `create-folder`, named
    "{config.CANVA_FOLDER_PREFIX} — <today's date, YYYY-MM-DD>". Keep its id.
```

and change step 5 to: once every job has been judged, call `write_index` **once**, with one
entry per résumé written — `company`, `title`, `fit_score`, `match_kind`, `reason`,
`apply_url`, `pdf_filename`, `canva_edit_url`, `corrections` — plus the search `window` and
the count of jobs judged but skipped. `write_index` is the only way to create that file.
Then report the same summary in your final message.

- [ ] **Step 7: Confirm no dangling references**

Run: `git grep -n "write_resume\|write_tailored_resume" -- src/ tests/`
Expected: no results in `src/`; only the guard assertion in `tests/test_tools_import.py`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/agent.py src/tools.py tests/test_tools_import.py
git commit -m "feat: wire the agent to Canva; prepare_resume replaces write_resume

Adds the Canva MCP, the per-job copy/edit/export sequence with a bounded
overflow-redraft loop, and the PreToolUse write guard. CV_EDITOR_RULES now
leaves the summary rules untouched and drops entry reordering, which
fixed-position slots cannot support."
```

---

### Task 8: First live run (spends money)

No new code. **Requires `base_cv.md`, `MONID_API_KEY` and `ANTHROPIC_API_KEY` in `.env`, and an authorised Canva connection.**

⚠️ Monid costs ~$0.06–0.12 per 24h run; the agent session cost ~$1.10 last time and will be higher here (more tool calls per job). Canva calls are free.

- [ ] **Step 1: Validate the element map before spending**

Run:

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -c "import sys;sys.path.insert(0,'src');import agent;o=agent.build_options();print('canva:', 'canva' in o.mcp_servers);print('pre-hook:', o.hooks['PreToolUse'][0].matcher);print('post-hook:', o.hooks['PostToolUse'][0].matcher)"
```

Expected: `canva: True`, the two matchers printed. Spends nothing.

- [ ] **Step 2: Clear the output directory**

Move any existing `output/*` aside so the run's results are unambiguous.

- [ ] **Step 3: Run the agent**

Run: `.venv/Scripts/python.exe src/agent.py`
Expected: the `[reduce]` line appears as before, then per qualifying job a copy → transaction → edit → commit → export sequence.

- [ ] **Step 4: Verify the PDFs**

Open at least two PDFs and confirm: the summary reads naturally with two bolded phrases; **no text overlaps the block below it**; skills are reordered for the job; bullets are reworded but the same in number; and every other section (Contact, Volunteering, Languages, Military Service, Education) is untouched.

Overflow is the failure this run exists to catch — check the tightest blocks (skills, and both bullet blocks) most carefully.

- [ ] **Step 4b: Verify the reduction and the overflow check actually ran**

Confirm in the run output:
- `[canva] transaction … N elements, geometry retained in-process` appears once per job
- **no** `containerElement` or raw element dump anywhere in the transcript
- if any job hit `[canva] OVERFLOW …`, it was cancelled and redrafted or skipped — never committed
- `[save_pdf] wrote …` once per PDF, and `[write_index] wrote …` exactly once

Record the total tool-call count and session cost. The pre-reduction estimate was $3-5 per
run and the post-reduction estimate $1.50-2.50 — report the real figure either way.

- [ ] **Step 5: Verify the run's bookkeeping**

Confirm: `output/<date>/index.md` lists every PDF with its apply URL and Canva edit link; the Canva account has a `Job CVs — <date>` folder containing one design per PDF; the master résumé and `CV — working copy` are unmodified.

- [ ] **Step 6: Commit what you learned**

```bash
git commit --allow-empty -m "chore: first live Canva run - <N> PDFs; <overflow/redraft observations, cost>"
```

---

## Self-Review

- **Spec coverage:** threshold gate (Task 3, Task 7 step 6b) ✓; per-job copy (Task 7 step 6d) ✓; dated Canva folder (Task 7 steps 3b, 6j) ✓; per-run index with apply URL and Canva link (Task 6) ✓; `base_cv.md` as source of truth with unchanged guards (Task 3) ✓; single `replace_text` write mode (Task 2, Task 7 step 6f) ✓; summary accepted as a plain paragraph, unchanged from today (Task 3, Task 7 step 5) ✓; element map + validation (Task 1) ✓; capacity/overflow detection (Tasks 1-2) and the bounded redraft loop (Task 7 step 6g) ✓; `prepare_resume` + `PreToolUse` guard as the two-part boundary (Tasks 3-4) ✓; Monid hook, buffer, env and `disallowed_tools` preserved (Task 7 test assertions) ✓; reordering dropped (Task 7 step 5) ✓; live verification (Task 8) ✓.
- **`LENGTH_BUDGET_RATIO` is 1.05, and the spec was corrected to match.** The spec originally said ~0.95, which would reject **every** run: reordering skills is length-preserving, so the new string is exactly as long as the original. The ratio must stay above 1.0; the budget is cheap prevention and `canva.find_overflows` is the authoritative check.
- **Cost:** the two Canva element dumps per job (~13-16KB each) are removed by the Task 5 reducer, which is the single largest lever — without it the run is roughly 80 tool calls carrying ~50K tokens of element JSON that the agent never needs, since the element map is pinned in config.
- **Not covered, and deliberately so:** run-start map validation is implemented (`canva.validate_map`) but is not wired into `agent.py` as a pre-flight abort, because the agent — not Python — drives the Canva calls. It is exercised by tests and available; wiring it into a startup check is a follow-up once the live run shows how drift actually manifests.
- **Placeholder scan:** every code step carries complete, runnable content; no "add error handling", no "similar to Task N". Task 8's steps 4-5 are visual-inspection checklists, which is the honest form for a rendering result that cannot be asserted programmatically.
- **Summary:** the agent writes a plain tailored paragraph, exactly as it does today. `CV_EDITOR_RULES` gains no Canva-specific instruction. The accepted cost is that the two phrases currently bolded inside that paragraph render at normal weight; block-level formatting everywhere else is preserved.
- **Import direction:** `render` imports from `tooling`. Task 3 drops `tooling`'s `render_output` import specifically so Task 6 can add that without a cycle. `tooling` does not import `canva`.
- **Type consistency:** `edits` is slot-keyed with a plain string for every slot, identically in Task 2's `build_operations`, Task 3's `prepare_resume`, and Task 7's workflow text. `config.CANVA_ELEMENT_MAP` maps a slot name directly to an `element_id` (no nested spec dict), consistently in Tasks 1, 2, 4 and 6. `parse_elements` returns `{"top","left","width","height","regions"}` and `compute_capacity`/`find_overflows` consume exactly those keys. `config.CANVA_ELEMENT_MAP` slot names (`summary`, `skills`, `experience.N.bullets`) are identical in Tasks 1, 2, 3, 4 and 6.
