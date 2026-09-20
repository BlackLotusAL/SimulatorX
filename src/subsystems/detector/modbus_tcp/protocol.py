"""Modbus TCP wire mapping; addresses are zero-based, without a CRC."""
import struct
from protocols.tcp.contracts import Protocol, Request


def target_parts(target):
    try:
        function, address, quantity = map(int, target.split(":"))
    except (AttributeError, ValueError):
        raise ValueError("Expected FC:address:quantity sequence target")
    size = 3 if function == 4 else 2
    if (function not in (3, 4, 6, 16) or address < 0 or quantity < 1
            or address + quantity > size or (function == 6 and quantity != 1)
            or target != f"{function:02}:{address}:{quantity}"):
        raise ValueError("Unsupported register sequence target")
    return function, address, quantity


class ModbusProtocol(Protocol):
    def decode_request(self, buffer):
        if len(buffer) < 6:
            return None
        transaction, protocol, length = struct.unpack("!HHH", buffer[:6])
        if protocol != 0 or not 2 <= length <= 254:
            raise ValueError("Invalid MBAP protocol identifier or length")
        if len(buffer) < 6 + length:
            return None
        unit, function = buffer[6:8]
        data = bytes(buffer[8:6 + length])
        return Request(str(function), transaction,
                       {"unit": unit, "function": function, "data": data}), 6 + length

    def validate_command(self, command):
        target_parts(command)

    def sequence_target(self, request, response):
        if response.get("exception", 0):
            return None
        f = request.fields
        address = struct.unpack("!H", f["data"][:2])[0]
        quantity = 1 if f["function"] == 6 else struct.unpack("!H", f["data"][2:4])[0]
        return f"{f['function']:02}:{address}:{quantity}"

    def validate_fields(self, command, fields):
        function, _, quantity = target_parts(command)
        expected = {"exception", "registers"} if function in (3, 4) else {"exception"}
        if set(fields) != expected:
            raise ValueError("Unknown response fields")
        exception = fields["exception"]
        if type(exception) is not int or exception not in (0, 1, 2, 3, 4):
            raise ValueError("Exception must be 0, 1, 2, 3 or 4")
        if function in (3, 4):
            values = fields["registers"]
            if (not isinstance(values, list) or len(values) != quantity
                    or any(type(v) is not int or not 0 <= v <= 65535 for v in values)):
                raise ValueError("Expected one uint16 register per requested address")

    def encode_response(self, request, fields):
        function = request.fields["function"]
        if fields.get("exception", 0):
            pdu = bytes([function | 0x80, fields["exception"]])
        elif function in (3, 4):
            values = fields["registers"]
            pdu = bytes([function, 2 * len(values)]) + struct.pack("!" + "H" * len(values), *values)
        else:
            pdu = bytes([function]) + request.fields["data"][:4]
        return struct.pack("!HHHB", request.request_id, 0, len(pdu) + 1,
                           request.fields["unit"]) + pdu
