# `resume` — the CV domain

Everything about the CV itself: what it says, what may be changed, and whether the result
still fits on the page.

| File | Responsibility |
|---|---|
| `base_cv.py` | Reads `base_cv.md` into sections and indexed entries. |
| `tailoring.py` | The truthfulness guards. |
| `canva.py` | Element geometry, capacity, overflow detection. |
| `render.py` | Filenames for exported PDFs. |

This package depends on nothing but `config`. You can load, read and test the CV logic
without touching the agent, the database or the network.

---

## Two sources, one CV

Your CV exists twice, deliberately.

**Canva holds the design** — the layout, fonts and spacing of the document an employer
actually receives. **`base_cv.md` holds the facts** — your real experience, in plain text.

The tailoring writes into Canva. The truthfulness checks read `base_cv.md`. They have to be
separate: you cannot validate a write by inspecting the thing you just wrote to. If the
guards read the Canva design, a fabricated bullet would simply confirm itself.

Keep the two in step. When your real CV changes, update both — the text here, the layout
there. If they drift apart, drafts start getting rejected for content the design no longer
has room for.

---

## What "tailored" is allowed to mean

Claude writes the wording. This package decides whether that wording is honest, and its
answer is final:

- **Skills must already be yours.** Anything not present in `base_cv.md` is stripped out and
  reported, however plausible it looks next to the job description.
- **Bullets are reworded one to one.** A draft that adds, drops, merges or splits a bullet is
  rejected outright. Rephrasing your experience is allowed; inventing more of it is not.
- **Length is budgeted.** Every piece of text is measured against the space the design gives
  it before anything is sent, and a rejection states the actual and permitted lengths so the
  next attempt is a correction rather than a guess.

A rejection isn't a failure of the run. The agent reads the reason, redrafts, and tries
again — up to twice — before skipping the job and recording why.

---

## Fitting the page

A CV that overflows its page is worse than one that wasn't tailored, and neither Canva nor a
character count will tell you it happened.

`canva.py` works from the design's real geometry. Every text block's position and height are
read from the live document, so the space available to a block is the distance to whatever
sits below it. After an edit, the page is re-measured: if a block has grown into its
neighbour, the edit is rejected and the draft is shortened.

Character budgets exist too, but only as cheap prevention. They can't be the authority —
reordering a skills list changes no lengths at all yet can still reflow a page, and a budget
tight enough to catch that would reject every run.

Two behaviours of the Canva API shape the code here, and both are silent failures if ignored:

- Replacing a whole text block inherits the formatting of its **first** region. If that
  region happens to be an empty spacer line, the replacement quietly loses every bullet
  marker. Bullets are therefore replaced one at a time, matched on their own text.
- A find-and-replace that matches **nothing** still reports success. So the reported result is
  never trusted alone; the page is read back and checked for the text that was supposed to
  land.

---

## The template

Copies are made from a **pinned template**, never from your live master CV, so editing your
own résumé can't disturb a run in progress.

The code addresses individual text boxes by id. If the template is redesigned those ids
change, and a map pointing at boxes that no longer exist would put your summary where your
skills should be — silently, given the API behaviour above. So the map is verified against the
live design at the start of every edit, and a mismatch cancels the job rather than guessing.

Every copy inherits the template's name, so all of a run's CVs share one title in Canva. The
Canva API offers no way to rename a design and no way to name a copy, so the template's own
name is the only lever. The PDFs themselves are named per job — company, title and job id —
which is what you actually send.
