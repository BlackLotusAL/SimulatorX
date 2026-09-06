"""Protocol extension contract and an explicitly non-vendor reference protocol."""
import json
import math
import struct
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Request:
    command: str
    request_id: int
    fields: dict


class Protocol(ABC):
    """Instances must be stateless across connections; buffers belong to handlers."""
    max_buffer = 1024 * 1024

    @abstractmethod
    def decode_request(self, buffer):
        """Return (Request, consumed_bytes), or None for an incomplete frame."""

    @abstractmethod
    def baseline(self, command):
        """Return a fresh dict of normal response fields; reject unknown commands."""

    @abstractmethod
    def validate_fields(self, command, fields):
        """Validate a complete response's wire types, not business normality."""

    @abstractmethod
    def encode_response(self, request, fields):
        """Encode correlation, lengths and checksums from the actual request and fields."""


class DemoProtocol(Protocol):
    """uint32 length | UTF-8 JSON payload | uint32 CRC32(payload), network endian.

    This is a reference test protocol, not a claim of compatibility with a device.
    """
    max_payload = 65536
    max_buffer = max_payload + 8 + 65536
    baselines = {"READ_STATUS": {"status": 0, "ready": True},
                 "READ_ANGLE": {"status": 0, "angle_deg": 0.0}}

    def baseline(self, command):
        if not isinstance(command, str) or command not in self.baselines:
            raise ValueError("Unknown reference protocol command")
        return dict(self.baselines[command])

    def validate_fields(self, command, fields):
        baseline = self.baseline(command)
        if not isinstance(fields, dict) or set(fields) != set(baseline):
            raise ValueError("Response fields do not match the command schema")
        if type(fields["status"]) is not int or not -(2 ** 31) <= fields["status"] < 2 ** 31:
            raise ValueError("status must be int32")
        if command == "READ_STATUS" and type(fields["ready"]) is not bool:
            raise ValueError("ready must be Boolean")
        if command == "READ_ANGLE":
            try:
                valid = type(fields["angle_deg"]) in (int, float) and math.isfinite(fields["angle_deg"])
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("angle_deg must be finite")

    def _encode(self, message):
        payload = json.dumps(message, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if not 0 < len(payload) <= self.max_payload:
            raise ValueError("Reference frame payload is too large")
        return struct.pack("!I", len(payload)) + payload + struct.pack("!I", zlib.crc32(payload))

    def _decode(self, buffer):
        if len(buffer) < 4:
            return None
        length = struct.unpack_from("!I", buffer)[0]
        if not 0 < length <= self.max_payload:
            raise ValueError("Invalid reference frame length")
        if len(buffer) < length + 8:
            return None
        payload = bytes(buffer[4:4 + length])
        checksum = struct.unpack_from("!I", buffer, 4 + length)[0]
        if checksum != zlib.crc32(payload):
            raise ValueError("Reference frame CRC mismatch")
        value = json.loads(payload)
        if not isinstance(value, dict) or set(value) != {"kind", "command", "request_id", "fields"}:
            raise ValueError("Invalid reference message")
        if type(value["request_id"]) is not int or not 0 <= value["request_id"] < 2 ** 32:
            raise ValueError("request_id must be uint32")
        self.baseline(value["command"])
        if not isinstance(value["fields"], dict):
            raise ValueError("fields must be an object")
        return value, length + 8

    def encode_request(self, command, request_id, fields=None):
        self.baseline(command)
        if type(request_id) is not int or not 0 <= request_id < 2 ** 32:
            raise ValueError("request_id must be uint32")
        if fields not in (None, {}):
            raise ValueError("Reference requests have no data fields")
        return self._encode({"kind": "request", "command": command,
                             "request_id": request_id, "fields": {}})

    def decode_request(self, buffer):
        decoded = self._decode(buffer)
        if decoded is None:
            return None
        value, used = decoded
        if value["kind"] != "request" or value["fields"]:
            raise ValueError("Expected a reference request with empty fields")
        return Request(value["command"], value["request_id"], value["fields"]), used

    def encode_response(self, request, fields):
        self.validate_fields(request.command, fields)
        return self._encode({"kind": "response", "command": request.command,
                             "request_id": request.request_id, "fields": fields})

    def decode_response(self, buffer):
        decoded = self._decode(buffer)
        if decoded is not None:
            value, used = decoded
            if value["kind"] != "response":
                raise ValueError("Expected a reference response")
            self.validate_fields(value["command"], value["fields"])
            return value, used
        return None
