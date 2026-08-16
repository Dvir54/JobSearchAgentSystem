"""Filenames for the exported Canva PDFs.

The per-run index.md is gone: the run's record is rows in `runs`, `seen` and
`matches`, and the operator sees it as the daily digest email. What survives here
is the filename, stored alongside each CV so `jobs pdf` can write it back out
under a recognisable name.

Nothing here imports from `agent`. These functions lived in `agent/tooling.py`
until R6, which made `resume` and `agent` import each other — a cycle that only
avoided a crash because one side deferred its import inside a function body.
Moving them to their single caller made the dependency point one way.
"""
import re


def _sanitise_path_component(value):
    """Strip characters that make a value unsafe as a path component. Used on
    company, title, and job_id alike — all three come from a scraper."""
    return re.sub(r'[<>:"/\\|?*]', "", value).strip().replace(" ", "_")


def pdf_filename(company, title, job_id=None):
    """A filesystem-safe name for one exported CV.

    Company, title and job_id come from a scraper and are never trusted as path
    components. Two distinct postings can share a company and title — two roles
    both called "Software Engineer" at the same employer — so job_id, when
    present, disambiguates them; without it, the second would silently overwrite
    the first.
    """
    parts = [_sanitise_path_component(company), _sanitise_path_component(title)]
    if job_id:
        parts.append(_sanitise_path_component(str(job_id)))
    return "_".join(parts) + ".pdf"
