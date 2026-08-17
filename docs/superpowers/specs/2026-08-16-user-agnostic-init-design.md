# R7 — `jobs init`: one agent, any user's CV

**Date:** 2026-08-16
**Branch:** `r7-user-agnostic-init`
**Status:** design approved, plan not yet written

## The problem

The agent tailors exactly one CV: the author's. `config.py` hardcodes a Canva design id, a
page id, and an element map naming four text boxes inside that specific design. `base_cv.md`
must be hand-written in a fixed markdown shape. Nothing tells a new user how to find their own
element ids, and nothing would work if they did — the map has room for exactly two jobs.

This phase makes the agent user-agnostic: a new user points it at their own Canva CV, and a
one-time setup step works out the rest.

## Goals

- Any user can run the agent against their own Canva design without editing code.
- The author's personal design ids leave the repository.
- The truthfulness guards keep working unchanged.
- Structures the agent cannot handle are **detected and refused clearly**, not half-supported.

## Non-goals

- Multi-page designs.
- Bullets split one-per-text-box, or skills split across several boxes.
- Editing anything beyond the three roles the agent already tailors.
- Any change to judging, scoring, scheduling, storage or email.

---

## Amended during implementation

Three things changed once this met a real design. Recorded here because the reasoning is the
useful part.

**The generated CV is lossless.** As specified, `build_resume` kept the roles it recognised and
discarded the rest — which on the author's own CV silently dropped his email, location, links
and job title. An omission cannot be reviewed, because there is nothing there to read. Inverted:
every text block is placed somewhere, and labelling decides *where* a block goes and whether it
is editable, never whether it survives. `coverage_gaps()` asserts it and `init` reports it.

**Placement is by column identity, not overlap with a heading's box.** A heading rarely spans its
own column: "Education" occupied x 342-548 while its degree dates sat at 681-767. Blocks are now
grouped into columns by transitive horizontal overlap, and a heading owns what is below it in its
column. A block above every heading in its column is header material; a block in a column with no
headings at all is the only thing that reaches `## Additional`.

**The label vocabulary is larger than three roles.** Job titles and dates must be identified to
write the markdown at all, even though nothing edits them — and projects need `project.N.title`
and `project.N.tech`, because loose paragraphs under a Projects heading are invisible to the
agent, which then drafts without knowing what the candidate has built. Only `summary`, `skills`
and `experience.N.bullets` are ever written to; everything else is locked.

## Decisions

| Decision | Choice |
|---|---|
| Interaction | **Generate and review.** No prompts beyond choosing the design. `init` writes both files and prints a summary; corrections are made by editing `profile.json` or re-running. |
| Editable roles | **The three proven ones:** `summary`, `skills`, `experience.N.bullets`. Everything else is locked. Anything unrecognised is locked. |
| Hardcoded config | **All Canva constants move to a git-ignored profile.** Everyone, including the author, runs `init`. One code path. |
| Page scope | **Single page only.** Multi-page designs are refused with an explanation. |
| Structural scope | **One box per entry's bullets, mixed or not.** Bullets-per-box and skill chips are refused. |

---

## How Canva access works

There is no account identifier anywhere in the project, and none is needed. The Canva MCP
server (`https://mcp.canva.com/mcp`) authenticates by OAuth: on first connection the Claude
CLI opens a browser, the user grants access to **their own** account, and the resulting token
is stored in `~/.claude/.credentials.json` — outside the repository. Every later call carries
that token, so the same code reaches whichever account was authorised on that machine.

The access token is short-lived and refreshed silently, which is what lets an unattended 09:00
run work. If the refresh token is revoked, the run fails, emails the error, and recovery is
re-running `jobs init` interactively.

**`init` verifies Canva access as its first act**, the same way `setup` proves the Gmail app
password by logging in. Nothing verifies Canva today, so a missing authorisation would
currently surface mid-run at 09:00.

---

## Architecture

Four units. The interesting logic is pure and testable without Canva or a model.

| Unit | Responsibility | Depends on |
|---|---|---|
| `resume/profile.py` | Load, save, validate a profile. Pure data. | — |
| `resume/base_cv.py` | Gains `render_base_cv()`, the inverse of `parse_resume()` | — |
| `agent/discover.py` | Read the design, label the blocks, assemble a profile | SDK, Canva |
| `delivery/cli.py` | `command_init()` — orchestration only | the above |

`render_base_cv` lives beside the parser deliberately: it gives a round-trip invariant worth
pinning — **`parse_resume(render_base_cv(x)) == x`**. If writer and parser ever disagree, a
test fails rather than a user's morning.

## The flow

1. **Verify Canva access.** Refuse with instructions if unauthorised.
2. **Choose the design.** `jobs init <url-or-id>`, or list the user's designs and take a number.
3. **Read it** via `start-editing-transaction`, then **cancel**. That call is the only one
   returning element ids *and* geometry; `get-design-content` returns text alone, unordered.
   Nothing is ever committed. Blocks are sorted by position to recover reading order.
