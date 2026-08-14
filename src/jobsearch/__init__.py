"""A daily agent that finds junior software jobs in Israel and tailors a CV.

Three subpackages, each with its own README:

- `agent`    the autonomous Claude session, its tools, and the hooks that are
             the real enforcement boundary
- `resume`   the CV domain: parsing base_cv.md, the truthfulness guards, and
             Canva geometry
- `delivery` getting results to the operator: the CLI, the digest email, and
             the 09:00 scheduled task

`config` holds every tunable setting; `db` holds every SQL statement.
"""
