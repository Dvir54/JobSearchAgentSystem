# Canva PDF Output (Phase R2) — Design

**Date**: 2026-07-29
**Status**: Draft, awaiting review
**Branch**: `r2-canva-pdf-output`
**Builds on**: R1 (autonomous Agent SDK session, Monid MCP) + the payload-reduction hook, both merged to `master` at `d96f9bf`

## Problem

Every tailored résumé is written as a markdown file by `render.py` + `write_tailored_resume`.
A `.md` file is not something you send to an employer. The candidate's real résumé —
layout, typography, icons — lives in Canva.

**Goal:** for every job that clears the fit threshold, produce a **tailored PDF rendered
from the real Canva résumé**, instead of a markdown file.

This is **Phase R2**, anticipated in the R1 spec
(`2026-07-25-agent-sdk-refactor-design.md:9,15,17,102`). Reading résumé *content* from
Canva to replace `base_cv.md` (the deferred "Q4") remains **out of scope** — `base_cv.md`
stays the content source of truth.

## Decisions locked

- **Threshold:** only jobs that are junior-friendly **and** `fit_score >= FIT_THRESHOLD`
  (70) get a Canva copy and a PDF. Everything below is judged and skipped exactly as today.
  Reuses the existing gate; the threshold stays a one-line config change.
- **One design copy per qualifying job**, persisted, so the candidate can open and tweak
  any of them before applying.
- **One Canva folder per run** (not per month) — Canva Free caps folders at ~200 items and
  a month of runs would land on that ceiling.
- **One `index.md` per run**, describing the jobs that got PDFs.
- **`base_cv.md` remains the content source of truth.** The truthfulness guards are
  unchanged and still diff against it.
- **Canva runs inside the daily agent** (not a separate on-demand step).

## Spike results — all verified live, 2026-07-29

Against the real account, design `DAHQxzJVWM4` (a duplicate of the master résumé):

| Question | Result |
|---|---|
| PDF export on Free | ✅ `{"formats":{"pdf":{},"jpg":{},"png":{},"gif":{},"mp4":{}}}` |
| Text blocks individually addressable | ✅ every block has a stable `element_id` |
| `replace_text` by `element_id` | ✅ both test operations returned `success` |
| Block formatting (bullets, font, colour, size) | ✅ preserved by `replace_text` |
| Inline formatting (bold spans inside a paragraph) | ❌ **flattened** by `replace_text` |
| Inline formatting via `find_and_replace_text` | ✅ **preserved** — bold survived |
| Rewriting a **bold** region keeps it bold | ✅ verified — new phrases rendered bold |
| Multiple `find_and_replace_text` ops on one element, batched | ✅ all three returned `success` |
| `copy-design` on Free | ✅ works |
| **Element IDs across a copy** | ✅ **identical** — same `page_id`, same element ids |
| Agent SDK → Canva MCP headlessly | ✅ works both inherited from CLI config and declared explicitly |
| Element height recomputed pre-commit | ✅ height changed `69.33 → 33.33` after an edit |

Two consequences worth stating plainly:

1. **Element IDs surviving `copy-design` means a static element map works for every job.**
   No per-copy re-discovery.
2. **`find_and_replace_text` is the formatting-safe write path**, but it is not used —
   see "One write mode" below. It is recorded here so the option is known if inline
   emphasis is ever wanted back.

## Architecture

Per qualifying job, inside the existing autonomous session:

```
copy-design(template)            → new design_id (element ids identical)
  ↓
start-editing-transaction        → returns element list + dimensions
  ↓
perform-editing-operations       → one replace_text per mapped element
  ↳ PreToolUse hook: guards vs base_cv.md → repair via updatedInput, or deny
  ↓
OVERFLOW CHECK on the returned dimensions  → cancel + redraft if any block grew too tall
  ↓
commit-editing-transaction       (or cancel-editing-transaction)
  ↓
export-design (pdf) → poll → download
  ↓
save output/<run-date>/<Company>_<Title>_<job_id>.pdf
move-item-to-folder → "Job CVs — <run-date>"
```

