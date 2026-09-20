import time


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Condition did not become true before deadline")


from framework.config import HardwareConfig, load_protocol
from framework.source import SOURCE_ROOT


def service_process(kind, *, port=0):
    protocol, device = {
        "sdk": ("sdk", "subsystems.motion.rotary_axis"),
        "tcp": ("tcp", "subsystems.detector.modbus_tcp"),
        "opcua": ("opcua", "subsystems.vacuum.chamber_plc"),
    }[kind]
    directory = SOURCE_ROOT.joinpath(*device.split("."), "resources")
    settings = {"nodeset": "vacuum.xml", "port": port} if kind == "opcua" else {"port": port}
    if kind == "sdk":
        settings = {}
    config = HardwareConfig("test/" + device.removeprefix("subsystems.").replace(".", "/"), protocol, settings, directory)
    hardware = load_protocol(config.type).create(config)
    hardware.validate()
    return hardware.create_process()


def opcua_process(port=0):
    return service_process("opcua", port=port)


def build_opcua_service(**options):
    from protocols.opcua.service import OPCUAService
    from subsystems.vacuum.chamber_plc.model import create, resource
    return OPCUAService(create(), resource("vacuum.xml"), resource("bindings.json"), **options)


def sdk_service(control_socket, sdk_socket, **options):
    from protocols.sdk.service import SDKService
    from subsystems.motion.rotary_axis.model import create
    spec = create()
    return SDKService(control_socket, sdk_socket, spec.model_factory(), spec.adapter_factory(), **options)
