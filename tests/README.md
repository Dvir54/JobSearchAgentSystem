# Tests

```bash
.venv/Scripts/python.exe -m pytest        # all of them
.venv/Scripts/python.exe -m pytest -k pdf # one slice
```

243 tests, about four seconds. Flat layout: one `test_*.py` per module, not mirrored onto the
package tree — at this size the mirror costs churn and buys no navigation.

## They use a real Postgres, not a mock

Database tests run against a throwaway `jobs_test` database in the same container, truncated
between tests. Deliberately not a fake: the design's whole dedup guarantee is *"the primary
key makes a duplicate insert impossible"*, and a mock would only prove the mock does what the
mock was told. `conftest.py` skips these with a clear message when the container is down, so
a developer without Docker gets a skip rather than a wall of errors.

`_offline_dedup` is autouse and makes cross-run dedup default to "every job is new" for unit
tests, so reducer tests do not depend on whatever the real `seen` table happens to hold today.
Tests that actually exercise dedup override it.

## The invariant: a green suite is necessary but not sufficient

**Every serious defect in this project's history was found by running the thing, never by the
test suite — and the suite was fully green each time.**

- R1: payload reduction silently never fired. One $7.19 run.
- R2: three Canva behaviours no fixture predicted.
- R3: `config.py` snapshotted `os.environ` before `load_dotenv()` ran, which would have
  silenced the digest every morning forever.
- R4 (pre-work): two failed mornings, both machine environment, neither visible to any test.

The reason is structural, not carelessness. These bugs live in the **seams** between our code
and something external — the CLI's own guards, Canva's API, Windows' console encoding, an
endpoint's schema. Tests mock those seams, so both halves of a mismatch stay internally
consistent and agree with each other.

The clearest case: `session.py` sent a flat `input` while `tooling._window()` read
`input["body"]`. Two parts of *our own* code disagreeing, each tested against its own
assumption, both passing.

**So:** budget one live run per phase before calling it done. When a live run exposes
something, write the regression test *and* record the measured number in a code comment —
that is why `config.py`, `mailer.py` and `hooks.py` carry comments naming past failures.
Prefer probes that pin both ends together over a test of either side alone.
