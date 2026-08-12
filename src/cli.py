"""The `jobs` command: setup, run, pdf.

Orchestration only — every decision of substance lives in db.py, tooling.py or
the agent's own judgement. This module's job is the run's shape: preflight, open
the run row, drive the session, close the row, report by email.
"""
import sys
import time

from dotenv import load_dotenv

import config
import db
import mailer
import tooling


def wait_for_database(attempts=20, delay=1.5):
    """Block until Postgres answers, or give up with a readable message.

    Docker Desktop starts at login and can lag well past 09:00 on a cold boot,
    so this waits rather than failing on the first refused connection.
    """
    last = None
    for _ in range(attempts):
        try:
            db.connect().close()
            return
        except Exception as exc:                  # noqa: BLE001 - retried below
            last = exc
            time.sleep(delay)
    raise RuntimeError(
        f"Postgres at {config.DATABASE_URL} did not answer after "
        f"{attempts} attempts ({last}). Is Docker Desktop running? "
        f"Try `docker compose up -d`.")


def _drive_session():
    """Isolated so tests can replace the whole agent session."""
    import asyncio

    import agent
    return asyncio.run(agent.run_session())


def _notify(run):
    """Render and send the digest for a finished run. Raises if the send fails."""
    matches = db.matches_for_run(run["id"]) if run.get("id") else []
    subject, text, html = mailer.render_digest(run, matches)
    mailer.send(subject, text, html)
    print(f"[email] sent: {subject}", file=sys.stderr)


def _try_notify(run, what):
    """Send the digest, reporting rather than raising if the mail itself fails.

    Used on the failure paths, where an unsendable email must not replace the
    error that actually broke the run.
    """
    try:
        _notify(run)
    except Exception as exc:                      # noqa: BLE001 - already failing
        print(f"[run] could not email the {what}: {exc}", file=sys.stderr)


def _preflight_failure_run(error):
    """A run-row shape for a failure that happened before any row existed."""
    return {"id": None, "status": "failed", "search_window": config.POSTED_LIMIT,
            "fetched_count": 0, "skipped_seen_count": 0, "examined_count": 0,
            "matched_count": 0, "error": error}


def command_run():
    """One day's work. Exit 0 on ok/empty, 1 on failed."""
    load_dotenv()

    try:
        wait_for_database()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        # The database is the thing that is broken, so there is nowhere to record
        # this. Email and the exit code are all there is.
        print(f"[run] preflight failed: {exc}", file=sys.stderr)
        _try_notify(_preflight_failure_run(str(exc)), "preflight failure")
        return 1

    run_id = db.start_run(config.POSTED_LIMIT)
    tooling.set_run_id(run_id)
    print(f"[run] run {run_id} started, window={config.POSTED_LIMIT}",
          file=sys.stderr)

    try:
        _drive_session()
    except Exception as exc:                      # noqa: BLE001 - every failure reports
        db.fail_run(run_id, f"{type(exc).__name__}: {exc}")
        print(f"[run] run {run_id} FAILED: {exc}", file=sys.stderr)
        _try_notify(db.get_run(run_id), "failure")
        return 1

    stats = tooling.last_run_stats()
    matched = tooling.matched_count()
    db.finish_run(run_id, fetched=stats["fetched"],
                  skipped_seen=stats["dropped_seen"],
                  examined=tooling.examined_count(), matched=matched,
                  status="ok" if matched else "empty")

    try:
        _notify(db.get_run(run_id))
    except Exception as exc:                      # noqa: BLE001 - report and fail loudly
        # The one failure that cannot report itself by email. The nonzero exit is
        # what shows up in Task Scheduler's history.
        print(f"[run] the run succeeded but the digest could not be sent: {exc}",
              file=sys.stderr)
        return 1
    return 0
