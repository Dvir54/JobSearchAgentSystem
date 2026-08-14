from datetime import datetime

import pytest

from jobsearch.delivery import cli
from jobsearch import config
from jobsearch import db
from jobsearch.agent import tooling


def _boom(message):
    """A stand-in that fails the way the real thing did."""
    def raise_it(*args, **kwargs):
        raise RuntimeError(message)
    return raise_it


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    """Never write run logs into the checkout from a test."""
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")


@pytest.fixture
def wired(pg, monkeypatch):
    """cli wired to the test database with the network stubbed out."""
    monkeypatch.setattr(cli, "start_docker_desktop", lambda: None)
    monkeypatch.setattr(cli, "start_container", lambda: None)
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(tooling, "_db_conn", lambda: pg)
    sent = {}
    monkeypatch.setattr(cli.mailer, "send",
                        lambda subject, text, html: sent.update(
                            subject=subject, text=text, html=html))
    return sent


def _stats(monkeypatch, *, fetched, dropped_seen, examined, matched):
    monkeypatch.setattr(tooling, "last_run_stats",
                        lambda: {"fetched": fetched, "kept": examined,
                                 "dropped_duplicate": 0, "dropped_non_israel": 0,
                                 "dropped_seen": dropped_seen})
    monkeypatch.setattr(tooling, "examined_count", lambda: examined)
    monkeypatch.setattr(tooling, "matched_count", lambda: matched)


def test_a_matched_run_closes_ok_and_emails_the_digest(wired, monkeypatch, pg):
    def fake_session():
        run_id = tooling.current_run_id()
        db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched",
                          "good fit", conn=pg)
        db.insert_match("111", run_id, title="Backend Dev", company="Acme",
                        location="Israel", apply_url="https://a/1",
                        posted_date="2026-08-12", canva_design_id="DAG1",
                        canva_url="https://c/1", pdf=b"%PDF",
                        pdf_filename="Acme_111.pdf", conn=pg)
        return {"summary": "done", "cost": 2.21, "is_error": False}

    monkeypatch.setattr(cli, "_drive_session", fake_session)
    _stats(monkeypatch, fetched=111, dropped_seen=80, examined=1, matched=1)

    assert cli.command_run() == 0
    assert wired["subject"] == "1 new job match"
    assert "jobs pdf 111" in wired["text"]
    with pg.cursor() as cur:
        cur.execute("SELECT status, fetched_count, skipped_seen_count, "
                    "matched_count FROM runs")
        assert cur.fetchone() == ("ok", 111, 80, 1)


def test_a_run_with_no_matches_closes_empty(wired, monkeypatch, pg):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "none", "cost": 0.4,
                                 "is_error": False})
    _stats(monkeypatch, fetched=90, dropped_seen=90, examined=0, matched=0)
    assert cli.command_run() == 0
    assert wired["subject"] == "No new matches today"
    with pg.cursor() as cur:
        cur.execute("SELECT status FROM runs")
        assert cur.fetchone()[0] == "empty"


def test_the_run_id_is_set_before_the_session_starts(wired, monkeypatch):
    seen = {}

    def capture():
        seen["run_id"] = tooling.current_run_id()
        return {"summary": "", "cost": 0.0, "is_error": False}

    monkeypatch.setattr(cli, "_drive_session", capture)
    _stats(monkeypatch, fetched=0, dropped_seen=0, examined=0, matched=0)
    cli.command_run()
    # record_verdict and save_pdf file rows against it; unset means orphaned rows.
    assert seen["run_id"] is not None


def test_a_crashing_session_fails_the_run_and_emails_the_error(wired, monkeypatch,
                                                               pg):
    def boom():
        raise RuntimeError("Monid returned 502 after 3 attempts")

    monkeypatch.setattr(cli, "_drive_session", boom)
    assert cli.command_run() == 1
    assert "502" in wired["subject"]
    with pg.cursor() as cur:
        cur.execute("SELECT status, error FROM runs")
        status, error = cur.fetchone()
    assert status == "failed"
    assert "502" in error


def test_rows_written_before_a_crash_survive(wired, monkeypatch, pg):
    def half_way():
        db.record_verdict("111", tooling.current_run_id(), "T", "C", 40,
                          "rejected", "no", conn=pg)
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_drive_session", half_way)
    cli.command_run()
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM seen")
        # Partial progress is real progress: tomorrow must not re-judge this job.
        assert cur.fetchone()[0] == 1


def test_preflight_failure_reports_without_a_run_row(wired, monkeypatch, pg):
    def down():
        raise RuntimeError("Docker is not running")

    monkeypatch.setattr(cli, "wait_for_database", down)
    assert cli.command_run() == 1
    assert "Docker is not running" in wired["subject"]
    with pg.cursor() as cur:
        cur.execute("SELECT count(*) FROM runs")
        # The database is the broken thing; there is nowhere to record this.
        assert cur.fetchone()[0] == 0


