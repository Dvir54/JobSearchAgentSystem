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
pytest
```

Run with verbose output:

```bash
pytest -v
```

Run a specific test file:

```bash
pytest tests/<test-file>.py
```

## Test Environment

Tests that require external services use the configured test environment and should not modify the candidate's production CV or database.

Keep API credentials and other secrets out of the test suite.

## Test Structure

```text
tests/
├── agent/       # Agent and job evaluation tests
├── resume/      # CV and Canva tests
├── database/    # Persistence tests
└── delivery/    # CLI and delivery tests
```