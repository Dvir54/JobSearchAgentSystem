# `delivery`

User-facing entry points for running the Job Search Agent.

This package handles the CLI, scheduled execution and email delivery.

## Components

| File | Responsibility |
|---|---|
| `cli.py` | `jobs` CLI commands and run orchestration |
| `mailer.py` | Daily digest email |
| `scheduling.py` | Scheduled job registration |

## CLI

Initialize the candidate's CV:

```bash
jobs init "<canva-resume-url>"
```

Initialize the system:

```bash
jobs setup
```

Run a search manually:

```bash
jobs run
```

Export a generated CV:

```bash
jobs pdf <cv-id>
```

## Daily Run

A normal run follows this flow:

```text
Scheduled / Manual Trigger
          ↓
       Preflight
          ↓
     Job Search
          ↓
    AI Evaluation
          ↓
   CV Generation
          ↓
    Store Results
          ↓
      Email Digest
```

The run is guarded so that a successful run for the current day is not executed again unintentionally.

`jobs run --force` can be used to explicitly re-run a completed day.

## Email

The agent sends one daily digest containing:

- Matching jobs
- Fit scores
- Evaluation summaries
- Apply links
- Generated CV IDs

The email does not automatically apply to any job.

Generated CVs can be retrieved later with:

```bash
jobs pdf <cv-id>
```

## Scheduling

`jobs setup` registers the scheduled task used for daily execution.

The scheduler is designed for a local Windows machine and can trigger the application after the machine starts or resumes, in addition to the regular daily schedule.

## Logs

Run logs are stored under:

```text
logs/
└── run-YYYY-MM-DD.log
```

These logs can be used to diagnose failed or missed runs.