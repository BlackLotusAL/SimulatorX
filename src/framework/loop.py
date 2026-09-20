"""A bounded periodic worker; state synchronization belongs to its owner."""
import threading
import math


class PeriodicLoop:
    def __init__(self, interval, tick, failed):
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval <= 0:
            raise ValueError("Tick interval must be a positive finite number")
        self.interval, self.tick, self.failed = interval, tick, failed
        self._stop = threading.Event()
        self.thread = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() and not self._stop.is_set())

    def start(self):
        if self.thread is not None:
            raise RuntimeError("Periodic worker already started")
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, name="simulatorx-tick", daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception as exc:
                self.failed(exc)
                return

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("Periodic worker did not stop")
            self.thread = None
