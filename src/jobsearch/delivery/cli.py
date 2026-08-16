"""The `jobs` command: setup, run, pdf.

Orchestration only — every decision of substance lives in db.py, tooling.py or
the agent's own judgement. This module's job is the run's shape: preflight, open
the run row, drive the session, close the row, report by email.
"""
import argparse
import contextlib
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from jobsearch import config
from jobsearch import db
from jobsearch.delivery import mailer
from jobsearch.delivery import scheduling
from jobsearch.agent import tooling


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


class _Tee:
    """Writes everything to every stream it was given.

    Flushes on every write: a run killed mid-flight — which is exactly what
    happened on 2026-08-14 — must still leave behind everything it had said up
    to that point. A stream that refuses a write is dropped silently rather than
    allowed to take down the run.
    """

    def __init__(self, *streams):
        self._streams = streams

    def write(self, text):
        for stream in self._streams:
            try:
                stream.write(text)
                stream.flush()
            except Exception:                     # noqa: BLE001 - see docstring
                pass
        return len(text)

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:                     # noqa: BLE001 - see docstring
                pass


def log_path(when=None):
    """One log file per day, named for the morning it belongs to.

    Per-day rather than one rolling file: a morning's run is findable by date,
    and old runs age out by filename instead of growing without bound.
    """
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    return config.LOG_DIR / f"run-{stamp}.log"


@contextlib.contextmanager
def tee_stderr_to_log():
    """Duplicate stderr into today's log for the duration of the block.

    A scheduled task's console closes with the process, so an unattended failure
    otherwise leaves no trace whatsoever: on 2026-08-14 the 9am run died in
    preflight, before the run row existed, and the only evidence left anywhere on
    the machine was a Windows exit code.

    Logging is a diagnostic aid and must never become a new way for 9am to fail,
    so a log that cannot be opened is reported and skipped.
    """
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", encoding="utf-8", errors="replace")
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print(f"[run] could not open the log: {exc}", file=sys.stderr)
        yield
        return

    original = sys.stderr
    sys.stderr = _Tee(original, handle)
    try:
        print(f"\n[run] ===== {datetime.now():%Y-%m-%d %H:%M:%S} =====",
              file=sys.stderr)
        yield
    finally:
        sys.stderr = original
        handle.close()


DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")


