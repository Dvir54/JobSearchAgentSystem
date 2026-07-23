import pytest

import monid


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Records nothing; returns canned payloads. get() walks the list, then
    repeats the last entry."""

    def __init__(self, post_payload, get_payloads=None):
        self._post_payload = post_payload
        self._get_payloads = list(get_payloads or [])
        self.get_calls = 0

    def post(self, url, json=None, timeout=None):
        return FakeResp(self._post_payload)

    def get(self, url, timeout=None):
        i = min(self.get_calls, len(self._get_payloads) - 1)
        self.get_calls += 1
        return FakeResp(self._get_payloads[i])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(monid.time, "sleep", lambda s: None)


def test_sync_completed_returns_items_without_polling():
    session = FakeSession({
        "runId": "r1", "status": "COMPLETED",
        "providerResponse": {"httpStatus": 200}, "output": [{"id": 1}, {"id": 2}],
    })
    items = monid.run_and_wait(session, "apify", "/x", {"body": {}})
    assert items == [{"id": 1}, {"id": 2}]
    assert session.get_calls == 0


def test_async_polls_then_returns_items():
    session = FakeSession(
        {"runId": "r1", "status": "RUNNING"},
        get_payloads=[
            {"runId": "r1", "status": "RUNNING"},
            {"runId": "r1", "status": "COMPLETED",
             "providerResponse": {"httpStatus": 200}, "output": [{"id": 9}]},
        ],
    )
    items = monid.run_and_wait(session, "apify", "/x", {"body": {}})
    assert items == [{"id": 9}]
    assert session.get_calls == 2


def test_provider_http_error_raises():
    session = FakeSession({
        "runId": "r1", "status": "COMPLETED",
        "providerResponse": {"httpStatus": 400}, "output": None,
    })
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "tikhub", "/x", {"queryParams": {}})


def test_failed_run_raises():
    session = FakeSession({"runId": "r1", "status": "FAILED"})
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "apify", "/x", {"body": {}})


def test_timeout_raises(monkeypatch):
    monkeypatch.setattr(monid, "RUN_TIMEOUT_SECONDS", -1)  # deadline already in the past
    session = FakeSession(
        {"runId": "r1", "status": "RUNNING"},
        get_payloads=[{"runId": "r1", "status": "RUNNING"}],
    )
    with pytest.raises(RuntimeError):
        monid.run_and_wait(session, "apify", "/x", {"body": {}})