4. **Refuse multi-page** before anything else is written.
5. **Label** the blocks with one Claude call: `summary`, `skills`, `experience[i].bullets`, or
   `locked`. Position is evidence alongside wording, so a block under a "Work Experience"
   heading reads as a job entry whatever it is titled.

   The call is a single one-shot `query()` through the SDK already in use, with no tools and
   no MCP servers — it is given the block list as text and returns a labelling. It needs
   `ANTHROPIC_API_KEY`, costs a few cents, and happens once per user rather than per run.

   **`init` does not need Postgres.** It touches Canva, the model and two files, so it can run
   before `jobs setup` has installed anything.
6. **Check the structural assumptions** (below) and refuse if they do not hold.
7. **Write `profile.json`** — design id, page id, editable slots, locked ids.
8. **Write `base_cv.md`** with canonical headings. Never overwrite an existing file without
   `--force`.
9. **Verify** with the existing `validate_map`: every id in the profile must resolve.
10. **Print the summary** — editable slots with previews, locked blocks in brief — and tell the
    user to review both files.

## Profile format

`profile.json` at the repo root, git-ignored:

```json
{
  "design_id": "DAHQxzJVWM4",
  "page_id": "PB5prZGGYdD17M0v",
  "design_title": "Dvir Resume",
  "slots": {
    "summary": "PB5prZGGYdD17M0v-LBrJ8LlFHVgPZm7d",
    "skills": "PB5prZGGYdD17M0v-LBkVtV7y5fKZMm0H",
    "experience.0.bullets": "PB5prZGGYdD17M0v-LBk2rXZgbWWq75bp",
    "experience.1.bullets": "PB5prZGGYdD17M0v-LBzpBGcBgpx9yCWC"
  },
  "locked": ["PB5prZGGYdD17M0v-LB6dWjhqhy865bfK", "…"]
}
```

`slots` replaces `CANVA_ELEMENT_MAP`; `locked` replaces `CANVA_VALIDATE_ONLY_IDS`.

## Structural assumptions, and what happens when they fail

The agent rewrites bullets with one find-and-replace per bullet, matching the exact text from
`base_cv.md`. It therefore does **not** require a box to contain only bullets — it can rewrite
bullet text inside a box that also holds a job title and dates, because it never targets them.

What it does require:

| Requirement | If violated |
|---|---|
| One page | Refuse: "This design has N pages. The agent tailors a single page." |
| A summary block | Refuse, naming the missing role |
| One skills block | Refuse: "Your skills appear to be in N separate boxes; the agent needs them in one." |
| Each entry's bullets in one box | Refuse: "Each bullet is a separate text box; the agent needs a job's bullets in one box." |
| At least one experience entry | Refuse, naming the missing role |

A refusal that happens *after* labelling still writes `profile.json`, so the user can fill the
gap by hand. `jobs run` refuses to start on an incomplete profile.

## Changes to existing code

- **`config.py`** loses `CANVA_TEMPLATE_DESIGN_ID`, `CANVA_PAGE_ID`, `CANVA_ELEMENT_MAP`,
  `CANVA_VALIDATE_ONLY_IDS` and the `_el()` helper. It keeps only generic settings.
- **`hooks.py`**, **`tooling.py`**, **`session.py`** read the profile instead of those
  constants. The drift check compares against `profile["slots"]` and `profile["locked"]`, and
  its failure message tells the user to re-run `jobs init`.
- **`session.py`**'s workflow prompt takes the design id from the profile.
- **`cli.py`** gains `command_init()`. `jobs setup` refuses to proceed without a profile and
  points at `jobs init`. `jobs run` does the same.
- **README and directory READMEs** gain the setup flow, the Canva scopes a user is granting,
  and the structural requirements — including a ten-second self-check a user can run before
  installing anything: *open the CV in Canva and click a bullet. If it selects the whole job
  block, or all the bullets, you are fine. If it selects only that one line, each bullet is
  its own box and the agent will refuse.* Same test on a skill: the whole list should
  highlight, not one word.
- **`.gitignore`** gains `profile.json`.

## Testing

- **Recorded fixtures, not live calls.** Element payloads from the author's design plus at
  least two synthetic ones: a mixed block holding title, dates and bullets together; and
  differently-named sections in a different order.
- **Refusal cases** get fixtures too: multi-page, one-bullet-per-box, split skills, no skills.
- **The round-trip invariant** — render then parse returns the same structure.
- **Profile validation** — missing slot, unknown element id, empty locked list.
- **The labelling call** is tested against a recorded response; only the acceptance check runs
  it live.
- **Acceptance:** run `jobs init` for real against the author's design, confirm the generated
  profile reproduces today's hardcoded map exactly, and confirm the generated `base_cv.md`
  parses to the same structure as the existing hand-written one.

That last check is the strongest signal available: the new mechanism must independently
rediscover the map that has been working for weeks.

## Known risk

This design is reasoned from **one** real CV. The labelling step is the part most likely to
disappoint on a layout nobody anticipated, and no amount of fixture-writing substitutes for
running it against genuinely different designs. Before this is advertised as working for
anyone, `init` should be run against two or three real Canva CVs that were not written with
this project in mind.

## Out of scope, deliberately

- Multi-page designs, bullets-per-box, skill chips — refused with clear messages.
- Tailoring projects or any role beyond the three that exist.
- Migrating an existing user's data: the author re-runs `init` once, which is the whole
  migration.
