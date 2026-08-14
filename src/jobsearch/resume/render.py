"""Filename helpers for the exported Canva PDFs.

The per-run index.md is gone: the run's record is rows in `runs`, `seen` and
`matches`, and the operator sees it as the daily digest email. What survives here
is the filename, which is still stored alongside each CV so `jobs pdf` can write
it back out under a recognisable name.
"""
from jobsearch.agent.tooling import safe_filename


def pdf_filename(company, title, job_id):
    """PDF counterpart of safe_filename. Company/title/job_id come from a scraper
    and are never trusted as path components."""
    return safe_filename(company, title, job_id).rsplit(".md", 1)[0] + ".pdf"