def test_a_failing_digest_send_still_exits_nonzero(wired, monkeypatch):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "ok", "cost": 1.0, "is_error": False})
    _stats(monkeypatch, fetched=1, dropped_seen=0, examined=0, matched=0)

    def refuse(subject, text, html):
        raise RuntimeError("Gmail rejected the login")

    monkeypatch.setattr(cli.mailer, "send", refuse)
    # The one failure that cannot self-report by email; the exit code is all
    # Task Scheduler will have.
    assert cli.command_run() == 1


def test_a_failing_failure_email_does_not_mask_the_original_error(wired,
                                                                  monkeypatch, pg):
    def boom():
        raise RuntimeError("Monid returned 502")

    def refuse(subject, text, html):
        raise RuntimeError("SMTP also down")

    monkeypatch.setattr(cli, "_drive_session", boom)
    monkeypatch.setattr(cli.mailer, "send", refuse)
    assert cli.command_run() == 1
    with pg.cursor() as cur:
        cur.execute("SELECT error FROM runs")
        # The run row must record what actually broke the run, not the mailer.
        assert "502" in cur.fetchone()[0]


def test_missing_env_keys_names_every_absent_key(monkeypatch):
    for key in cli.REQUIRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MONID_API_KEY", "monid_live_x")
    missing = cli.missing_env_keys()
    assert "MONID_API_KEY" not in missing
    assert "GMAIL_APP_PASSWORD" in missing


def test_a_blank_env_key_counts_as_missing(monkeypatch):
    # A key left as `GMAIL_APP_PASSWORD=` in .env is present but useless.
    for key in cli.REQUIRED_ENV_KEYS:
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "   ")
    assert cli.missing_env_keys() == ["GMAIL_APP_PASSWORD"]


def test_scheduled_command_points_at_the_venv_executable():
    # A scheduled, non-interactive session does not get the interactive PATH, so
    # the task must name an absolute executable.
    command, working_dir = cli.scheduled_command()
    assert command.endswith(" run")
    assert command.startswith('"')
    assert "jobs" in command.lower()
    assert working_dir == str(config.PROJECT_ROOT)


def _setup_wired(pg, monkeypatch):
    monkeypatch.setattr(cli, "start_docker_desktop", lambda: None)
    monkeypatch.setattr(cli, "start_container", lambda: None)
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(db, "session", lambda: pg)


def test_setup_applies_the_schema_and_registers_the_task(pg, monkeypatch, capsys):
    _setup_wired(pg, monkeypatch)
    monkeypatch.setattr(cli.mailer, "verify_credentials", lambda: None)
    monkeypatch.setattr(cli, "missing_env_keys", list)
    registered = {}
    monkeypatch.setattr(cli.scheduling, "register",
                        lambda command, working_dir: registered.update(
                            command=command, dir=working_dir))
    monkeypatch.setattr(cli.scheduling, "wake_timer_state",
                        lambda: "Wake timers — plugged in: enabled")

    assert cli.command_setup() == 0
    assert registered["command"].endswith(" run")
    assert "Wake timers" in capsys.readouterr().out


def test_setup_stops_and_names_missing_keys(pg, monkeypatch, capsys):
    _setup_wired(pg, monkeypatch)
    monkeypatch.setattr(cli, "missing_env_keys", lambda: ["GMAIL_APP_PASSWORD"])
    registered = []
    monkeypatch.setattr(cli.scheduling, "register",
                        lambda command, working_dir: registered.append(1))
    assert cli.command_setup() == 1
    assert "GMAIL_APP_PASSWORD" in capsys.readouterr().out
    # Never leave a scheduled task behind that is guaranteed to fail.
    assert registered == []


def test_setup_stops_when_the_app_password_is_wrong(pg, monkeypatch, capsys):
    _setup_wired(pg, monkeypatch)
    monkeypatch.setattr(cli, "missing_env_keys", list)

    def refuse():
        raise RuntimeError("Gmail rejected the login for me@example.com")

    monkeypatch.setattr(cli.mailer, "verify_credentials", refuse)
    registered = []
    monkeypatch.setattr(cli.scheduling, "register",
                        lambda command, working_dir: registered.append(1))
    # Better to fail here, on the operator's screen, than silently at 9am.
    assert cli.command_setup() == 1
    assert "Gmail rejected" in capsys.readouterr().out
    assert registered == []


def test_setup_reports_a_container_that_will_not_start(pg, monkeypatch, capsys):
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.setattr(cli, "start_docker_desktop", lambda: None)
    monkeypatch.setattr(cli, "start_container",
                        _boom("Is Docker Desktop running?"))
    assert cli.command_setup() == 1
    assert "Docker Desktop" in capsys.readouterr().out


