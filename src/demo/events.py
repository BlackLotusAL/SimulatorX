"""Structured events shared by the controller and the pytest reporter."""
import json
import threading
import time


class EventWriter:
    def __init__(self, path):
        self.path = path
        self.case_id = None
        self.lock = threading.Lock()

    def emit(self, kind, message, **data):
        event = {"time": time.time(), "kind": kind, "message": message,
                 "case_id": self.case_id, **data}
        with self.lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
