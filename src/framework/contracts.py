"""Hardware lifecycle contract; factories must construct without starting resources."""
from abc import ABC, abstractmethod


class Hardware(ABC):
    def __init__(self, config):
        self.config = config
        self.identity = config.identity
        self.client = None
        self.endpoints = {}

    @abstractmethod
    def validate(self):
        """Check configuration, resources and platform without starting a service."""

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        """Release connections and service resources; safe after partial startup."""

    @abstractmethod
    def reset(self):
        pass

    @abstractmethod
    def check_health(self):
        pass

    def diagnostics(self):
        return {"identity": self.identity, "endpoints": self.endpoints}


class ProcessHardware(Hardware):
    """Shared ownership, health and Reset rules for service-backed clients."""
    def __init__(self, config):
        super().__init__(config)
        self.process = None

    def stop(self):
        try:
            if self.process:
                self.process.stop()
        finally:
            self.process = self.client = None
            self.endpoints = {}

    def check_health(self):
        if self.client is None or self.process is None:
            raise RuntimeError("Hardware is not running: " + self.identity)
        return self.process.check_health()

    def reset(self):
        self.check_health()
        return self.client.reset()

    def diagnostics(self):
        result = super().diagnostics()
        if self.client:
            result.update(self.client.diagnostics())
        if self.process:
            result["service_log"] = self.process.log()
        return result
