import socket
import struct
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from local_service.tcp.client import TCPClient
from local_service.process import ServiceProcess
from local_service.tcp.protocol import DemoProtocol, Request
from local_service.tcp.service import TCPService


def receive(sock, codec, count=1):
    buffer = bytearray()
    messages = []
    while len(messages) < count:
        part = sock.recv(65536)
        assert part, "Unexpected EOF"
        buffer.extend(part)
        while True:
            decoded = codec.decode_response(buffer)
            if decoded is None:
                break
            message, used = decoded
            messages.append(message)
            del buffer[:used]
    assert not buffer
    return messages


def test_reference_codec_checks_crc_lengths_and_types():
    codec = DemoProtocol()
    frame = codec.encode_response(Request("READ_ANGLE", 77, {}), {"status": -99, "angle_deg": -720.0})
    length = struct.unpack("!I", frame[:4])[0]
    assert length == len(frame) - 8
    assert zlib.crc32(frame[4:-4]) == struct.unpack("!I", frame[-4:])[0]
    message, used = codec.decode_response(frame)
    assert used == len(frame) and message["request_id"] == 77
    assert message["fields"] == {"status": -99, "angle_deg": -720.0}
    with pytest.raises(ValueError, match="CRC"):
        codec.decode_response(frame[:-1] + bytes([frame[-1] ^ 1]))
    with pytest.raises(ValueError):
        codec.decode_request(struct.pack("!I", 999999))


def test_split_coalesced_requests_baseline_and_reconnection(tcp_service, tcp_responses):
    codec = DemoProtocol()
    tcp_responses["READ_STATUS"].set_sequence([{"status": 7, "ready": False}, {"status": 8}])
    tcp_responses["READ_ANGLE"].set_sequence([{"angle_deg": 12.5}])
    assert tcp_responses["READ_STATUS"].snapshot()["remaining"] == 2
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        first = codec.encode_request("READ_STATUS", 11)
        sock.sendall(first[:2])
        sock.sendall(first[2:7])
        sock.sendall(first[7:] + codec.encode_request("READ_ANGLE", 12))
        replies = receive(sock, codec, 2)
        assert [r["request_id"] for r in replies] == [11, 12]
        assert replies[0]["fields"] == {"status": 7, "ready": False}
        assert replies[1]["fields"] == {"status": 0, "angle_deg": 12.5}
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        sock.sendall(codec.encode_request("READ_STATUS", 13) + codec.encode_request("READ_STATUS", 14))
        replies = receive(sock, codec, 2)
        assert [r["fields"] for r in replies] == [{"status": 8, "ready": True}, {"status": 0, "ready": True}]
    state = tcp_responses["READ_STATUS"].snapshot()
    assert state["remaining"] == 0 and state["calls"] == 3
    tcp_responses["READ_STATUS"].set_sequence([{"ready": False}])
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        sock.sendall(codec.encode_request("READ_STATUS", 15))
        assert receive(sock, codec)[0]["fields"] == {"status": 0, "ready": False}


def test_bad_sequence_does_not_replace_pending_values(tcp_service, tcp_responses):
    target = tcp_responses["READ_STATUS"]
    target.set_sequence([{"status": 7}])
    for patch in ({"ready": 1}, {"length": 1}, {"status": 2 ** 31}):
        with pytest.raises(ValueError):
            target.set_sequence([patch])
    assert target.snapshot()["remaining"] == 1
    assert target.snapshot()["values"] == [{"status": 7}]


def test_malformed_request_does_not_consume_sequence(tcp_service, tcp_responses):
    codec = DemoProtocol()
    tcp_responses["READ_STATUS"].set_sequence([{"status": 7}])
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        frame = bytearray(codec.encode_request("READ_STATUS", 1))
        frame[-1] ^= 1
        sock.sendall(frame)
        assert sock.recv(1) == b""
    assert tcp_responses["READ_STATUS"].snapshot()["remaining"] == 1
    assert tcp_service.check_health()


def test_concurrent_clients_consume_whole_field_groups_once(tcp_service, tcp_responses):
    codec = DemoProtocol()
    tcp_responses["READ_STATUS"].set_sequence([
        {"status": index, "ready": index % 2 == 0} for index in range(1, 33)])
    endpoint = tcp_service.endpoint

    def query(identifier):
        with socket.create_connection(endpoint, timeout=3) as sock:
            sock.sendall(codec.encode_request("READ_STATUS", identifier))
            response = receive(sock, codec)[0]
            assert response["request_id"] == identifier
            return response["fields"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        fields = list(pool.map(query, range(32)))
    assert sorted(item["status"] for item in fields) == list(range(1, 33))
    assert all(item["ready"] == (item["status"] % 2 == 0) for item in fields)
    assert query(100) == {"status": 0, "ready": True}


def test_reset_discards_partial_requests_and_restores_baseline(tcp_service, tcp_responses):
    codec = DemoProtocol()
    tcp_responses["READ_STATUS"].set_sequence([{"status": 7}])
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        sock.sendall(codec.encode_request("READ_STATUS", 1)[:8])
        # Complete another call so the server has accepted the test connections.
        with socket.create_connection(tcp_service.endpoint, timeout=2) as other:
            other.sendall(codec.encode_request("READ_STATUS", 2))
            receive(other, codec)
        tcp_service.reset()
        assert sock.recv(1) == b""
    assert tcp_responses.snapshot() == {}
    with socket.create_connection(tcp_service.endpoint, timeout=2) as sock:
        sock.sendall(codec.encode_request("READ_STATUS", 3))
        assert receive(sock, codec)[0]["fields"]["status"] == 0


def test_protocol_adapter_can_be_replaced_without_sdk():
    class OtherProtocol(DemoProtocol):
        baselines = {"READ_STATUS": {"status": 12, "ready": False}}

    service = TCPService(protocol=OtherProtocol()).start()
    try:
        control = TCPClient(service.control.address)
        with socket.create_connection(control.endpoint, timeout=2) as sock:
            sock.sendall(OtherProtocol().encode_request("READ_STATUS", 1))
            assert receive(sock, OtherProtocol())[0]["fields"] == {"status": 12, "ready": False}
        assert control.check_health()
    finally:
        service.stop()


def test_owned_tcp_process_releases_port_and_preserves_unrelated_listener():
    process = ServiceProcess("tcp").start()
    endpoint = process.client.endpoint
    process.stop()
    with socket.socket() as existing:
        existing.bind(endpoint)
        existing.listen()
        with pytest.raises(RuntimeError):
            ServiceProcess("tcp", port=endpoint[1]).start()
        with socket.create_connection(endpoint, timeout=2):
            pass


def test_shutdown_nonzero_exit_is_an_environment_error():
    process = ServiceProcess("tcp")
    process.process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.readline(); sys.exit(7)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        process.stop()
    assert process.process is None
