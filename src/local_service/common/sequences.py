"""Atomic response sequences shared by the independent SDK and TCP services."""
import copy
import threading


class Sequences:
    def __init__(self):
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        with self._lock:
            self._entries = {}

    def set(self, target, values):
        if not isinstance(values, list):
            raise ValueError("A sequence must be a list")
        with self._lock:
            previous = self._entries.get(target, {})
            self._entries[target] = {
                "values": copy.deepcopy(values), "cursor": 0,
                "calls": previous.get("calls", 0),
                "generation": previous.get("generation", 0) + 1,
            }

    def take(self, target):
        with self._lock:
            entry = self._entries.setdefault(target, {
                "values": [], "cursor": 0, "calls": 0, "generation": 0,
            })
            index = entry["cursor"]
            overridden = index < len(entry["values"])
            entry["calls"] += 1
            result = {
                "target": target, "call": entry["calls"],
                "generation": entry["generation"], "overridden": overridden,
                "sequence_index": index if overridden else None,
                "value": copy.deepcopy(entry["values"][index]) if overridden else None,
            }
            if overridden:
                entry["cursor"] += 1
            return result

    def snapshot(self, target=None):
        with self._lock:
            entries = self._entries if target is None else {
                target: self._entries.get(target, {
                    "values": [], "cursor": 0, "calls": 0, "generation": 0,
                })
            }
            return {key: {**copy.deepcopy(value),
                          "remaining": len(value["values"]) - value["cursor"]}
                    for key, value in entries.items()}
