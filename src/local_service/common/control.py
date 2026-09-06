"""Test control clients. These do not load a second copy of the native library."""
from .transport import rpc


class SequenceTarget:
    def __init__(self, client, target):
        self.client, self.target = client, target

    def set_sequence(self, values):
        return self.client.request("set_sequence", target=self.target, values=values)

    def snapshot(self):
        return self.client.request("sequences", target=self.target)[self.target]


class SequenceControl:
    def __init__(self, client):
        self.client = client

    def __getitem__(self, target):
        return SequenceTarget(self.client, target)

    def snapshot(self):
        return self.client.request("sequences")

    def events(self):
        return self.client.diagnostics()["events"]


class ControlClient:
    def __init__(self, endpoint, timeout=2.0):
        self.control_endpoint, self.timeout = endpoint, timeout
        self.returns = SequenceControl(self)

    def request(self, operation, **fields):
        return rpc(self.control_endpoint, {"op": operation, **fields}, self.timeout)

    def reset(self):
        return self.request("reset")

    def check_health(self):
        return self.request("health")

    def diagnostics(self):
        return self.request("diagnostics")
