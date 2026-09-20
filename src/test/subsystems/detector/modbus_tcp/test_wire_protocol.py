"""Wire assertions use independent struct/hex frames, not the device encoder."""
import socket
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest

pytestmark = pytest.mark.integration
from test.helpers import service_process


def frame(function=4, address=0, quantity=3, transaction=1, unit=1, data=None):
    pdu = bytes([function]) + (struct.pack("!HH", address, quantity) if data is None else data)
    return struct.pack("!HHHB", transaction, 0, len(pdu) + 1, unit) + pdu


def receive(sock):
    def exact(n):
        result = b""
        while len(result) < n:
            part = sock.recv(n - len(result))
            assert part, "Unexpected EOF"
            result += part
        return result
    header = exact(7)
    length = struct.unpack("!H", header[4:6])[0]
    return header + exact(length - 1)


@pytest.fixture
def tcp(device):
    return device.subsystems["detector"].hardware["modbus_tcp"].client


def exchange(client, request):
    with socket.create_connection(client.endpoint, timeout=3) as sock:
        sock.sendall(request)
        return receive(sock)


def test_four_functions_golden_frames(tcp):
    pairs = [
        ("123400000006110300000002", "12340000000711030400010064"),
        ("000200000006000400000003", "000200000009000406000000010064"),
        ("000300000006ff060001abcd", "000300000006ff060001abcd"),
        ("00040000000b011000000002040000012c", "000400000006011000000002"),
        ("000500000006010300000002", "0005000000070103040000012c"),
        ("000600000006010400000003", "000600000009010406000000000000"),
    ]
    for request, response in pairs:
        assert exchange(tcp, bytes.fromhex(request)) == bytes.fromhex(response)


@pytest.mark.parametrize("payload,code", [
    (frame(1), 1), (frame(3, 2, 1), 2), (frame(4, 2, 2), 2),
    (frame(3, 0, 0), 3), (frame(3, 0, 126), 3), (frame(3, 0, 125), 2),
    (frame(6, 0, 2), 3), (frame(6, data=b""), 3),
    (frame(16, data=bytes.fromhex("000000020400020063")), 3),
    (frame(16, data=bytes.fromhex("00000002020001")), 3),
    (frame(16, data=bytes.fromhex("0000007c")), 3),
    (frame(4, data=bytes.fromhex("0000000300")), 3),
])
def test_natural_exceptions_do_not_mutate_or_consume(tcp, payload, code):
    target = tcp.responses["03:0:2"]
    target.set_sequence([{"registers": [7, 8]}])
    response = exchange(tcp, payload)
    assert response[-2:] == bytes([payload[7] | 128, code])
    assert target.snapshot()["remaining"] == 1
    assert exchange(tcp, frame(4))[-6:] == bytes.fromhex("000000010064")
    assert exchange(tcp, frame(3, 0, 2))[-4:] == bytes.fromhex("00070008")
    assert exchange(tcp, frame(3, 0, 2))[-4:] == bytes.fromhex("00010064")


def test_write_executes_before_exception_override(tcp):
    for function, data in [(6, bytes.fromhex("0001012c")),
                           (16, bytes.fromhex("000000020400000190"))]:
        target = "06:1:1" if function == 6 else "16:0:2"
        tcp.responses[target].set_sequence([{"exception": 4}])
        assert exchange(tcp, frame(function, data=data))[-2:] == bytes([function | 128, 4])
    assert exchange(tcp, frame(3, 0, 2))[-4:] == bytes.fromhex("00000190")


def test_split_coalesced_requests_and_sequences(tcp):
    tcp.responses["04:0:3"].set_sequence([{"registers": [7, 0, 99]}, {"exception": 4}])
    with socket.create_connection(tcp.endpoint, timeout=3) as sock:
        first = frame(transaction=65535)
        for part in (first[:2], first[2:7], first[7:] + frame(transaction=0)):
            sock.sendall(part)
        assert receive(sock) == bytes.fromhex("ffff00000009010406000700000063")
        assert receive(sock) == bytes.fromhex("000000000003018404")
    assert exchange(tcp, frame())[-6:] == bytes.fromhex("000000010064")
    assert tcp.responses["04:0:3"].snapshot()["calls"] == 3


