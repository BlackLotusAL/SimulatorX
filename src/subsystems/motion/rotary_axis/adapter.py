"""Reference native ABI codec and return-code policy; maintained by the rotary device."""
import struct
from dataclasses import asdict
from protocols.sdk.contracts import SDKAdapter
from framework.transport import receive_exact
from .abi import CONTROL_ERROR, FUNCTIONS

# Reference ABI wire format: network endian; no native structs or pointers on the wire.
REQUEST = struct.Struct("!4sIidd")
RESPONSE = struct.Struct("!4sidddIIIIII")
MAGIC = b"SX01"


class RotaryAdapter(SDKAdapter):
    def target(self, target):
        if not isinstance(target, str) or target.count(".") != 1:
            raise ValueError("Target must be SDK.Function, e.g. rotary.MoveAbsolute")
        sdk, function = target.split(".")
        if sdk != "rotary" or function not in FUNCTIONS:
            raise ValueError("Unknown SDK function target")
        return sdk, function

    def handle(self, sock, invoke):
        magic, opcode, axis, first, second = REQUEST.unpack(receive_exact(sock, REQUEST.size))
        if magic != MAGIC or not 1 <= opcode <= len(FUNCTIONS):
            return
        function = FUNCTIONS[opcode - 1]
        args = {"axis": axis}
        if function in ("MoveAbsolute", "MoveRelative"):
            args.update(angle=first, speed=second)
        elif function == "Home":
            args["speed"] = first
        result = invoke("rotary", function, args)
        state = result["state"]
        flags = sum((1 << i) for i, key in enumerate(
            ("stalled", "positive_limit", "negative_limit")) if state["faults"][key])
        sock.sendall(RESPONSE.pack(MAGIC, result["return_value"], state["position"],
                                   state["velocity"], state["target"], state["enabled"],
                                   state["homed"], state["busy"], state["done"], state["alarm"], flags))

    def validate_returns(self, values):
        if not isinstance(values, list) or len(values) > 10000 or any(
                type(v) is not int or not CONTROL_ERROR < v < 2 ** 31 for v in values):
            raise ValueError("Expected at most 10000 int32 return codes; INT32_MIN is reserved")

    def info(self, model):
        return {"profile": asdict(model.profile), "reference_sdk": "rotary",
                "functions": list(FUNCTIONS)}