def docker_is_running():
    """True when the Docker daemon answers. `docker info` fails fast if not."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              text=True).returncode == 0
    except OSError:
        return False                              # docker not on PATH at all


def start_docker_desktop(attempts=40, delay=3):
    """Launch Docker Desktop if its daemon is not answering, and wait for it.

    Docker Desktop does not start itself on this machine (AutoStart is off), and
    on 2026-08-14 the 9am task fired seven minutes after a reboot into a machine
    with no daemon and no database. Nothing else was ever going to start it.

    Launching is not sufficient on its own: a cold start takes 30-90 seconds, and
    `docker compose up` against a daemon that is still booting simply fails.
    """
    if docker_is_running():
        return
    if not DOCKER_DESKTOP.exists():
        raise RuntimeError(
            f"The Docker daemon is not answering and {DOCKER_DESKTOP} does not "
            f"exist, so there is nothing to start.")

    print(f"[run] starting Docker Desktop ({DOCKER_DESKTOP.name})...",
          file=sys.stderr)
    subprocess.Popen([str(DOCKER_DESKTOP)])
    for _ in range(attempts):
        time.sleep(delay)
        if docker_is_running():
            print("[run] the Docker daemon is up", file=sys.stderr)
            return
    raise RuntimeError(
        f"Docker Desktop was started but its daemon did not answer within "
        f"{attempts * delay}s.")


def wait_for_database(attempts=20, delay=1.5):
    """Block until Postgres answers, or give up with a readable message."""
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


def ensure_database():
    """Bring the database up, then wait for it to answer.

    The recovery is an attempt, not a precondition. Docker may be absent or the
    database served some other way, so a failure to start it is reported and then
    left to wait_for_database to adjudicate: what decides whether the run can
    proceed is Postgres answering, and its error is the one worth emailing.
    """
    try:
        start_docker_desktop()
        start_container()
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print(f"[run] could not start the database ({exc}); "
              f"trying to connect anyway", file=sys.stderr)
    wait_for_database()


def _drive_session():
    """Isolated so tests can replace the whole agent session.

    The import stays inside the function: delivery imports the agent session,
    and the session's module graph reaches back into delivery. Deferring it to
    call time is what keeps that from being an import cycle.
    """
    import asyncio

    from jobsearch.agent import session
    return asyncio.run(session.run_session())


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


def command_run(force=False):
    """One day's work. Exit 0 on ok/empty/already-done, 1 on failed.

    Everything it prints is teed to today's log, because the caller that matters
    is Task Scheduler and its console is gone the moment the process ends.
    """
    load_dotenv()
    with tee_stderr_to_log():
        code = _execute_run(force)
        print(f"[run] exit {code}", file=sys.stderr)
        return code


def _execute_run(force=False):
    try:
        ensure_database()
    except Exception as exc:                      # noqa: BLE001 - report and stop
        # The database is the thing that is broken, so there is nowhere to record
        # this. Email and the exit code are all there is.
        print(f"[run] preflight failed: {exc}", file=sys.stderr)
        _try_notify(_preflight_failure_run(str(exc)), "preflight failure")
        return 1

    # Three triggers fire this command — 09:00, resume, logon — so most
    # invocations on any given day have nothing to do. Cheaper than a search by
    # several dollars, and it is what stops one email a day becoming five.
    if not force and db.successful_run_today():
        print("[run] today's run has already finished; nothing to do. "
              "Use `jobs run --force` to run anyway.", file=sys.stderr)
        return 0

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
        # Fatal here, unlike in `run`: setup is watched by a human who can act on
        # "Docker Desktop never became ready" far better than on a connection
        # timeout reported two minutes later.
        start_docker_desktop()
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


def _read_design(design):
    """(design_id, title, payload). Isolated so tests can replace Canva entirely."""
    import asyncio

    from jobsearch.agent import canva_read
    return asyncio.run(canva_read.read_design(design))


def _label(elements):
    """element_id -> role. Isolated so tests can replace the model entirely."""
    import asyncio

    from jobsearch.agent import discover
    return asyncio.run(discover.label_blocks(elements))


def command_init(design=None, force=False):
    """Point the agent at this user's Canva CV. Run once, before `jobs setup`.

    Needs no database: it touches Canva, the model and two files, so it can run
    on a machine where nothing else is installed yet.
    """
    load_dotenv()
    from jobsearch.agent import discover
    from jobsearch.resume import base_cv, canva, profile

    print("Checking Canva access...")
    try:
        design_id, title, payload = _read_design(design)
    except Exception as exc:                      # noqa: BLE001 - report and stop
        # Canva is authorised by OAuth held in ~/.claude, not by a key in .env,
        # so the only fix is an interactive authorisation. `setup` proves the
        # Gmail password the same way and for the same reason: better to fail on
        # the operator's screen than silently at 09:00 three weeks later.
        print(f"  Could not read the design: {exc}\n"
              f"  If Canva is not authorised on this machine, run `claude` once "
              f"and approve the Canva connection, then try again.")
        return 1

    pages = payload.get("pages") or []
    if len(pages) > 1:
        print(f"\n  This design has {len(pages)} pages. The agent tailors a "
              f"single page.\n  Use a one-page version, or keep everything the "
              f"agent edits on page 1.")
        return 1
    page_id = pages[0].get("page_id") if pages else None

    elements = canva.parse_elements(payload.get("richtexts"))
    print(f'\nReading "{title}"...\n  1 page - {len(elements)} text blocks\n')

    print("Identifying sections...")
    labels = _label(elements)
    built = discover.build_profile(labels, elements, design_id, page_id, title)
    problems = discover.structural_problems(labels, elements)

    profile.save(built)
    print(f"  Wrote {config.PROFILE_PATH.name}   {len(built['slots'])} editable "
          f"slots, {len(built['locked'])} locked")

    if problems:
        for problem in problems:
            print(f"  x {problem}")
        print("\nThe profile was written anyway so you can fill the gap by hand.\n"
              "Until then `jobs run` will refuse to start.")
        return 1

    if config.BASE_CV_PATH.exists() and not force:
        print(f"  ! {config.BASE_CV_PATH.name} already exists - not overwritten.\n"
              f"    Use `jobs init --force` to regenerate it, which discards any "
              f"edits you have made.")
    else:
        text = base_cv.render_base_cv(discover.build_resume(labels, elements))
        config.BASE_CV_PATH.write_text(text, encoding="utf-8")
        print(f"  Wrote {config.BASE_CV_PATH.name}   ({len(text):,} bytes)")

    print("\nNEXT: open base_cv.md and check it.\n"
          "  It is the source of truth for every honesty check the agent makes -\n"
          "  which skills it may claim, and how many bullets each job has.\n"
          "  Text pulled out of a design can arrive garbled, so read it once.\n\n"
          "Then run `jobs setup` to install the database and the daily schedule.")
    return 0


def command_pdf(job_id, open_after=True):
    """Write one stored CV under output/<run date>/ and open it.

    This is the only route from the database back to a file the operator can
    attach: the digest deliberately carries no attachments. Filed under the date
    the CV was made rather than today, so exporting the same job twice always
    lands in the same place instead of scattering it across dated folders.
    """
    load_dotenv()
    found = db.fetch_pdf(job_id)
    if found is None:
        print(f"No stored CV for job {job_id!r}. Check the id in the digest "
              f"email — it is the number after `jobs pdf`.")
        return 1
    payload, filename, run_date = found
    # After the None check, so an unknown id leaves no empty folder behind.
    directory = config.OUTPUT_DIR / run_date.isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
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
    init_parser = subparsers.add_parser(
        "init", help="point the agent at your Canva CV (run once, first)")
    init_parser.add_argument(
        "design", nargs="?",
        help="Canva design id or URL; omit to let it find your CV")
    init_parser.add_argument(
        "--force", action="store_true",
        help="regenerate base_cv.md, discarding your edits")
    subparsers.add_parser("setup", help="install: database, schema, 9am task")
    run_parser = subparsers.add_parser(
        "run", help="run one day's search (the scheduled task calls this)")
    run_parser.add_argument(
        "--force", action="store_true",
        help="run even if today's search has already finished")
    pdf_parser = subparsers.add_parser("pdf", help="write a stored CV to a file")
    pdf_parser.add_argument("job_id", help="the id shown in the digest email")

    args = parser.parse_args(argv)
    if args.command == "init":
        return command_init(args.design, force=args.force)
    if args.command == "setup":
        return command_setup()
    if args.command == "run":
        return command_run(force=args.force)
    if args.command == "pdf":
        return command_pdf(args.job_id)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
