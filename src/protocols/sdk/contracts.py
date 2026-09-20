"""SDK hosting contracts; native ABI and device semantics remain device-local."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


class SDKModel(ABC):
    @property
    @abstractmethod
    def tick_interval(self):
        pass

    @abstractmethod
    def advance(self, dt):
        pass

    @abstractmethod
    def validate(self, function, args):
        pass

    @abstractmethod
    def execute(self, function, args):
        pass

    @abstractmethod
    def snapshot(self):
        pass

    @abstractmethod
    def reset(self):
        pass

    @abstractmethod
    def set_fault(self, name, active):
        pass


class SDKAdapter(ABC):
    @abstractmethod
    def target(self, target):
        """Validate SDK.Function and return its two parts."""

    @abstractmethod
    def validate_returns(self, values):
        pass

    @abstractmethod
    def handle(self, sock, invoke):
        """Decode a native request, invoke the model, and encode its result."""

    @abstractmethod
    def info(self, model):
        """Describe this device and its supported functions."""


@dataclass(frozen=True)
class SDKDefinition:
    model_factory: Callable
    adapter_factory: Callable
    build_sdk: Callable
