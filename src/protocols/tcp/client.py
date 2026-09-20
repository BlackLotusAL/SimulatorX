"""Control response sequences for the independent TCP service."""
from framework.control import ControlClient, SequenceControl


class TCPClient(ControlClient):
    def __init__(self, requests):
        self.requests = requests
        self.returns = SequenceControl(self)

    def request(self, operation, **fields):
        return self.requests.request({"op": operation, **fields})

    def close(self):
        self.requests.close()

    @property
    def endpoint(self):
        return tuple(self.request("ping")["endpoint"])

    @property
    def responses(self):
        return self.returns
