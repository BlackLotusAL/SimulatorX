"""Independent reference detector behavior, not a vendor register map."""
import struct
from protocols.tcp.contracts import ResponseModel, TCPDefinition
from .protocol import target_parts


class DetectorRegisters(ResponseModel):
    def __init__(self):
        self.reset()

    def reset(self):
        self.holding = [1, 100]

    def baseline(self, command):
        function, _, quantity = target_parts(command)
        result = {"exception": 0}
        if function in (3, 4):
            result["registers"] = [0] * quantity
        return result

    def respond(self, request):
        function, data = request.fields["function"], request.fields["data"]
        if function not in (3, 4, 6, 16):
            return {"exception": 1}
        if len(data) < 4:
            return {"exception": 3}
        address, value = struct.unpack("!HH", data[:4])
        quantity = 1 if function == 6 else value
        limit = 123 if function == 16 else 125
        if not 1 <= quantity <= limit:
            return {"exception": 3}
        if function == 16:
            if len(data) != 5 + quantity * 2 or data[4] != quantity * 2:
                return {"exception": 3}
            values = list(struct.unpack("!" + "H" * quantity, data[5:]))
        else:
            if len(data) != 4:
                return {"exception": 3}
            values = [value]
        size = 3 if function == 4 else 2
        if address + quantity > size:
            return {"exception": 2}
        if function in (6, 16):
            if address == 0 and values[0] not in (0, 1):
                return {"exception": 3}
            self.holding[address:address + quantity] = values
            return {"exception": 0}
        registers = self.holding if function == 3 else [0, self.holding[0],
                        self.holding[1] if self.holding[0] else 0]
        return {"exception": 0, "registers": list(registers[address:address + quantity])}


def create():
    from .protocol import ModbusProtocol
    return TCPDefinition(ModbusProtocol, DetectorRegisters)
