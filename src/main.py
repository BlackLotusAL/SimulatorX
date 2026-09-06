"""Run one local simulator directly from the source tree."""
import argparse
import importlib


ENTRY_POINTS = {
    "plc": "local_service.plc.service",
    "sdk": "local_service.sdk.service",
    "tcp": "local_service.tcp.service",
    "build-sdk": "local_service.sdk.build",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX local simulation services")
    parser.add_argument("component", choices=ENTRY_POINTS, help="Service to run, or build-sdk to compile the .so")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return importlib.import_module(ENTRY_POINTS[args.component]).main(args.arguments)


if __name__ == "__main__":
    main()
