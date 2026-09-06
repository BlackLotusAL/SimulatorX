"""Run a configured machine or selected hardware directly from source."""
import argparse
import json
import threading
import sys
from pathlib import Path

from framework.runtime import DeviceRuntime
from framework.config import DEFAULT_DEVICE



def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX equipment simulation")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run the device or selected hardware")
    run.add_argument("--device", default=str(DEFAULT_DEVICE))
    run.add_argument("--select", action="append", help="subsystem/hardware; repeat to select multiple")
    run.add_argument("--ready-file")
    run.add_argument("--managed", action="store_true")
    build = commands.add_parser("build-sdk")
    build.add_argument("--device", default=str(DEFAULT_DEVICE))
    build.add_argument("--hardware", required=True)
    build.add_argument("--output", required=True)
    commands.add_parser("demo", help="Run the optional PLC / SDK / TCP browser demo")
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "demo":
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
        from demo.web.app import main as demo_main
        return demo_main(args_list[1:])
    args = parser.parse_args(args_list)
    try:
        if args.command == "build-sdk":
            device = DeviceRuntime.from_config(args.device, [args.hardware])
            hardware = next(device.hardware())
            if not callable(getattr(hardware, "build_sdk", None)):
                raise ValueError("Selected hardware does not provide a native SDK")
            print(hardware.build_sdk(args.output))
            return
        with DeviceRuntime.from_config(args.device, args.select) as device:
            info = {"device": device.id, "hardware": {h.identity: h.endpoints for h in device.hardware()}}
            if args.ready_file:
                path = Path(args.ready_file)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text(json.dumps(info), encoding="utf-8")
                temporary.replace(path)
            print(json.dumps({"event": "ready", **info}), flush=True)
            stopped = threading.Event()
            if args.managed:
                def watch_parent():
                    sys.stdin.readline()
                    stopped.set()
                threading.Thread(target=watch_parent, daemon=True).start()
            try:
                while not stopped.wait(0.2):
                    device.check_health()
            except KeyboardInterrupt:
                pass
    except Exception as exc:
        parser.exit(1, "Simulation failed: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
