# `resume`

CV processing, validation, tailoring and Canva rendering.

The package manages two parts of the candidate's CV:

- **`base_cv.md`** — the source of truth for the candidate's experience and skills.
- **Canva design** — the visual layout used for generated CVs.

## Responsibilities

| File | Responsibility |
|---|---|
| `base_cv.py` | Parse and manage the base CV |
| `tailoring.py` | Validate tailored CV content |
| `canva.py` | Canva elements, geometry and page validation |
| `profile.py` | Mapping between CV sections and Canva elements |
| `render.py` | Generated CV output handling |

## CV Tailoring

The agent can adapt existing CV content to better match a job.

It cannot:

- Invent skills or experience
- Add unsupported experience
- Change the number of existing job bullets
- Modify locked CV elements
- Produce a CV that exceeds the available page layout

Tailored content is validated before the generated CV is committed.

## Canva Workflow

```text
Base CV
   +
Job Description
   ↓
AI-generated edits
   ↓
Content validation
   ↓
Canva design copy
   ↓
Layout validation
   ↓
PDF
```

The original Canva résumé is never modified directly. Generated CVs are created from the configured base design.

## Setup

`jobs init` connects the Canva résumé and creates the local CV metadata required by the resume pipeline.

If the Canva design structure changes, run `jobs init` again to refresh the configuration.

## Supported CV Structure

The current pipeline is designed for:

- Single-page CVs
- Editable text blocks
- Job experience represented in supported text elements
- Skills represented in supported text elements

A design with more than one page is refused outright — the agent tailors a single page. Other structural problems still write the profile, list what is missing, and block `jobs run` until you fill the gap by hand. Either way initialization fails loudly rather than producing an invalid tailored CV later.