Jobs below threshold never reach this sequence.

## The enforcement boundary

Today `write_tailored_resume` is a single deterministic function the agent cannot bypass.
With Canva, the writes are **MCP calls the agent makes** — and an in-process tool cannot
call an MCP server. The boundary therefore splits in two, and **both halves are required**:

1. **`prepare_resume(job, score, tailored) -> dict`** (in-process, deterministic)
   Runs the relevance gate, `strip_invented_skills`, `repair_entry_coverage`, and the new
   length budget. Returns a **per-element edit plan** plus `corrections`. Replaces
   `write_tailored_resume`'s render-and-write tail.

2. **`PreToolUse` hook on `mcp__canva__perform-editing-operations`** (the real boundary)
   Inspects the text actually being sent, re-checks it against `base_cv.md`, and either
   rewrites it via `updatedInput` or denies the call. This catches any divergence between
   what `prepare_resume` returned and what the agent actually sends.

Guards fail → hook denies → the run calls `cancel-editing-transaction` → nothing is
committed, nothing is exported. The guarantee that an untruthful résumé cannot be published
is preserved by construction, as it is today.

This mirrors the already-proven `PostToolUse` reduction hook on `monid_get_run`.

## Overflow — the primary layout risk

Canva elements are absolutely positioned and **do not reflow**. Longer tailored text
silently overlaps whatever sits below it, producing a broken PDF with no error.

Measured slack in the current design — the amount each editable block can grow before it
collides with the next element:

| Block | Bottom edge | Next element starts | Slack |
|---|---|---|---|
| About Me summary | 188.65 | 229.65 | 41.0 px |
| Skills | 612.82 | 621.98 | **9.2 px** |
| IBM bullets | 475.39 | 485.39 | **10.0 px** |
| Ness bullets | 582.26 | 589.23 | **7.0 px** |

Overflow is the *default* outcome without a check, not an edge case.

**Two-layer defence:**

- **Prevention — length budget.** `prepare_resume` rejects any rewritten block exceeding
  `LENGTH_BUDGET_RATIO` (1.05) times the original's character count. Deterministic, cheap,
  catches gross overruns before a single API call. Note it must stay **above 1.0**:
  reordering skills is length-preserving, so a sub-1.0 ratio would reject every run.
- **Detection — height read-back.** `perform-editing-operations` returns *recomputed*
  `dimension.height` for every element **before commit**. Compare each edited element's new
  bottom edge against the top of the next element below it in the same column. If any
  collides: `cancel-editing-transaction`, report which block overflowed and by how much, and
  let the agent redraft shorter. **Bounded to 2 redraft attempts per job**, then skip the job
  and record it in the index rather than publish a broken PDF.

The slack table is computed **once per run from the template design**, not hardcoded, so it
stays correct if the résumé layout changes.

## One write mode: `replace_text`

Every mapped element — summary, skills, and each entry's bullets — is written with a single
`replace_text` carrying the full new text.

**The agent's output is unchanged from today.** The summary is a plain tailored paragraph
connected to both the base CV and the job, exactly as `tailor_cv` produces now. No new
structure, no markup, no Canva detail leaking into `CV_EDITOR_RULES`.

**What this costs, stated plainly:** `replace_text` flattens *inline* formatting, so the two
phrases currently bolded inside the About Me paragraph render at normal weight. That is
accepted deliberately — preserving them would require the agent to mark up its prose, which
is not worth the complexity for two bold phrases.

**What it does not cost:** block-level formatting is fully preserved — verified live. The
skills list came back still bulleted and styled after a `replace_text`, and font, size,
colour, alignment and spacing were all retained. Every untouched section (Contact,
Volunteering, Languages, Military Service, Education), every heading, and every icon is
unaffected because the pipeline never writes to them.