def test_setup_also_starts_docker_desktop(pg, monkeypatch, capsys):
    # Unlike `run`, setup is watched by a human — so here a Docker that will not
    # start is fatal and named, rather than left for wait_for_database to report.
    _setup_wired(pg, monkeypatch)
    monkeypatch.setattr(cli, "start_docker_desktop",
                        _boom("Docker Desktop never became ready"))
    assert cli.command_setup() == 1
    assert "never became ready" in capsys.readouterr().out


def test_pdf_writes_the_stored_bytes_to_the_current_directory(pg, monkeypatch,
                                                              tmp_path):
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.chdir(tmp_path)
    run_id = db.start_run("24h", pg)
    db.record_verdict("111", run_id, "Backend Dev", "Acme", 82, "matched", "r",
                      conn=pg)
    db.insert_match("111", run_id, title="Backend Dev", company="Acme",
                    location="Israel", apply_url="https://a/1",
                    posted_date="2026-08-12", canva_design_id="DAG1",
                    canva_url="https://c/1", pdf=b"%PDF-1.4 body",
                    pdf_filename="Acme_Backend_Dev_111.pdf", conn=pg)

    assert cli.command_pdf("111", open_after=False) == 0
    written = tmp_path / "Acme_Backend_Dev_111.pdf"
    assert written.read_bytes() == b"%PDF-1.4 body"


def test_pdf_reports_an_unknown_job_without_writing_anything(pg, monkeypatch,
                                                             tmp_path, capsys):
    monkeypatch.setattr(db, "session", lambda: pg)
    monkeypatch.chdir(tmp_path)
    assert cli.command_pdf("nope", open_after=False) == 1
    assert "nope" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_main_dispatches_each_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "command_setup", lambda: called.append("setup") or 0)
    monkeypatch.setattr(cli, "command_run", lambda: called.append("run") or 0)
    monkeypatch.setattr(cli, "command_pdf",
                        lambda job_id: called.append(f"pdf:{job_id}") or 0)
    assert cli.main(["setup"]) == 0
    assert cli.main(["run"]) == 0
    assert cli.main(["pdf", "4446164871"]) == 0
    assert called == ["setup", "run", "pdf:4446164871"]


def test_main_propagates_a_failing_exit_code(monkeypatch):
    # Task Scheduler reads this; a run that failed must not look successful.
    monkeypatch.setattr(cli, "command_run", lambda: 1)
    assert cli.main(["run"]) == 1


def test_main_with_no_command_shows_usage_and_fails(capsys):
    assert cli.main([]) == 2
    assert "setup" in capsys.readouterr().out


def test_config_loads_dotenv_before_reading_the_environment(monkeypatch):
    """Regression: config read os.environ at import time while load_dotenv ran
    later, inside command_setup — so GMAIL_* were empty strings no matter what
    .env held, and `jobs setup` reported credentials missing that were present."""
    import importlib

    import dotenv
    called = []
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: called.append(1))
    from jobsearch import config as config_module
    importlib.reload(config_module)
    assert called, "config must load .env before reading os.environ"
    importlib.reload(config_module)      # restore real values for later tests


def test_console_is_configured_for_non_ascii(monkeypatch):
    """Regression: printing the app-password hint (which contains an arrow) to a
    cp1252 console raised UnicodeEncodeError and took the command down."""
    calls = {}

    class FakeStream:
        def reconfigure(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(cli.sys, "stdout", FakeStream())
    monkeypatch.setattr(cli.sys, "stderr", FakeStream())
    cli._configure_console()
    assert calls["encoding"] == "utf-8"
    assert calls["errors"] == "replace"


def test_configure_console_survives_a_stream_without_reconfigure(monkeypatch):
    # pytest's captured stdout has no reconfigure(); this must not be fatal.
    monkeypatch.setattr(cli.sys, "stdout", object())
    monkeypatch.setattr(cli.sys, "stderr", object())
    cli._configure_console()


# --- preflight brings the database up instead of waiting for it ---

def test_run_starts_docker_before_waiting_for_the_database(wired, monkeypatch):
    """Regression: on 2026-08-14 the 9am task fired seven minutes after a reboot,
    found Docker Desktop not running (its AutoStart is off), waited two minutes
    for a container nobody was going to start, and died before writing a run row.
    Waiting is not enough — nothing else on this machine starts the database."""
    order = []
    monkeypatch.setattr(cli, "start_docker_desktop", lambda: order.append("docker"))
    monkeypatch.setattr(cli, "start_container", lambda: order.append("compose"))
    monkeypatch.setattr(cli, "wait_for_database", lambda: order.append("wait"))
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "", "cost": 0.0, "is_error": False})
    _stats(monkeypatch, fetched=0, dropped_seen=0, examined=0, matched=0)

    assert cli.command_run() == 0
    assert order == ["docker", "compose", "wait"]


