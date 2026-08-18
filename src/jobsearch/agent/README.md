# `agent` — the autonomous session

One Claude session does a whole day's work: read the postings, decide which ones fit, and
write the tailored wording for the ones that do.

Claude is trusted with **judgement and prose**. Everything else — what it's allowed to see,
what it's allowed to write, and what actually gets saved — is decided by the code in this
package.

| File | Responsibility |
|---|---|
| `session.py` | The workflow prompt and the session's configuration. One session per run. |
| `tools.py` | The tools the agent may call, as an in-process MCP server. |
| `tooling.py` | What those tools actually do, and the payload reducer. |
| `hooks.py` | Intercepts tool traffic: reduces what comes back, guards what goes out. |
| `jobs.py` | Turns raw scraper JSON into a clean posting. |
| `discover.py` | Reads a Canva CV once, at setup, and works out which box is which. |
| `canva_read.py` | Opens a design to read it, and cancels. Never commits. |

The same reduction principle applies at setup: `canva_read` keeps the design payload in this
process with a hook and hands the session a short receipt, so a 13-16KB design is never read
and rewritten by a model that has no use for it.

---

## How one session runs

The agent is given a workflow and a set of tools, then left to work through the day's jobs on
its own. A single run looks like this from the inside:

1. **Search.** It calls the job source with a pinned recipe — five role titles, Israel,
   entry level, the last 24 hours. The recipe lives in `session.py`, not in the model's
   judgement, so what gets searched is stable and reviewable.

2. **Receive a manifest, not a scrape.** The raw result never reaches it. See below.

3. **Read one posting at a time.** The manifest carries only ids, titles and companies. When
   the agent wants to judge a job it asks for that job's description specifically.

4. **Score it, and record the verdict.** Every posting gets a score, a one-line reason, and a
   row in the database — including the ones it rejects. That row is what stops tomorrow's run
   paying to think about the same job twice.

5. **Tailor the ones that qualify.** For anything scoring at or above the threshold, it
   drafts new wording and hands it to `prepare_resume`, which either returns an approved set
   of edits or refuses with a reason specific enough to fix.

6. **Report.** At the end it summarises what it examined, matched and skipped.

---

## The payload reducer

A morning's scrape is around a megabyte of JSON — roughly 190,000 tokens of job descriptions,
duplicate listings and postings that aren't really in Israel. Sending that to a model is slow,
expensive, and mostly waste.

So it never arrives. A hook intercepts the search result in this process and rewrites it
before the agent sees anything:

- **deduplicate** by job id
- **drop** anything whose location isn't actually in Israel — the source's own location filter
  is leaky and returns remote roles from across the region
- **drop** every posting already judged on a previous day
- **project** what's left down to a manifest of roughly 97 bytes per job

A megabyte becomes about a kilobyte. The full descriptions stay here in memory and are handed
over one at a time when the agent asks for a specific job.

**Cross-run deduplication lives in this hook — in code, before the model is involved.** Not in
the prompt, and not in a tool the agent has to remember to call. That placement is why a job
you were shown yesterday costs nothing at all today, not even the tokens to look at it.

---

## Hooks are the boundary, not the prompt

The agent makes its own calls to Canva. So a check that runs *before* it calls — one that
hands it an approved list of operations and trusts it to send them — is advice, not
enforcement.

The real boundary is a hook sitting between the model's decision and the API, inspecting what
is genuinely about to be written. Two of them run on every edit:

- **Before a write**, the operations are checked against the CV's actual content: no invented
  skills, no changed bullet counts, nothing outside the slots the design defines.
- **After a write**, the resulting page is measured to confirm the text landed and nothing
  overflowed. Canva reports success for a replacement that matched nothing, so a reported
  success is never taken at face value.

The same principle shapes the session's tool list. The agent has **no** `Bash`, `Read`,
`Write`, `WebFetch` or `Agent` tools — they are explicitly denied, not merely left
unmentioned. Without that, a failure in the reduction step degrades into the model reading
the raw scrape off disk by hand, one grep at a time, at many times the cost.

---

## Size limits worth knowing

Two separate ceilings apply to tool traffic, and they are not the same one:

- What a tool returns is capped before hooks run. It's raised via the session's environment so
  the reducer sees real data rather than a truncated stub.
- What a **hook** hands back is capped again, at 32 KB, and nothing raises it. This is why
  descriptions are served one job at a time instead of being packed into the manifest — a
  reduced payload that breaches the ceiling is silently replaced by a short preview, and the
  agent would see one job out of twenty.

The budgets in `config.py` sit inside both limits, so an unusual run fails loudly instead of
quietly losing most of its work.

---

## Reading a CV once, at setup

`jobs init` has to answer a question the design itself doesn't state: which text box is the
summary, and which holds the second job's bullets? Positions and text are available; meaning
is not.

That judgement is made **once and then frozen** into `profile.json`. Re-deciding it every
morning would let the same CV tailor differently on different days — precisely the
inconsistency the guards exist to prevent — and would pay for the same answer daily.

Two rules do most of the work, and only one of them involves a model.

**Labelling** asks Claude what each block is: the summary, the skills, a job's bullets, a
project, a section heading, or something else. It only has to be right about the three roles
the agent can edit, because those are the only ones with a guard behind them. Anything
unparseable, invented or omitted degrades to `other`, which is locked — the failure mode has
to be *"did not tailor"*, never *"tailored the wrong box"*.

**Placement** is geometry, and it carries everything else. Blocks are grouped into columns by
transitive horizontal overlap, so a two-column CV yields two groups. A heading owns the blocks
below it *in its own column*; a block above every heading in its column is header material.
Geometry generalises across unfamiliar designs far better than recognising that "Perfil",
"Profile" and "Career Summary" mean the same thing.

## The generated CV is lossless

**Every text block in the design ends up somewhere in `base_cv.md`.** Labelling decides where a
block goes and whether it is editable — never whether it survives.

That inversion matters on a CV nobody has seen before. An extractor that keeps only what it
recognises discards the rest silently, and nobody can review text that isn't there. Here a
misjudged block lands in the wrong section instead: visible in the file, and fixed by editing
markdown rather than by hunting element ids.

`coverage_gaps()` asserts it and `jobs init` reports it — *"All 40 blocks captured"*. Anything
genuinely unattributable lands under `## Additional` rather than in the bin.