`find_and_replace_text` was verified to preserve inline bold — including when the bold
region itself is rewritten — and is recorded in the spike table above. It is **not used**:
that path exists only if inline emphasis is ever wanted back, and taking it would mean the
agent marking up its prose, which this design deliberately avoids.

## Element map

Pinned in `config.py`, keyed to the template design. Verified live:

```
summary        replace  PB5prZGGYdD17M0v-LBrJ8LlFHVgPZm7d   plain paragraph
skills         replace  PB5prZGGYdD17M0v-LBkVtV7y5fKZMm0H   newline-separated
experience[0]  replace  title   PB5prZGGYdD17M0v-LB6dWjhqhy865bfK
                        date    PB5prZGGYdD17M0v-LBm83fB0jYRwNXp0
                        bullets PB5prZGGYdD17M0v-LBk2rXZgbWWq75bp   newline-separated
experience[1]  replace  title   PB5prZGGYdD17M0v-LBy14hl84Yxspf65
                        date    PB5prZGGYdD17M0v-LBDfDPSFmCscLJyk
                        bullets PB5prZGGYdD17M0v-LBzpBGcBgpx9yCWC
projects[0]    replace  title   PB5prZGGYdD17M0v-LBSw3MPln78BRrNQ  (AI Market Context Agent)
                        tech    PB5prZGGYdD17M0v-LBBn7RTVpPvK72YS
projects[1]    replace  title   PB5prZGGYdD17M0v-LBQSRXttJ86dgQdP  (Crypto Advisor)
                        tech    PB5prZGGYdD17M0v-LBWRLc5NXj6GqzXz
projects[2]    replace  title   PB5prZGGYdD17M0v-LBCWJ2xXXDKgHbZT  (Robotic Vacuum Sensor Sim)
                        tech    PB5prZGGYdD17M0v-LBY9Fc1br0rnxvPL
```

Note the project **titles and tech lines are facts, not tailorable prose** — R2 reorders
nothing and invents nothing, so in practice only `summary`, `skills`, and the two `bullets`
elements are ever written. The remaining ids are mapped so the run-start validation can
detect layout drift, not because they get edited.

Sections the pipeline **never touches**: Contact, Volunteering, Languages, Military Service,
Education, name, and all icons.

**Map drift is the main maintenance hazard.** If the template's structure changes, ids can
vanish or shift meaning silently. Mitigation: **validate the map at run start** — read the
template's elements once and assert every mapped id exists. Missing id → abort the run with
a loud error, before any copy or spend. Do not attempt to auto-repair the map.

## Template design

Copies are made from a **pinned template design**, not from the live master résumé, so
edits to the master cannot silently break a run mid-flight.

- `CANVA_TEMPLATE_DESIGN_ID` in config (initially `DAHQxzJVWM4`).
- When the master résumé changes, the template is re-duplicated and the map re-validated —
  a documented manual step, surfaced by the run-start validation when it drifts.

## Per-run index

`output/<run-date>/index.md`, listing every job that got a PDF:

| Column |
|---|
| Company, Title |
| Fit score, match kind, one-line reason |
| Apply URL (LinkedIn) |
| PDF filename |
| Canva edit URL for that job's design |
| Guard corrections, if any |

Plus a one-line footer: `N others judged and skipped`. Rationale: the fit score, reason,
apply URL and correction banner are **operator information** and cannot live inside a CV
sent to an employer, but without them a folder of PDFs is unusable — you would not know
where to apply.

The per-job `.md` résumé is **removed**. `render.py`'s résumé rendering is deleted; only the
metadata formatting survives, relocated into the index writer.

## Module changes

