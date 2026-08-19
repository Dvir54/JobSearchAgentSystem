# Tests

Test suite for the Job Search Agent.

The tests cover the main application boundaries, including:

- Job discovery and normalization
- Job evaluation
- CV parsing and tailoring
- Canva integration
- Database operations
- CLI and delivery flows

## Run Tests

From the project root:

```bash
.venv/Scripts/pip install -e ".[dev]"   # pytest, once
.venv/Scripts/python.exe -m pytest
```

Run with verbose output:

```bash
.venv/Scripts/python.exe -m pytest -v
```

Run a specific test file:

```bash
.venv/Scripts/python.exe -m pytest tests/test_discover.py
```

## Test Environment

The suite cannot touch the candidate's real data. Database tests run against a
throwaway `jobs_test` database in the same container, truncated between tests,
and the Canva profile is copied to a temporary directory before each test — so
nothing that writes a profile can reach the committed fixture.

Database tests skip with a clear message when the container is not running.

Keep API credentials and other secrets out of the test suite.

## Test Structure

One `test_*.py` per module, flat rather than mirroring the package tree. At this
size a mirror costs navigation without adding any.

```text
tests/
├── conftest.py            # shared fixtures: test database, profile, offline dedup
├── fixtures/              # recorded payloads and a sample profile
└── test_*.py              # one per module
```
