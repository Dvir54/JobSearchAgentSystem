# Docs

`superpowers/specs/` and `superpowers/plans/` hold one design spec and one implementation
plan per phase, named by date.

**These are a decision record, not current documentation.** A spec describes what was decided
at the time and why — including options rejected and constraints discovered. Where a spec
disagrees with the code, **the code wins**; the spec is still worth reading for the reasoning
that got there. Current documentation is the README in each directory.

| Phase | What it did |
|---|---|
| 2026-07-17 / 07-19 | Phase 1: the original deterministic pipeline |
| 2026-07-23 | Swapped the job source to Monid → Apify harvestapi; truthfulness guards |
| 2026-07-25 | Rebuilt the pipeline as one autonomous Agent SDK session |
| 2026-07-28 | Payload reduction — the in-process hook that made daily runs affordable |
| 2026-07-29 | R2: Canva-rendered PDF output |
| 2026-08-12 | R3: the daily agent — Postgres, cross-run dedup, 09:00 task, digest email |
| 2026-08-14 | R4: package structure, `jobs pdf` destination, these READMEs |

`agent-sdk-reference.md` is vendor reference material for the Claude Agent SDK, kept locally
because several of its behaviours (output-size guards, hook ordering, `allowed_tools` versus
`disallowed_tools`) are load-bearing here and were expensive to learn.
