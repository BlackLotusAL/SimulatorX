"""SDK model control client, independent of the native SDK call channel."""
from ..common.control import ControlClient


class SDKClient(ControlClient):
    @property
    def launch_environment(self):
        info = self.request("ping")
        return {"SIMULATORX_CONTROL_SOCKET": info["sdk_socket"],
                "SIMULATORX_SDK_ERROR_FILE": info["error_file"],
                "SIMULATORX_CONTROL_TIMEOUT_MS": "1000"}

    def snapshot(self):
        return self.request("snapshot")

    def set_fault(self, name, active):
        return self.request("set_fault", name=name, active=active)

    def call(self, function, *, sdk="rotary", **args):
        """Reference SDK operation via control RPC, useful for service tests.

        SUT integration tests must call the actual .so in the SUT process.
        """
        return self.request("call", sdk=sdk, function=function, args=args)
