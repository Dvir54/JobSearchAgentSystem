# Docs

`superpowers/specs/` and `superpowers/plans/` hold a design spec and an implementation plan
for each phase of the project, named by date.

**They are a decision record, not documentation.** Each spec captures what was decided at the
time and why — including the options that were rejected and the constraints discovered along
the way. Where a spec and the code disagree, the code is right; the spec is still worth
reading for the reasoning that led there.

For how the system works today, read the README in each directory, starting from the
[project README](../README.md).

| Phase | What it built |
|---|---|
| Foundations | The first pipeline: search, score, tailor |
| Job source | Moved the search to Monid's LinkedIn scraper |
| Agent SDK | Rebuilt the pipeline as one autonomous Claude session |
| Payload reduction | The in-process hook that made daily runs affordable |
| Canva output | Real CV design, rendered per job and exported as PDF |
| Daily agent | Postgres as the system of record, cross-run dedup, scheduling, digest email |
| Structure | The package layout and documentation as they stand now |

`agent-sdk-reference.md` is reference material for the Claude Agent SDK, kept locally because
several of its behaviours — output size limits, hook ordering, how tool permissions actually
work — are load-bearing here and are easier to have on hand than to rediscover.
