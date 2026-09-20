"""Independent Modbus TCP client for the reference detector business workflow."""
import socket
import struct
import time
from .operation import BusinessError, OperationSUT


class DetectorSUT(OperationSUT):
    def __init__(self, endpoint, **options):
        super().__init__(**options)
        self.endpoint = tuple(endpoint)
        self._request_id = 0

    def start_check(self):
        return self._start(self._check)

    def _exchange(self, sock, address, quantity):
        self._checkpoint()
        self._request_id = (self._request_id + 1) % 65536
        deadline = min(self._deadline, time.monotonic() + self.io_timeout)
        sock.settimeout(max(0.001, deadline - time.monotonic()))
        sock.sendall(struct.pack("!HHHBBHH", self._request_id, 0, 6, 1, 4, address, quantity))
        buffer = bytearray()
        while True:
            self._checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Device TCP response timed out")
            sock.settimeout(remaining)
            part = sock.recv(260)
            if not part:
                raise EOFError("Device closed its business connection")
            buffer.extend(part)
            if len(buffer) < 7:
                continue
            transaction, protocol, length, unit = struct.unpack("!HHHB", buffer[:7])
            if transaction != self._request_id or protocol != 0 or unit != 1 or not 2 <= length <= 254:
                raise BusinessError("protocol_error", "Invalid MBAP or response correlation")
            if len(buffer) < length + 6:
                continue
            if len(buffer) != length + 6:
                raise BusinessError("protocol_error", "Unexpected trailing response data")
            pdu = buffer[7:]
            if pdu[0] == 0x84:
                if len(pdu) != 2 or pdu[1] == 0:
                    raise BusinessError("protocol_error", "Malformed exception response")
                raise BusinessError("device_error", "Modbus exception " + str(pdu[1]))
            if pdu[0] != 4 or len(pdu) != 2 + 2 * quantity or pdu[1] != 2 * quantity:
                raise BusinessError("protocol_error", "Unexpected function or register byte count")
            return list(struct.unpack("!" + "H" * quantity, pdu[2:]))

    def _check(self):
        with socket.create_connection(self.endpoint, timeout=self.io_timeout) as sock:
            while True:
                code, ready = self._exchange(sock, 0, 2)
                status = {"status": code, "ready": bool(ready)}
                self._observe(status)
                if code != 0:
                    raise BusinessError("device_error", "Device status is nonzero")
                if ready not in (0, 1):
                    raise BusinessError("protocol_error", "Invalid ready register")
                if ready:
                    measurement, = self._exchange(sock, 2, 1)
                    self._observe(dict(status, measurement=measurement))
                    return
                self._pause()
