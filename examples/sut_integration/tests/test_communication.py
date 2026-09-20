import socket
import threading
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.integration

from subsystems.detector.modbus_tcp.protocol import ModbusProtocol
from subsystems.detector.modbus_tcp.model import DetectorRegisters
from reference_sut import DetectorSUT
from integration_support import terminal


@contextmanager
def responder(reply):
    """A small business-protocol peer, with no simulator control connection."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(3)
    calls, errors = [], []

    def serve():
        try:
            with listener.accept()[0] as client:
                client.settimeout(3)
                buffer = bytearray()
                codec = ModbusProtocol()
                while True:
                    data = client.recv(65536)
                    if not data:
                        return
                    buffer.extend(data)
                    decoded = codec.decode_request(buffer)
                    if decoded:
                        request, used = decoded
                        del buffer[:used]
                        calls.append(request.command)
                        response = reply(codec, request)
                        if response is None:
                            return
                        client.sendall(response)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname(), calls
    finally:
        listener.close()
        thread.join(4)
        assert not thread.is_alive()
        assert not errors, errors


def test_status_queries_do_not_consume_device_responses():
    entered, release = threading.Event(), threading.Event()

    def reply(codec, request):
        if request.fields["data"][:2] == b"\x00\x02":
            entered.set()
            assert release.wait(2)
        return codec.encode_response(request, DetectorRegisters().respond(request))

    with responder(reply) as (endpoint, calls):
        sut = DetectorSUT(endpoint, io_timeout=3)
        try:
            identity = sut.start_check()
            assert entered.wait(2)
            for _ in range(100):
                status = sut.get_status(identity)
                assert status["state"] == "running"
                assert status["observed"]["ready"] is True
            assert calls == ["4", "4"]
            release.set()
            assert terminal(sut, identity)["state"] == "succeeded"
            for _ in range(100):
                sut.get_status(identity)
            assert calls == ["4", "4"]
        finally:
            release.set()
            sut.close()


@pytest.mark.parametrize("mode,expected", [
    ("correlation", "protocol_error"), ("byte_count", "protocol_error"),
    ("disconnect", "communication_error"),
    ("unit", "protocol_error"), ("protocol", "protocol_error"),
    ("function", "protocol_error"), ("length", "protocol_error"),
])
def test_bad_device_response_is_not_success(mode, expected):
    def reply(codec, request):
        if mode == "disconnect":
            return None
        if mode == "correlation":
            request.request_id += 1
        frame = codec.encode_response(request, DetectorRegisters().respond(request))
        if mode == "byte_count":
            frame = frame[:8] + bytes([frame[8] ^ 1]) + frame[9:]
        if mode == "unit":
            frame = frame[:6] + b"\x02" + frame[7:]
        if mode == "protocol":
            frame = frame[:2] + b"\x00\x01" + frame[4:]
        if mode == "function":
            frame = frame[:7] + b"\x03" + frame[8:]
        if mode == "length":
            frame = frame[:4] + b"\x00\xff" + frame[6:]
        return frame

    with responder(reply) as (endpoint, calls):
        sut = DetectorSUT(endpoint)
        try:
            status = terminal(sut, sut.start_check())
            assert status["state"] == "failed"
            assert status["error_code"] == expected
            assert calls == ["4"]
        finally:
            sut.close()
