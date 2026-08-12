import mailer

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
