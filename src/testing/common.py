"""Shared SDK/TCP fixture ownership, diagnostics and cleanup."""
import hashlib
import json
from pathlib import Path


def _owned_service(request, kind):
    from local_service.process import ServiceProcess
    process = ServiceProcess(kind, profile=request.config.getoption("--sdk-profile") if kind == "sdk" else None,
                             protocol=request.config.getoption("--tcp-protocol") if kind == "tcp" else None)
    request.addfinalizer(process.stop)
    process.start()
    client = process.client
    client.service_process = process
    return client


def _case_environment(client, request, kind):
    def restore():
        failures = []
        diagnostics = {}
        try:
            diagnostics = client.diagnostics()
        except Exception as exc:
            failures.append(exc)
            diagnostics["diagnostic_error"] = str(exc)
        process = getattr(client, "service_process", None)
        if process:
            try:
                diagnostics["service_log"] = process.log()
            except Exception as exc:
                failures.append(exc)
                diagnostics["service_log_error"] = str(exc)
        try:
            root = Path(request.config.getoption("--simulatorx-artifacts"))
            root.mkdir(parents=True, exist_ok=True)
            identity = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
            path = root / (kind + "-" + identity + ".json")
            path.write_text(json.dumps({"test": request.node.nodeid, **diagnostics},
                                       ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        except Exception as exc:
            failures.append(exc)
        # Attempt all cleanup stages even when initial setup or diagnostics fail.
        for action in (client.check_health, client.reset):
            try:
                action()
            except Exception as exc:
                failures.append(exc)
        if failures:
            request.session.shouldstop = kind.upper() + " cleanup failed; environment must not be reused"
            raise RuntimeError(request.session.shouldstop + ": " + "; ".join(str(e) for e in failures))

    request.addfinalizer(restore)
    client.check_health()
    client.reset()
    return client
