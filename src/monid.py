"""Monid (monid.ai) transport: run an endpoint and wait for its result.

Knows nothing about jobs. This is the swap seam — pointing the pipeline at a
different Monid endpoint (e.g. TikHub, once it recovers) is a config change,
not a rewrite. harvestapi runs are asynchronous, so run_and_wait polls.
"""
import time

from config import MONID_API_BASE

POLL_INTERVAL_SECONDS = 3
RUN_TIMEOUT_SECONDS = 180


def run_and_wait(session, provider, endpoint, run_input):
    """POST /v1/run, then poll GET /v1/runs/{id} until the run finishes.

    Returns the run's output as a list of raw items. Raises RuntimeError on a
    FAILED run, a provider httpStatus >= 400, or a timeout.
    """
    resp = session.post(
        f"{MONID_API_BASE}/run",
        json={"provider": provider, "endpoint": endpoint, "input": run_input},
        timeout=60,
    )
    data = resp.json()
    run_id = data["runId"]
    status = data.get("status")

    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while status not in ("COMPLETED", "FAILED"):
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Monid run {run_id} for {provider}{endpoint} timed out "
                f"after {RUN_TIMEOUT_SECONDS}s"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        data = session.get(f"{MONID_API_BASE}/runs/{run_id}", timeout=60).json()
        status = data.get("status")

    if status == "FAILED":
        raise RuntimeError(f"Monid run {run_id} for {provider}{endpoint} FAILED")

    provider_status = (data.get("providerResponse") or {}).get("httpStatus")
    if provider_status is not None and provider_status >= 400:
        raise RuntimeError(
            f"{provider}{endpoint} provider error: httpStatus {provider_status}"
        )

    output = data.get("output")
    return output if isinstance(output, list) else []
