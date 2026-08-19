# `agent`

The AI workflow responsible for discovering jobs, evaluating candidate fit, and preparing tailored CV content.

## Responsibilities

The agent:

1. Searches for relevant job postings.
2. Filters and deduplicates postings before evaluation.
3. Reads individual job descriptions.
4. Scores each job against the candidate's CV.
5. Records the evaluation.
6. Prepares tailored CV content for strong matches.
7. Reports the results of the run.

## Components

| File | Responsibility |
|---|---|
| `session.py` | Agent workflow and session configuration |
| `tools.py` | Tools exposed to the AI session |
| `tooling.py` | Tool implementations and data handling |
| `hooks.py` | Input/output validation and filtering |
| `jobs.py` | Job data parsing and normalization |
| `discover.py` | CV and Canva profile discovery |
| `canva_read.py` | Read-only Canva access during setup |

## AI vs. Application Logic

The model is responsible for:

- Evaluating job fit
- Explaining the evaluation
- Drafting tailored CV wording

Application code is responsible for:

- Job filtering and deduplication
- Fit thresholds
- CV content validation
- Canva write boundaries
- Persistence
- Final output

This keeps AI-generated content inside the application's validation and persistence boundaries.

## Job Evaluation

Each evaluated posting receives:

- A score from `0–100`
- A short explanation
- A persisted evaluation

Previously evaluated jobs are skipped on later runs.

## CV Tailoring

Only jobs that meet the configured fit threshold proceed to CV tailoring.

The generated content is validated before it is written to Canva. Invalid or unsupported changes are rejected rather than committed.