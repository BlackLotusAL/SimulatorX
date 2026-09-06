import argparse
import json
import logging
import sys
import threading
from pathlib import Path

from opcua import Server, ua
from .bindings import load_bindings, resource
from .simulator import VacuumSimulator


def build_simulator(opcua_port=4840, nodesets=None, bindings_path=None, profile=None):
    server = Server()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{opcua_port}/simulatorx/")
    server.set_server_name("SimulatorX Vacuum PLC")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    for nodeset in nodesets or [resource("vacuum.xml")]:
        server.import_xml(str(nodeset))
    options = json.loads(Path(profile).read_text(encoding="utf-8")) if profile else {}
    return VacuumSimulator(server, load_bindings(bindings_path), **options)


def serve(simulator, ready_file=None, managed=False):
    stopped = threading.Event()
    try:
        simulator.start()
        info = {"opcua_endpoint": simulator.endpoint}
        if ready_file:
            path = Path(ready_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(info), encoding="utf-8")
            temporary.replace(path)
        print(json.dumps({"event": "ready", **info}), flush=True)
        if managed:
            def watch_parent():
                sys.stdin.readline()
                stopped.set()
            threading.Thread(target=watch_parent, daemon=True).start()
        while not stopped.wait(0.1):
            if not simulator.ready:
                raise RuntimeError(simulator.failed_reason or "OPC UA service stopped")
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX: native OPC UA node testing")
    parser.add_argument("--opcua-port", type=int, default=4840)
    parser.add_argument("--nodeset", action="append", help="XML files, in dependency order")
    parser.add_argument("--bindings", help="JSON node mapping")
    parser.add_argument("--profile", help="JSON simulation settings")
    parser.add_argument("--fast", action="store_true", help="Fast chamber dynamics for tests")
    parser.add_argument("--ready-file", help="Write actual endpoint after startup")
    parser.add_argument("--managed", action="store_true", help="Stop on parent stdin command/EOF")
    args = parser.parse_args(argv)
    if not 0 <= args.opcua_port <= 65535:
        parser.error("Port must be in 0..65535; 0 requests an OS-assigned port")
    logging.basicConfig(level=logging.WARNING)
    try:
        simulator = build_simulator(args.opcua_port, args.nodeset, args.bindings,
                                    args.profile or (resource("fast.json") if args.fast else None))
        serve(simulator, args.ready_file, args.managed)
    except Exception as exc:
        parser.exit(1, f"Simulator startup failed: {exc}\n")


if __name__ == "__main__":
    main()
