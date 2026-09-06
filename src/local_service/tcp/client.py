"""Control response sequences for the independent TCP service."""
from ..common.control import ControlClient


class TCPClient(ControlClient):
    @property
    def endpoint(self):
        return tuple(self.request("ping")["endpoint"])

    @property
    def responses(self):
        return self.returns
