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

## This package depends on nothing else

`resume` imports only `config`. `agent` imports `resume`, never the reverse — you can load
and test the CV domain without pulling in the session, the hooks or the payload reducer.

That was not true until R6. `render.py` imported `safe_filename` from `agent/tooling.py`
while `agent/tooling.py` imported `pdf_filename` back from `render.py`, and the pair only
avoided an import error because the second import hid inside a function body. Moving that
function here — to its only caller — made the dependency one-directional, and the deferred
import could go back to the top of the file where it belongs.
