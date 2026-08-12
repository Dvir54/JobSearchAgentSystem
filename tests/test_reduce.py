import json
from pathlib import Path

import config
import tooling
from tooling import reduce_run_payload

FIXTURE = Path(__file__).parent / "fixtures" / "harvestapi_response.json"


def _raw(job_id, location, title="Developer", description="desc"):
    return {"id": job_id, "title": title, "company": {"name": "Acme"},
            "descriptionText": description, "linkedinUrl": "https://x",
            "postedDate": "2026-07-27T01:47:21.000Z",
            "location": {"linkedinText": location}}


def _run(status="COMPLETED", output=None, window="week", run_id="01TEST"):
    return json.dumps({
        "runId": run_id,
        "status": status,
        "input": {"body": {"postedLimit": window}},
        "output": output if output is not None else [],
    }, ensure_ascii=False)


def test_reduces_completed_run_to_envelope_with_counts():
    payload = _run(output=[
        _raw("1", "Tel Aviv, Israel"),
        _raw("1", "Tel Aviv, Israel"),     # duplicate id
        _raw("2", "EMEA"),                 # not Israel
        _raw("3", "Haifa, Israel"),
    ], run_id="01ABC")
    env = json.loads(reduce_run_payload(payload))
    assert env["status"] == "COMPLETED"
    assert env["runId"] == "01ABC"
    assert env["window"] == "week"
    assert env["fetched"] == 4
    assert env["kept"] == 2
    assert env["dropped_duplicate"] == 1
    assert env["dropped_non_israel"] == 1
    assert [j["id"] for j in env["jobs"]] == ["1", "3"]


def test_envelope_carries_status_and_run_id_for_polling_agent():
    # WORKFLOW step 3 tells the agent that receiving this envelope means the
    # run is done, so status/runId must be present and correct.
    payload = _run(output=[_raw("1", "Tel Aviv, Israel")], run_id="01XYZ")
    env = json.loads(reduce_run_payload(payload))
    assert set(env) == {"status", "runId", "window", "fetched", "kept",
                         "dropped_duplicate", "dropped_non_israel",
                         "dropped_seen", "jobs", "note"}
    assert env["status"] == "COMPLETED"
    assert env["runId"] == "01XYZ"


def test_manifest_carries_only_id_title_company():
    """Descriptions are the bulk, and all of them together blew the 32KB inline
    ceiling on 2026-08-11. The manifest is what the agent can be handed at once;
    everything else comes from get_job."""
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")])))
    assert set(env["jobs"][0]) == {"id", "title", "company"}


def test_posted_date_survives_reduction():
    reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")]))
    assert tooling.get_job("1")["posted_date"] == "2026-07-27T01:47:21.000Z"


def test_descriptions_are_kept_in_full():
    """Held in process rather than serialised — but never trimmed."""
    long_desc = "Requirements: " + ("Python and SQL. " * 500)
    reduced = reduce_run_payload(_run(output=[_raw("1", "Israel", description=long_desc)]))
    assert long_desc not in reduced           # not in the envelope...
    assert tooling.get_job("1")["description"] == long_desc   # ...but intact here


def test_window_missing_is_null_not_fatal():
    payload = json.dumps({"runId": "01TEST", "status": "COMPLETED", "input": {},
                          "output": [_raw("1", "Israel")]})
    env = json.loads(reduce_run_payload(payload))
    assert env["window"] is None
    assert env["kept"] == 1


def test_running_run_passes_through():
    assert reduce_run_payload(_run(status="RUNNING", output=None)) is None


def test_failed_run_passes_through():
    assert reduce_run_payload(_run(status="FAILED")) is None


def test_unparseable_text_passes_through():
    assert reduce_run_payload("not json at all") is None


def test_oversized_stub_declines_with_actionable_log(capsys):
    # The real defect: the CLI truncates an oversized MCP result to a file
    # BEFORE PostToolUse hooks run, so the hook receives this stub instead of
    # JSON. json.loads fails, and the decline must be loud and actionable —
    # not silent, since a silent decline let the raw 787KB payload through and
    # cost $7.19 in one run.
    stub = ("Error: result (774,006 characters) exceeds maximum allowed tokens. "
            "Output has been saved to C:\\x\\y.txt.")
    assert reduce_run_payload(stub) is None
    captured = capsys.readouterr()
    assert "DECLINED" in captured.err
    assert "MAX_MCP_OUTPUT_TOKENS" in captured.err


def test_running_status_declines_with_informational_log(capsys):
    assert reduce_run_payload(_run(status="RUNNING", output=None)) is None
    captured = capsys.readouterr()
    assert "RUNNING" in captured.err


def test_missing_output_list_passes_through():
    assert reduce_run_payload(json.dumps({"status": "COMPLETED"})) is None


