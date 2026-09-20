"""Run a managed service until shutdown or a failed health check."""
import json
import sys
import threading
from pathlib import Path

from .messages import EnvironmentError
from .pipe import serve_requests


def serve(service, ready_file=None, managed=False, replies=None):
    """Stop on parent stdin EOF, explicit input, interrupt, or failed health."""
    stopped = threading.Event()
    parent_errors = []
    try:
        service.start()
        info = service.info()
        if ready_file:
            path = Path(ready_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(info), encoding="utf-8")
            temporary.replace(path)
        print(json.dumps({"event": "ready", **info}), flush=True)
        if managed:
            def parent():
                try:
                    if replies is None:
                        sys.stdin.readline()
                    else:
                        serve_requests(service, sys.stdin, replies)
                except Exception as exc:
                    parent_errors.append(exc)
                finally:
                    stopped.set()
            threading.Thread(target=parent, daemon=True).start()
        while not stopped.wait(0.05):
            service.check_health()
        if parent_errors:
            raise EnvironmentError("Parent communication failed: " + str(parent_errors[0]))
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
