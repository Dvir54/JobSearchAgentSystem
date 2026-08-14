from jobsearch.delivery import mailer

RUN_OK = {"id": 7, "status": "ok", "search_window": "24h", "fetched_count": 111,
          "skipped_seen_count": 80, "examined_count": 27, "matched_count": 2,
          "error": None}
MATCHES = [
    {"job_id": "444", "title": "Backend Developer", "company": "Alignerr",
     "apply_url": "https://apply/444", "canva_url": "https://canva/444",
     "fit_score": 87, "reason": "Python and Postgres in the core stack"},
    {"job_id": "555", "title": "QA Engineer", "company": "Fives",
     "apply_url": "https://apply/555", "canva_url": "https://canva/555",
     "fit_score": 74, "reason": "Automation focus matches the internship"},
]


def test_matched_digest_subject_counts_the_matches():
    subject, _, _ = mailer.render_digest(RUN_OK, MATCHES)
    assert subject == "2 new job matches"


def test_matched_digest_carries_every_job_and_its_retrieval_command():
    _, text, html = mailer.render_digest(RUN_OK, MATCHES)
    for body in (text, html):
        assert "Backend Developer" in body
        assert "Alignerr" in body
        assert "87" in body
        assert "Python and Postgres in the core stack" in body
        assert "https://apply/444" in body
        assert "https://canva/444" in body
        assert "jobs pdf 444" in body          # the digest has no attachments
    assert text.index("Backend Developer") < text.index("QA Engineer")


def test_matched_digest_reports_the_run_stats():
    _, text, _ = mailer.render_digest(RUN_OK, MATCHES)
    assert "111" in text and "80" in text and "27" in text and "24h" in text


def test_empty_run_says_so_without_listing_jobs():
    run = dict(RUN_OK, status="empty", matched_count=0)
    subject, text, _ = mailer.render_digest(run, [])
    assert subject == "No new matches today"
    assert "111" in text                       # the stats still appear
    assert "jobs pdf" not in text


def test_failed_run_names_the_cause_in_the_subject_and_body():
    run = dict(RUN_OK, status="failed", error="Monid returned 502 after 3 attempts")
    subject, text, _ = mailer.render_digest(run, [])
    assert subject.startswith("Job agent FAILED:")
    assert "502" in subject
    assert "Monid returned 502 after 3 attempts" in text


def test_failed_subject_is_truncated_for_a_huge_error():
    run = dict(RUN_OK, status="failed", error="x" * 500)
    subject, _, _ = mailer.render_digest(run, [])
    assert len(subject) <= 120                 # a subject line, not a stack trace


def test_failed_subject_takes_only_the_first_line_of_a_traceback():
    run = dict(RUN_OK, status="failed",
               error="RuntimeError: boom\n  File x, line 3\n  File y, line 9")
    subject, _, _ = mailer.render_digest(run, [])
    assert subject == "Job agent FAILED: RuntimeError: boom"


def test_html_escapes_a_job_title_that_contains_markup():
    # Titles come from LinkedIn and land in an inbox unfiltered.
    hostile = [dict(MATCHES[0], title="Dev <script>alert(1)</script>")]
    _, _, html = mailer.render_digest(RUN_OK, hostile)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_singular_subject_for_one_match():
    subject, _, _ = mailer.render_digest(dict(RUN_OK, matched_count=1),
                                         MATCHES[:1])
    assert subject == "1 new job match"


def test_a_failed_run_that_produced_matches_still_reports_the_failure():
    # Partial progress is real: rows survive a crash. The subject must still say
    # the run failed, or a broken run reads as a good morning.
    run = dict(RUN_OK, status="failed", error="crashed after two CVs")
    subject, text, _ = mailer.render_digest(run, MATCHES)
    assert subject.startswith("Job agent FAILED:")
    assert "crashed after two CVs" in text


def test_build_message_is_multipart_with_both_bodies(monkeypatch):
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    message = mailer.build_message("Subject", "plain", "<p>rich</p>")
    assert message["Subject"] == "Subject"
    assert message["From"] == "me@example.com"
    assert message["To"] == "me@example.com"        # mail from you to yourself
    assert message.get_body("plain").get_content().strip() == "plain"
    assert "<p>rich</p>" in message.get_body("html").get_content()


def test_non_ascii_survives_the_encode(monkeypatch):
    # Israeli listings bring Hebrew company names, and the digest uses em-dashes.
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    body = "מובילאיי — fit 82"
    message = mailer.build_message("נמצאו משרות", body, f"<p>{body}</p>")
    assert message.get_body("plain").get_content().strip() == body
    raw = message.as_bytes()
    assert b"\xd7\x9e" in raw or b"=?utf-8?" in raw.lower()


def test_send_logs_in_and_sends_once(monkeypatch):
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "app-password")
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["endpoint"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, message):
            calls["subject"] = message["Subject"]

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    mailer.send("Subject", "plain", "<p>rich</p>")
    assert calls["endpoint"] == (config.SMTP_HOST, config.SMTP_PORT)
    assert calls["login"] == ("me@example.com", "app-password")
    assert calls["subject"] == "Subject"


def test_send_refuses_when_credentials_are_missing(monkeypatch):
    import pytest
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "")
    with pytest.raises(RuntimeError) as excinfo:
        mailer.send("Subject", "plain", "<p>rich</p>")
    assert "GMAIL_ADDRESS" in str(excinfo.value)


def test_a_failing_login_does_not_leak_the_password(monkeypatch):
    import pytest
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "sixteen-char-sec")

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            raise mailer.smtplib.SMTPAuthenticationError(535, b"bad password")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    with pytest.raises(RuntimeError) as excinfo:
        mailer.verify_credentials()
    # This text reaches stderr, the run row, and Task Scheduler history.
    assert "sixteen-char-sec" not in str(excinfo.value)
    assert "App password" in str(excinfo.value)


def test_an_unreachable_server_is_reported_readably(monkeypatch):
    import pytest
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "pw")

    def boom(host, port, timeout=None):
        raise OSError("getaddrinfo failed")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", boom)
    with pytest.raises(RuntimeError) as excinfo:
        mailer.send("s", "t", "<p>h</p>")
    assert "smtp.gmail.com" in str(excinfo.value)


def test_a_spaced_app_password_is_normalised(monkeypatch):
    # Google shows app passwords as "abcd efgh ijkl mnop"; pasting it verbatim
    # is the obvious thing to do, and Gmail's SMTP login rejects the spaces.
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    assert mailer.app_password() == "abcdefghijklmnop"


def test_send_logs_in_with_the_normalised_password(monkeypatch):
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    used = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            used["password"] = password

        def send_message(self, message):
            pass

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    mailer.send("s", "t", "<p>h</p>")
    assert used["password"] == "abcdefghijklmnop"


def test_a_password_of_only_spaces_counts_as_missing(monkeypatch):
    import pytest
    from jobsearch import config
    monkeypatch.setattr(config, "GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "    ")
    with pytest.raises(RuntimeError):
        mailer.send("s", "t", "<p>h</p>")
