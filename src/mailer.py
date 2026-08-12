"""The daily digest: compose it, then send it over Gmail SMTP.

Named mailer.py, not email.py — the latter shadows the stdlib `email` package and
breaks smtplib at import time.

Rendering is separated from sending so every flavour is testable without a socket.
The digest is built from committed database rows, never from the agent's own
summary of what it did: the model can be wrong about what it wrote, the rows
cannot.
"""
import html as html_escape
import smtplib
from email.message import EmailMessage

import config

_SUBJECT_LIMIT = 120


def _stats_line(run):
    return (f"Scanned {run['fetched_count']} postings in the last "
            f"{run['search_window']} · {run['skipped_seen_count']} already seen "
            f"· {run['examined_count']} judged · {run['matched_count']} matched")


def _subject(run, matches):
    # Failure wins over matches: a run can crash after writing some CVs, and a
    # broken morning must never read as a good one.
    if run["status"] == "failed":
        cause = (run.get("error") or "unknown error").splitlines()[0]
        subject = f"Job agent FAILED: {cause}"
        if len(subject) > _SUBJECT_LIMIT:
            return subject[:_SUBJECT_LIMIT - 1] + "…"
        return subject
    if not matches:
        return "No new matches today"
    noun = "match" if len(matches) == 1 else "matches"
    return f"{len(matches)} new job {noun}"


def _text_body(run, matches):
    if run["status"] == "failed":
        return (f"The run failed.\n\n{run.get('error') or 'unknown error'}\n\n"
                f"{_stats_line(run)}\n")
    if not matches:
        return (f"Nothing cleared the fit threshold today.\n\n"
                f"{_stats_line(run)}\n")
    blocks = []
    for match in matches:
        blocks.append(
            f"{match['title']} — {match['company']} · fit {match['fit_score']}\n"
            f"{match['reason']}\n"
            f"Apply:  {match['apply_url']}\n"
            f"Canva:  {match['canva_url']}\n"
            f"PDF:    jobs pdf {match['job_id']}\n")
    return "\n".join(blocks) + f"\n{_stats_line(run)}\n"


def _html_body(run, matches):
    esc = html_escape.escape
    if run["status"] == "failed":
        return (f"<p>The run failed.</p>"
                f"<pre>{esc(run.get('error') or 'unknown error')}</pre>"
                f"<p><small>{esc(_stats_line(run))}</small></p>")
    if not matches:
        return (f"<p>Nothing cleared the fit threshold today.</p>"
                f"<p><small>{esc(_stats_line(run))}</small></p>")
    blocks = []
    for match in matches:
        # Everything interpolated here comes from a job posting written by a
        # stranger, so every field is escaped — including the URLs.
        blocks.append(
            f"<div style='margin:0 0 20px 0'>"
            f"<div><strong>{esc(match['title'])}</strong> — "
            f"{esc(match['company'])} · fit {match['fit_score']}</div>"
            f"<div>{esc(match['reason'])}</div>"
            f"<div><a href='{esc(match['apply_url'])}'>Apply</a> · "
            f"<a href='{esc(match['canva_url'])}'>View CV in Canva</a> · "
            f"<code>jobs pdf {esc(str(match['job_id']))}</code></div>"
            f"</div>")
    return ("<div style='font-family:system-ui,sans-serif;font-size:15px'>"
            + "".join(blocks)
            + f"<p><small>{esc(_stats_line(run))}</small></p></div>")


def render_digest(run, matches):
    """(subject, text_body, html_body) for one finished run.

    Three flavours off run['status']: matches, empty, failed. One email arrives
    every morning whichever it is, so silence means the scheduler itself is
    broken — the one failure no in-app handling can report.
    """
    return (_subject(run, matches), _text_body(run, matches),
            _html_body(run, matches))


def _require_credentials():
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must both be set in .env. "
            "Generate an app password at Google Account → Security → App "
            "passwords (2-step verification must be on).")


def build_message(subject, text_body, html_body):
    """The digest as a multipart message, addressed from the operator to
    themselves. No images and no tracking — there is no one else to render for."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.GMAIL_ADDRESS
    message["To"] = config.GMAIL_ADDRESS
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def _smtp():
    """SMTP_SSL on 465: implicit TLS, so there is no plaintext phase to fail
    open on. The timeout is deliberate — a hung socket at 9am would otherwise
    hold the scheduled task open indefinitely."""
    return smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=30)


def _readable_failure(exc):
    """Turn an SMTP exception into something safe to print.

    Never let the app password into the message: this text reaches stderr, the
    run row, and Task Scheduler's history.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return RuntimeError(
            f"Gmail rejected the login for {config.GMAIL_ADDRESS}. App password "
            f"wrong, revoked, or 2-step verification off. (SMTP {exc.smtp_code})")
    return RuntimeError(f"Could not reach {config.SMTP_HOST}: {exc}")


def send(subject, text_body, html_body):
    """Send the digest. Raises on failure — the caller decides what that means."""
    _require_credentials()
    message = build_message(subject, text_body, html_body)
    try:
        with _smtp() as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise _readable_failure(exc) from None


def verify_credentials():
    """Log in and disconnect without sending. `jobs setup` calls this so a bad
    app password fails on the operator's screen, not silently at 9am."""
    _require_credentials()
    try:
        with _smtp() as smtp:
            smtp.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
    except (smtplib.SMTPException, OSError) as exc:
        raise _readable_failure(exc) from None
