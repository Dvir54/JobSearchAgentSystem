# `agent` — the autonomous session

One Claude Agent SDK session does the whole day's work. Claude judges fit and drafts the
tailored wording. **Nothing else is delegated to it.**

| File | Responsibility |
|---|---|
| `session.py` | The workflow prompt, SDK options, and hook registration. One session per run. |
| `tools.py` | The in-process MCP tools the agent may call. Definitions only. |
| `tooling.py` | What those tools actually do, plus the payload reducer. |
| `hooks.py` | Payload reduction and the Canva write guard. |
| `jobs.py` | Normalises raw scraper JSON into a posting. |

## The invariant: the hooks are the enforcement boundary, not the prompt

The agent makes the Canva MCP calls itself. So a guard that runs *before* it calls — like
`prepare_resume` returning an approved operation list — is advice, not enforcement: nothing
stops the model sending different operations. A **PreToolUse hook inspecting what is
genuinely about to be written** is the only real boundary, because it sits between the
model's decision and the API.

Two SDK facts make this concrete, and both cost a live run to learn:

**`allowed_tools` only pre-approves — it does not restrict.** With it alone the agent kept
`Bash`, `Grep` and `Agent`, and when payload reduction failed it routed around the failure
by hand-parsing a 787KB file in 256 tool calls, for $7.19 in one run. `disallowed_tools` is
what actually denies `Bash`, `Read`, `Write`, `WebFetch` and `Agent`. A failure cannot
degrade into hand-parsing.

**The CLI truncates oversized MCP results *before* PostToolUse hooks run.** An oversized
result becomes a "saved to file" stub, so the reduction hook silently never fires. Raising
`MAX_MCP_OUTPUT_TOKENS` via `env` is what lets the hook see real JSON.

## Reduction happens before the model sees anything

A raw 24h scrape measured 774,006 characters — roughly 193K tokens. The PostToolUse hook on
`monid_get_run` reduces it in-process: dedupe by id, keep only Israel-located postings, drop
every job already in `seen`, and project to a manifest of about 97 bytes per job. Full
descriptions stay in process and `get_job` serves them one at a time.

**Cross-run dedup lives here, in the hook** — not in the prompt, not in a tool the model
chooses to call. That placement is why a job you were shown yesterday costs nothing today.

There is a **second, separate 32KB ceiling** on what a PostToolUse hook hands *back*, with no
environment variable to raise it. `MAX_MCP_OUTPUT_TOKENS` governs the earlier guard and does
nothing here. A reduced envelope of 55,198 bytes was silently truncated to a 2,000-byte
preview and the agent could see 1 job of 22. Serving descriptions one at a time is the fix;
`INLINE_RESULT_LIMIT_BYTES` and `SAFE_ENVELOPE_BYTES` in `config.py` are belt and braces that
make a pathological run fail loudly instead.

## The search recipe is pinned

The endpoint takes its fields inside a `body` envelope: `input: {"body": {...}}`. The prompt
once sent them flat, so `monid_run` rejected the first call of **every** run and the agent
improvised the wrapper each time. `tooling._window()` had always read `input["body"]` — two
ends of our own code disagreeing, invisible to any test of either side alone. The regression
test asserts what we send round-trips through what the reducer reads.