| File | Change |
|---|---|
| `config.py` | `CANVA_TEMPLATE_DESIGN_ID`, `CANVA_ELEMENT_MAP`, `CANVA_FOLDER_PREFIX`, `MAX_REDRAFT_ATTEMPTS = 2`, `LENGTH_BUDGET_RATIO = 0.95` |
| `canva.py` *(new)* | Deterministic, SDK-free: build `replace_text` operations from the edit plan, compute the slack table from a design's elements, detect overflow from returned dimensions, parse export/download responses. Fully unit-testable. |
| `tooling.py` | `write_tailored_resume` → `prepare_resume`: same gate and guards, returns `{element_id: text}` + corrections instead of writing markdown. Adds the length budget. |
| `hooks.py` | Second hook: `PreToolUse` on `perform-editing-operations`, enforcing guards on what is actually sent. |
| `agent.py` | Canva MCP in `mcp_servers`; Canva tools in `allowed_tools`; `WORKFLOW` rewritten for the per-job sequence, the overflow-redraft loop, and the folder step. **`CV_EDITOR_RULES` is unchanged for the summary** and drops only the reorder instruction. The summary stays a freely tailored plain paragraph, as today. |
| `render.py` | Résumé rendering deleted; metadata rendering becomes the index writer. |
| `tools.py` | `write_resume` tool replaced by `prepare_resume`. |
| `tests/` | `canva.py` unit tests with stubbed payloads (including a real captured `start-editing-transaction` response); guard-hook tests; index-writer tests. |

## Error handling

- **Map validation fails at run start** → abort before any copy or spend. Loud.
- **Guards deny a write** → `cancel-editing-transaction`, job skipped, recorded in the index.
- **Overflow after 2 redrafts** → `cancel-editing-transaction`, job skipped, recorded with
  the offending block. Never publish a broken PDF.
- **Canva auth failure** → abort the Canva step loudly. The run still produces the index, so
  a lapsed token yields a usable run, not a silent empty one.
- **Rate limit (`copy-design` / `export-design`, 20 req/min)** → back off and retry, do not
  treat as fatal. A run with 20+ matches will hit it.
- **Export job fails or times out** → job skipped and recorded; other jobs unaffected.

Every skip is visible in the index. A silent skip is a defect.

## Testing

- `canva.py` is deterministic and unit-tested against a **captured real
  `start-editing-transaction` payload** — operation building, slack-table computation, and
  overflow detection all testable with no network.
- `prepare_resume` keeps the existing guard tests; the length budget gets its own.
- The `PreToolUse` guard hook is tested for deny and `updatedInput` repair, mirroring the
  existing reduction-hook tests.
- End-to-end is verified by one live run and **visual inspection of the PDFs** — the
  honest limit of this design, since correct rendering cannot be asserted programmatically.

## Out of scope

- Reading résumé content from Canva to replace `base_cv.md` (the deferred Q4).
- Reordering experience/project entries by relevance. Slots are fixed positions with fixed
  heights; swapping content between differently-sized boxes is the most direct route to
  overflow. The CV-editor rules keep entries in their original order for R2.
- Deleting old designs or folders — impossible: no delete endpoint exists in either the
  Canva MCP or the Connect REST API. Cleanup is manual in the Canva UI.
- Brand templates and `autofill-design` (Pro / Enterprise only).
- Multi-page résumés.

## Risks

- **Overflow** — mitigated by two layers above, but "fits the box" is ultimately a rendering
  property. The first runs need visual checking.
- **Element-map drift** — run-start validation converts silent mis-mapping into a loud abort,
  but keeping the template in sync with the master résumé is a real manual cost that does not
  exist today.
- **Canva token refresh over time** — verified working now, but refresh tokens are
  single-use and rotating; one desync revokes the grant. Failure must be loud.
- **Reduced test coverage** — today's write path is local file I/O covered end to end. Canva
  writes can only be stubbed; real correctness is visual.
- **More failure points per job** — copy → start → perform → check → commit → export → poll
  → download → move, versus one local write today. Slower and more to go wrong.
- **Design accumulation** — ~7 designs/run persist forever with no programmatic cleanup.
  Accepted deliberately in exchange for being able to revise any generated CV.