def test_malformed_items_pass_through_rather_than_raise():
    # normalize_posting requires id/title/linkedinUrl; a junk item must not
    # blow up the run — it must fall back to the raw payload.
    payload = _run(output=[{"nonsense": True}])
    assert reduce_run_payload(payload) is None


def test_non_dict_input_field_does_not_raise():
    # run["input"] as a non-dict (e.g. a string) makes `_window` call .get on
    # a str and raise AttributeError. That must degrade to pass-through, not
    # escape the reducer.
    payload = json.dumps({"runId": "01TEST", "status": "COMPLETED",
                          "input": "not-a-dict",
                          "output": [_raw("1", "Tel Aviv, Israel")]})
    assert reduce_run_payload(payload) is None


def test_all_filtered_out_still_returns_envelope_with_kept_zero(capsys):
    # If harvestapi ever reshapes `location`, every item can fail the Israel
    # filter. The envelope must still be returned (not a silent None -> raw
    # 284K-token dump) and the situation must be loudly logged.
    payload = _run(output=[_raw("1", "EMEA"), _raw("2", "Nowhere, Nowhere")])
    env = json.loads(reduce_run_payload(payload))
    assert env["fetched"] == 2
    assert env["kept"] == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "location" in captured.err


def test_dict_tool_response_is_also_accepted():
    payload = json.loads(_run(output=[_raw("1", "Israel")]))
    env = json.loads(reduce_run_payload(payload))
    assert env["kept"] == 1


def test_real_scrape_shrinks_hard_and_preserves_descriptions():
    items = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = _run(output=items)
    reduced = reduce_run_payload(payload)
    env = json.loads(reduced)

    assert env["fetched"] == 99
    assert env["kept"] == 47
    assert env["dropped_duplicate"] == 17
    assert env["dropped_non_israel"] == 35
    # the whole point: a large majority of the payload is gone
    assert len(reduced) < len(payload) * 0.25
    # every kept job is listed, and the envelope now fits inline with room to spare
    assert len(env["jobs"]) == env["kept"] == 47
    assert len(reduced) < config.SAFE_ENVELOPE_BYTES
    # ...and none of it came out of the descriptions
    by_id = {str(i["id"]): i.get("descriptionText", "") for i in items}
    for job in env["jobs"]:
        assert tooling.get_job(job["id"])["description"] == by_id[job["id"]]


def test_is_run_in_progress_detects_polling_states():
    from tooling import is_run_in_progress
    assert is_run_in_progress(json.dumps({"status": "RUNNING"})) is True
    assert is_run_in_progress(json.dumps({"status": "PENDING"})) is True


def test_is_run_in_progress_false_for_terminal_states():
    from tooling import is_run_in_progress
    for terminal in ("COMPLETED", "FAILED", "BLOCKED", "TIMED_OUT"):
        assert is_run_in_progress(json.dumps({"status": terminal})) is False


def test_is_run_in_progress_never_raises_on_junk():
    from tooling import is_run_in_progress
    assert is_run_in_progress("not json") is False
    assert is_run_in_progress(None) is False
    assert is_run_in_progress(json.dumps([1, 2, 3])) is False
    assert is_run_in_progress(json.dumps({"no": "status"})) is False


def test_previously_seen_jobs_are_dropped_before_the_model_sees_them(monkeypatch):
    monkeypatch.setattr(tooling, "_unseen_ids",
                        lambda ids: {str(i) for i in ids if str(i) != "111"})
    payload = _run(output=[_raw("111", "Tel Aviv, Israel"),
                           _raw("222", "Haifa, Israel")])
    envelope = json.loads(reduce_run_payload(payload))
    assert [job["id"] for job in envelope["jobs"]] == ["222"]
    assert envelope["kept"] == 1
    assert envelope["dropped_seen"] == 1
    assert tooling.last_run_stats()["dropped_seen"] == 1


def test_a_seen_job_is_not_retrievable_by_get_job(monkeypatch):
    # Dropped means gone, not hidden: if it stayed in _JOBS_BY_ID the agent could
    # still pull the description and pay for it.
    monkeypatch.setattr(tooling, "_unseen_ids", lambda ids: set())
    reduce_run_payload(_run(output=[_raw("111", "Tel Aviv, Israel")]))
    assert "error" in tooling.get_job("111")


def test_dedup_failure_degrades_to_scoring_everything(monkeypatch, capsys):
    def boom(ids):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tooling, "_query_unseen_ids", boom)
    payload = _run(output=[_raw("111", "Tel Aviv, Israel")])
    envelope = json.loads(reduce_run_payload(payload))
    # Losing dedup costs money; losing the run costs the day. Keep the run.
    assert [job["id"] for job in envelope["jobs"]] == ["111"]
    assert envelope["dropped_seen"] == 0
    assert "cross-run dedup unavailable" in capsys.readouterr().err
