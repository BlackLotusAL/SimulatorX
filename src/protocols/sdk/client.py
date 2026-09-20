"""SDK model control client, independent of the native SDK call channel."""
from framework.control import ControlClient


class SDKClient(ControlClient):
    @property
    def launch_environment(self):
        info = self.request("ping")
        endpoint = info["sdk_socket"]
        routing = ({"SIMULATORX_CONTROL_SOCKET": endpoint} if isinstance(endpoint, str) else
                   {"SIMULATORX_SDK_ENDPOINT": "tcp://%s:%s" % tuple(endpoint)})
        return {**routing,
                "SIMULATORX_SDK_ERROR_FILE": info["error_file"],
                "SIMULATORX_CONTROL_TIMEOUT_MS": "1000"}

    def snapshot(self):
        return self.request("snapshot")

    def set_fault(self, name, active):
        return self.request("set_fault", name=name, active=active)

    def call(self, function, *, sdk=None, **args):
        """Reference SDK operation via control RPC, useful for service tests.

        SUT integration tests must call the actual DLL/.so in the SUT process.
        """
        return self.request("call", sdk=sdk or self.request("ping")["reference_sdk"],
                            function=function, args=args)
