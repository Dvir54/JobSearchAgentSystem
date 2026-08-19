# `jobsearch`

Core package for the Job Search Agent.

The package is split into three main areas:

```text
jobsearch/
├── agent/       # Job discovery, evaluation and AI workflow
├── resume/      # CV parsing, tailoring and Canva rendering
└── delivery/    # CLI, scheduling and email delivery
```

The root package also contains the shared configuration and database layer.

| File | Responsibility |
|---|---|
| `config.py` | Application configuration and tunable settings |
| `db.py` | PostgreSQL access and persistence |

## Data Flow

```text
Job Sources
    ↓
Agent
    ↓
Fit Evaluation
    ↓
Resume Tailoring
    ↓
Database
    ↓
Email / CLI
```

PostgreSQL is the system of record for job evaluations, matches, generated CVs and run history.

User-specific CV configuration is kept separately from the source code and is created during `jobs init`.

## Package Dependencies

```text
delivery ──────┐
               ├──→ agent ──→ resume
agent ─────────┘
                     ↓
                   config
```

The resume package contains the core CV logic and can be tested independently of the agent, database and external services.