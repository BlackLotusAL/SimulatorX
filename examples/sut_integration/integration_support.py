"""Test-project lifecycle glue, deliberately outside the SimulatorX plugin."""
import hashlib
import json
import time
from pathlib import Path


def finish_sut(sut, device, request):
    failures = []
    try:
        sut.close()
    except Exception as exc:
        failures.append(exc)
        # A remaining writer must never run across Reset. Stop the environment;
        # the public device finalizer will then reject health checks and Reset.
        try:
            device.stop()
        except Exception as cleanup:
            failures.append(cleanup)
    try:
        root = Path(request.config.getoption("--sut-artifacts"))
        root.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
        (root / ("sut-" + identity + ".json")).write_text(json.dumps(
            {"test": request.node.nodeid, **sut.diagnostics()},
            ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    except Exception as exc:
        failures.append(exc)
    if failures:
        request.session.shouldstop = "SUT cleanup failed; environment must not be reused"
        raise RuntimeError(request.session.shouldstop + ": " + "; ".join(map(str, failures)))


def terminal(sut, operation_id, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = sut.get_status(operation_id)
        if status["state"] != "running":
            return status
        time.sleep(0.01)
    raise AssertionError("SUT did not report a terminal state: " + repr(sut.get_status(operation_id)))
