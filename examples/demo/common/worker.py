"""One manual operation per process, with native routing fixed before startup."""
import json
import sys
import threading
import time
from .business import controller


def run(create_sut, start_operation):
    descriptor = json.loads(sys.argv[1])
    sut = create_sut(descriptor)
    cancel = threading.Event()
    def parent():
        sys.stdin.readline()
        cancel.set()
    threading.Thread(target=parent, daemon=True).start()
    try:
        operation = start_operation(sut, descriptor)
        while True:
            if cancel.is_set():
                sut.close()
            status = sut.get_status(operation)
            print(json.dumps(controller(status), ensure_ascii=True), flush=True)
            if status["state"] != "running":
                break
            time.sleep(0.05)
    finally:
        sut.close()
