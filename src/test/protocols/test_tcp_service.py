import socket

import pytest


@pytest.mark.integration
def test_tcp_host_accepts_independent_protocol_and_behavior():
    from protocols.tcp.contracts import Protocol, Request, ResponseModel
    from protocols.tcp.service import TCPService

    class ByteProtocol(Protocol):
        def decode_request(self, buffer):
            return (Request("byte", buffer[0], {}), 1) if buffer else None

        def validate_command(self, target):
            if target != "byte":
                raise ValueError("Unknown byte target")

        def validate_fields(self, target, fields):
            if type(fields.get("value")) is not int or not 0 <= fields["value"] <= 255:
                raise ValueError("Expected a byte")

        def encode_response(self, request, fields):
            return bytes([fields["value"]])

    class ByteModel(ResponseModel):
        def baseline(self, command):
            return {"value": 42}

        def respond(self, request):
            return self.baseline(request.command)

        def reset(self):
            pass

    service = TCPService(ByteProtocol(), ByteModel()).start()
    try:
        service.dispatch({"op": "set_sequence", "target": "byte", "values": [{"value": 7}]})
        with socket.create_connection(service.device.address, timeout=3) as sock:
            sock.sendall(b"\x00\x01")
            data = sock.recv(2)
            while len(data) < 2:
                data += sock.recv(2 - len(data))
            assert data == b"\x07\x2a"
    finally:
        service.stop()


def test_tcp_contract_requires_explicit_device_response():
    from protocols.tcp.contracts import ResponseModel

    class Incomplete(ResponseModel):
        def baseline(self, command):
            return {}

        def reset(self):
            pass

    with pytest.raises(TypeError, match="respond"):
        Incomplete()


def test_tcp_has_only_one_listener(monkeypatch):
    from protocols.tcp import service
    from subsystems.detector.modbus_tcp.model import create
    listener = service.Listener
    addresses = []

    def record(address, handler):
        addresses.append(address)
        return listener(address, handler)

    monkeypatch.setattr(service, "Listener", record)
    spec = create()
    simulator = service.TCPService(spec.protocol_factory(), spec.model_factory())
    assert addresses == [("127.0.0.1", 0)]
    assert not hasattr(simulator, "control")
