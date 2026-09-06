"""Demo business adapters extend the external reference SUT, never the model."""
import ctypes
from sut_integration.reference_sut.motion import MotionSUT, AxisState


class DemoMotionSUT(MotionSUT):
    def start_command(self, action, target=30, speed=90):
        def execute():
            names = {"enable": "Enable", "disable": "Disable", "stop": "Stop", "clear": "ClearFault"}
            if action in names:
                self._call(names[action])
            else:
                completed = False
                try:
                    if action == "home":
                        self._call("Home", speed)
                        destination = 0
                    else:
                        state = AxisState()
                        self._call("GetState", ctypes.byref(state))
                        destination = state.position_deg + target if action == "relative" else target
                        self._call("MoveRelative" if action == "relative" else "MoveAbsolute", target, speed)
                    self._wait_position(destination, action)
                    completed = True
                finally:
                    if not completed:
                        self._call("Stop")
        return self._start(execute)


def create_sut(descriptor):
    return DemoMotionSUT(descriptor["library"], timeout=4)


def start_manual(sut, descriptor):
    return sut.start_command(descriptor["action"], descriptor.get("target", 30), descriptor.get("speed", 90))
