"""TCP framing and response behavior are independent extension contracts."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class Request:
    command: str
    request_id: int
    fields: dict


class Protocol(ABC):
    max_buffer = 1024 * 1024

    def sequence_target(self, request, response):
        """Return a sequence key, or None when this response must not consume one."""
        return request.command

    @abstractmethod
    def decode_request(self, buffer):
        """Return (Request, consumed_bytes), or None for an incomplete frame."""

    @abstractmethod
    def validate_command(self, command):
        """Validate a sequence target without consuming model state."""

    @abstractmethod
    def validate_fields(self, command, fields):
        """Validate complete response wire types, not business normality."""

    @abstractmethod
    def encode_response(self, request, fields):
        """Encode correlation, lengths and checksums from actual fields."""


class ResponseModel(ABC):
    @abstractmethod
    def baseline(self, command):
        """Return fresh response fields for sequence validation, without side effects."""

    @abstractmethod
    def respond(self, request):
        """Validate and execute device behavior, or return a protocol error response."""

    @abstractmethod
    def reset(self):
        """Restore initial behavior state."""


@dataclass(frozen=True)
class TCPDefinition:
    protocol_factory: Callable
    model_factory: Callable
