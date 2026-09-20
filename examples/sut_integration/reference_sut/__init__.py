"""External example application; not part of the SimulatorX public API."""
from .vacuum import VacuumSUT
from .motion import MotionSUT
from .detector import DetectorSUT

__all__ = ["VacuumSUT", "MotionSUT", "DetectorSUT"]
