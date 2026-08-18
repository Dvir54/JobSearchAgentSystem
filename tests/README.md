# Tests

```bash
.venv/Scripts/pip install -e ".[dev]"       # pytest, once
.venv/Scripts/python.exe -m pytest          # everything, about five seconds
.venv/Scripts/python.exe -m pytest -k pdf   # one slice
```

340 tests, one file per module. Flat rather than mirroring the package tree — at this size a
mirror costs navigation without adding any.

---

## What they cover

| Area | What's pinned |
|---|---|
| `test_reduce`, `test_payload_ceiling` | The scrape is deduplicated, filtered to Israel, stripped of already-seen jobs, and kept inside the size limits |
| `test_tailoring`, `test_guards` | Invented skills are removed, bullet counts are preserved, length budgets are enforced |
| `test_canva` | Capacity and overflow are computed from real geometry |
| `test_hooks` | The write guard rejects what it should, and template drift is caught |
| `test_db` | Deduplication, verdict recording, and PDF round-trips against real Postgres |
| `test_cli` | Run shape: preflight, the once-per-day guard, exit codes, digest on every path |
| `test_scheduling` | The task carries all three triggers and the settings that let it wake a laptop |
| `test_mailer` | All three digest flavours, with every field escaped |
| `test_discover` | Reading any user's design: column grouping, section placement, the refusals, and that nothing in a design is dropped |
| `test_profile` | The per-user map: validation, and that no Canva identity is left in the source |

---

## The database tests use a real Postgres

Not a mock. They run against a throwaway `jobs_test` database in the same container, cleared
between tests.

That's deliberate. The central guarantee of the design is *"a duplicate insert is impossible
because the primary key says so"* — and a mock asked to prove that would only demonstrate
that the mock does what it was told. The claim is about Postgres, so Postgres has to answer
it.

If the container isn't running these tests skip with a clear message rather than failing, so
the rest of the suite still runs.

---

## What a green suite does and doesn't tell you

It tells you the logic is right: the guards reject what they should, the reducer keeps what it
should, the run reports what it did.

It cannot tell you the system works, because the interesting failures in a project like this
one don't live inside the code. They live in the seams between it and something external — an
API that reports success for an edit that changed nothing, a scheduler that treats waking a
laptop differently from starting it, a console that can't print the character you're about to
write to it.

Tests mock those seams, and a mock is built from the same assumption as the code it's testing.
When the assumption is wrong, both halves agree with each other and pass.

So the suite is necessary and not sufficient. The other half of the practice is running the
real thing before calling a change done, and writing down what it measured — which is why
several modules carry comments naming exact byte counts, timings and API quirks. Those numbers
came from runs, not from reasoning, and they're the part worth keeping.