def test_bad_sequence_preserves_pending_values(tcp):
    target = tcp.responses["04:0:3"]
    target.set_sequence([{"exception": 4}])
    for patch in ({"registers": [1]}, {"registers": [0, True, 1]},
                  {"registers": [0, 1, 65536]}, {"exception": 5}, {"address": 0}):
        with pytest.raises(ValueError):
            target.set_sequence([patch])
    for name in ("03:2:1", "06:0:2", "3:0:1", "04:0:0"):
        with pytest.raises(ValueError):
            tcp.responses[name].set_sequence([{}])
    with pytest.raises(ValueError):
        tcp.responses["06:1:1"].set_sequence([{"value": 5}])
    assert target.snapshot()["values"] == [{"exception": 4}]


@pytest.mark.parametrize("header", ["000100010006", "000100000001", "0001000000ff"])
def test_invalid_mbap_closes_connection_without_consumption(tcp, header):
    tcp.responses["04:0:3"].set_sequence([{"exception": 4}])
    with socket.create_connection(tcp.endpoint, timeout=3) as sock:
        sock.sendall(bytes.fromhex(header))
        assert sock.recv(1) == b""
    assert tcp.responses["04:0:3"].snapshot()["remaining"] == 1
    assert any(e["event"] == "protocol_error" for e in tcp.diagnostics()["events"])
    assert tcp.check_health()


def test_concurrent_clients_consume_complete_responses(tcp):
    tcp.responses["04:0:3"].set_sequence([{"registers": [i, i, i]} for i in range(32)])
    def query(i):
        response = exchange(tcp, frame(transaction=i))
        assert struct.unpack("!H", response[:2])[0] == i
        return struct.unpack("!HHH", response[-6:])
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sorted(pool.map(query, range(32))) == [(i, i, i) for i in range(32)]


def test_reset_discards_partial_frames_and_restores_registers(tcp):
    exchange(tcp, frame(6, 1, 300))
    tcp.responses["04:0:3"].set_sequence([{"exception": 4}])
    with socket.create_connection(tcp.endpoint, timeout=3) as sock:
        sock.sendall(frame()[:8])
        exchange(tcp, frame(3, 0, 2))
        tcp.reset()
        assert sock.recv(1) == b""
    assert tcp.responses.snapshot() == {}
    assert exchange(tcp, frame())[-6:] == bytes.fromhex("000000010064")


def test_two_processes_have_independent_registers_and_reset():
    with service_process("tcp") as first, service_process("tcp") as second:
        assert first.process.pid != second.process.pid
        exchange(first.client, frame(6, 1, 300))
        exchange(second.client, frame(6, 1, 400))
        first.client.reset()
        assert exchange(first.client, frame(3, 1, 1))[-2:] == bytes.fromhex("0064")
        assert exchange(second.client, frame(3, 1, 1))[-2:] == bytes.fromhex("0190")


def test_tcp_process_releases_port_and_preserves_unrelated_listener():
    process = service_process("tcp").start()
    endpoint = process.client.endpoint
    process.stop()
    with socket.socket() as existing:
        existing.bind(endpoint)
        existing.listen()
        with pytest.raises(RuntimeError):
            service_process("tcp", port=endpoint[1]).start()
        with socket.create_connection(endpoint, timeout=2):
            pass


def test_shutdown_nonzero_exit_is_an_environment_error():
    process = service_process("tcp")
    process.process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.readline(); sys.exit(7)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        process.stop()
    assert process.process is None


def test_invalid_write_does_not_consume_matching_sequence(tcp):
    target = tcp.responses["06:0:1"]
    target.set_sequence([{"exception": 4}])
    assert exchange(tcp, frame(6, 0, 2))[-2:] == bytes.fromhex("8603")
    assert target.snapshot()["remaining"] == 1
    assert exchange(tcp, frame(6, 0, 0))[-2:] == bytes.fromhex("8604")
    assert target.snapshot()["remaining"] == 0
    assert exchange(tcp, frame(3, 0, 1))[-2:] == b"\x00\x00"


def test_maximum_legal_write_frame_has_address_exception(tcp):
    data = struct.pack("!HHB", 0, 123, 246) + b"\x00\x01" * 123
    assert len(frame(16, data=data)) == 259
    assert exchange(tcp, frame(16, data=data))[-2:] == bytes.fromhex("9002")
