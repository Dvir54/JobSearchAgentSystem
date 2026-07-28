import json
from pathlib import Path

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
    ])
    env = json.loads(reduce_run_payload(payload))
    assert env["window"] == "week"
    assert env["fetched"] == 4
    assert env["kept"] == 2
    assert env["dropped_duplicate"] == 1
    assert env["dropped_non_israel"] == 1
    assert [j["id"] for j in env["jobs"]] == ["1", "3"]


def test_kept_jobs_carry_only_the_seven_needed_fields():
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")])))
    assert set(env["jobs"][0]) == {"id", "title", "company", "description",
                                   "url", "posted_date", "location"}


def test_posted_date_survives_reduction():
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Tel Aviv, Israel")])))
    assert env["jobs"][0]["posted_date"] == "2026-07-27T01:47:21.000Z"


def test_descriptions_are_kept_in_full():
    long_desc = "Requirements: " + ("Python and SQL. " * 500)
    env = json.loads(reduce_run_payload(_run(output=[_raw("1", "Israel", description=long_desc)])))
    assert env["jobs"][0]["description"] == long_desc


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


def test_missing_output_list_passes_through():
    assert reduce_run_payload(json.dumps({"status": "COMPLETED"})) is None


def test_malformed_items_pass_through_rather_than_raise():
    # normalize_posting requires id/title/linkedinUrl; a junk item must not
    # blow up the run — it must fall back to the raw payload.
    payload = _run(output=[{"nonsense": True}])
    assert reduce_run_payload(payload) is None


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
    # ...and none of it came out of the descriptions
    by_id = {str(i["id"]): i.get("descriptionText", "") for i in items}
    for job in env["jobs"]:
        assert job["description"] == by_id[job["id"]]
