"""The `jobs` command: setup, run, pdf.

Orchestration only — every decision of substance lives in db.py, tooling.py or
the agent's own judgement. This module's job is the run's shape: preflight, open
the run row, drive the session, close the row, report by email.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

import config
import db
import mailer
import scheduling
import tooling


def _configure_console():
    """Make stdout/stderr able to carry non-ASCII without killing the command.

    A Windows console defaults to cp1252, and several messages here contain
    arrows, em-dashes and middle dots — printing one raised UnicodeEncodeError
    and took down `jobs setup` while it was reporting an unrelated error.
    errors="replace" means a stray character degrades to '?' instead of ending
    the run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass          # captured or already-wrapped streams: nothing to do


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


REQUIRED_ENV_KEYS = ("MONID_API_KEY", "ANTHROPIC_API_KEY", "GMAIL_ADDRESS",
                     "GMAIL_APP_PASSWORD")


def missing_env_keys():
    """Every required key absent or blank in the environment (after .env loads).

    Blank counts as missing: `GMAIL_APP_PASSWORD=` in .env is present and useless,
    and would otherwise pass setup and fail at 9am.
    """
    return [key for key in REQUIRED_ENV_KEYS if not os.environ.get(key, "").strip()]


def start_container():
    """`docker compose up -d`, from the repo root. Raises with docker's output."""
    result = subprocess.run(["docker", "compose", "up", "-d"],
                            cwd=str(config.PROJECT_ROOT), capture_output=True,
                            text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"`docker compose up -d` failed: "
            f"{(result.stderr or result.stdout).strip()}. Is Docker Desktop "
            f"running?")


def scheduled_command():
    """(command line, working directory) for the scheduled task.

    Names the venv's `jobs.exe` by absolute path: a scheduled non-interactive
    session does not inherit the interactive PATH, and a bare `jobs` would
    produce a task that fails silently every morning.
    """
    executable = Path(sys.executable).parent / "jobs.exe"
    return f'"{executable}" run', str(config.PROJECT_ROOT)


def command_setup():
    """Install everything, once. Idempotent: re-running repairs a partial install."""
    load_dotenv()

    print("Starting Postgres...")
    try:
        start_container()
        wait_for_database()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print("  container is up and answering")

    print("Applying the schema...")
    db.apply_schema()
    print("  runs, seen, matches are in place")

    print("Checking .env...")
    missing = missing_env_keys()
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        print("  Add them to .env and run `jobs setup` again.")
        return 1
    print("  all required keys present")

    print("Checking the Gmail app password...")
    try:
        mailer.verify_credentials()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print("  Gmail accepted the login")

    print("Registering the 9am task...")
    command, working_dir = scheduled_command()
    try:
        scheduling.register(command, working_dir)
    except Exception as exc:                      # noqa: BLE001 - report and stop
        print(f"  FAILED: {exc}")
        return 1
    print(f"  {config.TASK_NAME} registered: {command}")
    print(f"  {scheduling.wake_timer_state()}")
    print("\nSetup complete. The first run happens at 09:00; "
          "`jobs run` starts one now.")
    return 0


def command_pdf(job_id, open_after=True):
    """Write one stored CV to the current directory and open it.

    This is the only route from the database back to a file the operator can
    attach: the digest deliberately carries no attachments.
    """
    load_dotenv()
    found = db.fetch_pdf(job_id)
    if found is None:
        print(f"No stored CV for job {job_id!r}. Check the id in the digest "
              f"email — it is the number after `jobs pdf`.")
        return 1
    payload, filename = found
    path = Path.cwd() / filename
    path.write_bytes(payload)
    print(f"Wrote {path} ({len(payload):,} bytes)")
    if open_after:
        os.startfile(path)        # noqa: S606 - Windows-only by design
    return 0


def main(argv=None):
    _configure_console()
    parser = argparse.ArgumentParser(
        prog="jobs", description="Daily job search, tailoring, and digest.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("setup", help="install: database, schema, 9am task")
    subparsers.add_parser("run",
                          help="run one day's search (the 9am task calls this)")
    pdf_parser = subparsers.add_parser("pdf", help="write a stored CV to a file")
    pdf_parser.add_argument("job_id", help="the id shown in the digest email")

    args = parser.parse_args(argv)
    if args.command == "setup":
        return command_setup()
    if args.command == "run":
        return command_run()
    if args.command == "pdf":
        return command_pdf(args.job_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
