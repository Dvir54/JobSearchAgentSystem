# `resume` — the CV domain

Everything about your actual CV: what it says, what may be changed, and whether the result
still fits on the page.

| File | Responsibility |
|---|---|
| `base_cv.py` | Parses `base_cv.md` into sections and indexed entries. |
| `tailoring.py` | The truthfulness guards. |
| `canva.py` | Element parsing, capacity maths, overflow detection. |
| `render.py` | PDF filenames. |

## The invariant: truthfulness is enforced in code, never delegated

Claude drafts the wording. Whether that draft is *allowed* is decided by Python:

- **Invented skills are stripped.** Anything not present in `base_cv.md` is removed and
  reported as a correction.
- **Bullets are reworded one-to-one.** A draft with a different bullet count than the base CV
  entry is rejected outright.
- **Length budgets are checked** before anything reaches Canva, with the actual and allowed
  lengths in the rejection message — without the numbers, the only way back is to bisect, one
  tool call per guess.

The guards diff against `base_cv.md` and not against Canva, because Canva is the thing being
written to. You cannot validate a write against its own target.

## Canva behaviours that cost live runs to discover

**`replace_text` inherits the FIRST original region's formatting.** A text block whose first
region is an empty `"\n"` spacer therefore loses every bullet marker — silently, with the
operation reporting success. Experience bullets go through one `find_and_replace_text` per
bullet instead, and each `find` has its trailing full stop stripped because find/replace is a
substring match and `base_cv.md` does not end bullets with one.

**A `find_and_replace_text` that matches nothing still reports `status: "success"`.** So
`edit_operation_results` is never trusted alone; a PostToolUse hook reads back the real
geometry to confirm the text landed and nothing overflowed.

Overflow is decided from measured element heights, not from a character estimate. The
character budget in `config.py` is cheap prevention only — skills reordering is
length-preserving, so a sub-1.0 ratio would reject every run. The summary gets a looser
budget derived from the design: the About Me box has 110.3px of room for its current 69.3px.

## The template

The pinned template is design `DAHQxzJVWM4`, titled **"Dvir Resume"**. Every per-job copy
inherits that title, because `copy-design` has **no title parameter** and the Canva MCP
exposes no rename or update-design tool at all. Renaming the template is therefore the only
way to change what the copies are called — and it means every CV in a run's folder shares one
name, distinguishable only by the Canva links in the digest.

Copies are made from a **pinned template**, not from your live master résumé, so editing the
master cannot break a run mid-flight. When your real CV changes, update both `base_cv.md`
(the facts) and the Canva template (the design). If they drift apart, drafts start getting
rejected because the guards insist on content the design no longer has.

## A known wart

`render.py` imports `safe_filename` from `agent/tooling.py`, and `agent/tooling.py` imports
`pdf_filename` back from `render.py` inside a function. That cycle is benign only because the
second import is lazy. It predates the R4 restructure, which was a pure move; untangling it
means relocating `safe_filename`, which is a behavioural change and was deliberately left
out of scope.