def test_a_failed_recovery_does_not_stop_a_reachable_database(wired, monkeypatch):
    """Docker may be absent, or the database may be served some other way. The
    recovery is an attempt, not a precondition: whether the run proceeds is
    decided by Postgres answering."""
    monkeypatch.setattr(cli, "start_docker_desktop", _boom("no Docker Desktop"))
    monkeypatch.setattr(cli, "start_container", _boom("compose failed"))
    monkeypatch.setattr(cli, "wait_for_database", lambda: None)
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "", "cost": 0.0, "is_error": False})
    _stats(monkeypatch, fetched=0, dropped_seen=0, examined=0, matched=0)

    assert cli.command_run() == 0


def test_preflight_reports_the_database_error_not_the_docker_one(wired, monkeypatch):
    # "could not start Docker" is a symptom; "Postgres never answered" is the
    # fact the operator needs, and it names the port and the fix.
    monkeypatch.setattr(cli, "start_docker_desktop", _boom("no Docker Desktop"))
    monkeypatch.setattr(cli, "wait_for_database",
                        _boom("Postgres at :5433 did not answer"))
    assert cli.command_run() == 1
    assert "Postgres at :5433 did not answer" in wired["subject"]


def test_start_docker_desktop_does_nothing_when_the_daemon_answers(monkeypatch):
    monkeypatch.setattr(cli, "docker_is_running", lambda: True)
    launched = []
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: launched.append(a))
    cli.start_docker_desktop()
    assert launched == []


def test_start_docker_desktop_launches_it_and_waits_for_the_daemon(monkeypatch):
    # Cold-starting Docker Desktop takes 30-90s, so launching is not enough:
    # `docker compose up` against a daemon that is still booting just fails.
    answers = iter([False, False, True])
    monkeypatch.setattr(cli, "docker_is_running", lambda: next(answers))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    launched = []
    monkeypatch.setattr(cli.subprocess, "Popen",
                        lambda *a, **k: launched.append(a))
    cli.start_docker_desktop(attempts=5, delay=0)
    assert launched


def test_start_docker_desktop_gives_up_with_a_readable_error(monkeypatch):
    monkeypatch.setattr(cli, "docker_is_running", lambda: False)
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: None)
    with pytest.raises(RuntimeError) as excinfo:
        cli.start_docker_desktop(attempts=2, delay=0)
    assert "Docker" in str(excinfo.value)


def test_start_docker_desktop_names_a_missing_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "docker_is_running", lambda: False)
    monkeypatch.setattr(cli, "DOCKER_DESKTOP", tmp_path / "Docker Desktop.exe")
    with pytest.raises(RuntimeError) as excinfo:
        cli.start_docker_desktop(attempts=1, delay=0)
    assert "Docker Desktop.exe" in str(excinfo.value)


# --- an unattended run leaves a log behind ---

def test_log_path_is_one_file_per_day(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    assert cli.log_path(datetime(2026, 8, 15, 9, 0)).name == "run-2026-08-15.log"


def test_a_run_writes_its_progress_to_the_log(wired, monkeypatch):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "", "cost": 0.0, "is_error": False})
    _stats(monkeypatch, fetched=7, dropped_seen=0, examined=0, matched=0)
    assert cli.command_run() == 0
    written = cli.log_path().read_text(encoding="utf-8")
    assert "started" in written
    assert "exit 0" in written


def test_a_preflight_failure_is_written_to_the_log(wired, monkeypatch):
    """The 2026-08-14 failure left nothing behind: it died before the run row
    existed, and a scheduled task's console closes with the process. The whole
    diagnosis had to be inferred from a Windows exit code."""
    monkeypatch.setattr(cli, "wait_for_database",
                        _boom("Postgres at :5433 did not answer"))
    assert cli.command_run() == 1
    written = cli.log_path().read_text(encoding="utf-8")
    assert "Postgres at :5433 did not answer" in written
    assert "exit 1" in written


def test_the_log_does_not_swallow_the_real_stderr(wired, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "", "cost": 0.0, "is_error": False})
    _stats(monkeypatch, fetched=0, dropped_seen=0, examined=0, matched=0)
    cli.command_run()
    # Running `jobs run` by hand must still show its progress on the console.
    assert "started" in capsys.readouterr().err


def test_a_log_that_cannot_be_opened_does_not_break_the_run(wired, monkeypatch,
                                                             tmp_path):
    # Logging is a diagnostic aid. It must never become a new way for 9am to fail.
    monkeypatch.setattr(cli, "log_path", _boom("read-only filesystem"))
    monkeypatch.setattr(cli, "_drive_session",
                        lambda: {"summary": "", "cost": 0.0, "is_error": False})
    _stats(monkeypatch, fetched=0, dropped_seen=0, examined=0, matched=0)
    assert cli.command_run() == 0
