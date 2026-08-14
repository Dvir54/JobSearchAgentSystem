"""The 32KB inline-result ceiling, replayed against the real payload that hit it.

The 2026-08-11 live run reduced a 774KB scrape to a 55,198-byte envelope and still
failed: the CLI writes any tool result at or above 32,768 bytes to disk and hands
the model a 2,000-byte preview, so the agent saw 1 job of 22 and stopped. Unit tests
with two synthetic jobs could never have caught that — only the real shape does.

`REAL_RUN` is that exact scrape, kept as a fixture. If it is missing (a fresh clone,
another machine), these tests skip rather than pass silently.
"""
import json
from pathlib import Path

import pytest

from jobsearch import config
from jobsearch.agent import tooling

REAL_RUN = Path(__file__).parent / "fixtures" / "monid_run_2026-08-11.json"

pytestmark = pytest.mark.skipif(
    not REAL_RUN.exists(),
    reason=f"real-payload fixture not present at {REAL_RUN}")


@pytest.fixture
def reduced():
    raw = json.loads(REAL_RUN.read_text(encoding="utf-8"))
    text = tooling.reduce_run_payload(raw)
    assert text is not None, "the reducer declined the real payload"
    return text, json.loads(text)


def test_the_real_payload_that_broke_the_run_now_fits_inline(reduced):
    text, envelope = reduced
    assert len(text) < config.INLINE_RESULT_LIMIT_BYTES
    assert len(text) < config.SAFE_ENVELOPE_BYTES
    # it was 55,198 bytes; anything near that means the fix regressed
    assert len(text) < 5000, f"envelope grew back to {len(text)} bytes"
    assert envelope["kept"] == 22
    assert envelope["fetched"] == 97


def test_every_kept_job_is_listed_not_just_the_ones_that_fit(reduced):
    """The failure mode was seeing 1 job of 22. `kept` must equal what is listed."""
    _, envelope = reduced
    assert len(envelope["jobs"]) == envelope["kept"] == 22
    assert "manifest_truncated_to" not in envelope


def test_no_description_text_crosses_into_the_envelope(reduced):
    text, envelope = reduced
    assert all(set(job) == {"id", "title", "company"} for job in envelope["jobs"])
    # a phrase from a real posting's description must not appear anywhere in it
    full = tooling.get_job(envelope["jobs"][0]["id"])
    probe = full["description"][200:260]
    assert probe and probe not in text


def test_get_job_returns_each_posting_in_full(reduced):
    _, envelope = reduced
    for entry in envelope["jobs"]:
        job = tooling.get_job(entry["id"])
        assert job["error"] == ""
        assert job["id"] == entry["id"]
        assert job["title"] == entry["title"]
        assert job["description"], f"{entry['company']} came back with no description"
        assert job["url"].startswith("http")
        assert "israel" in job["location"].lower()


def test_no_single_posting_can_approach_the_ceiling(reduced):
    _, envelope = reduced
    for entry in envelope["jobs"]:
        served = json.dumps(tooling.get_job(entry["id"]), ensure_ascii=False)
        assert len(served) < config.INLINE_RESULT_LIMIT_BYTES


def test_descriptions_survive_intact_at_this_size(reduced):
    """The point of holding them in process is that they are NOT trimmed."""
    _, envelope = reduced
    raw = json.loads(REAL_RUN.read_text(encoding="utf-8"))
    by_id = {str(item["id"]): item for item in raw["output"]}
    for entry in envelope["jobs"]:
        served = tooling.get_job(entry["id"])["description"]
        original = by_id[str(entry["id"])].get("descriptionText") or ""
        # byte-for-byte, trailing whitespace included — the pipeline must not be
        # quietly reshaping the text the fit score is judged from
        assert served == original, f"{entry['company']} was altered"


def test_unknown_job_id_costs_one_job_not_the_run(reduced):
    out = tooling.get_job("no-such-id")
    assert out["error"] and "no-such-id" in out["error"]


def test_an_absurd_description_is_capped_rather_than_breaching(reduced):
    _, envelope = reduced
    victim = envelope["jobs"][0]["id"]
    tooling._JOBS_BY_ID[str(victim)]["description"] = "x" * 60000
    served = tooling.get_job(victim)
    assert len(served["description"]) <= config.MAX_JOB_DESCRIPTION_CHARS + 40
    assert "truncated" in served["description"]
    assert len(json.dumps(served)) < config.INLINE_RESULT_LIMIT_BYTES
