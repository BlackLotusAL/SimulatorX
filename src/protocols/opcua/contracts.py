"""Node-backed behavior; OPC UA remains the sole business state store."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class NodeAccess:
    read: Callable
    write: Callable
    keys: frozenset


class NodeBehavior(ABC):
    tick_interval = 0.1

    @abstractmethod
    def step(self, dt):
        """Advance from current nodes under the host's state lock."""


@dataclass(frozen=True)
class OPCUADefinition:
    model_factory: Callable
    namespace_uri: str
    object_id: str
    reset_id: str
    server_name: str
