"""One child-process entry point for protocol-specific service factories."""
import argparse
import contextlib
import sys
from pathlib import Path

from .config import HardwareConfig, load_protocol, read_object, PROTOCOL_TYPES
from .runner import serve


def main(argv=None):
    parser = argparse.ArgumentParser(description="SimulatorX protocol service")
    parser.add_argument("--type", required=True, choices=PROTOCOL_TYPES)
    parser.add_argument("--config", required=True)
    parser.add_argument("--ready-file")
    parser.add_argument("--managed", action="store_true")
    parser.add_argument("--pipe-control", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = read_object(args.config)
        config = HardwareConfig(payload["identity"], args.type,
                                payload["settings"], Path(payload["directory"]))
        replies = sys.stdout if args.pipe_control else None
        # Device prints and ready messages remain logs, never pipe responses.
        with contextlib.redirect_stdout(sys.stderr if replies else sys.stdout):
            service = load_protocol(args.type).create_service(config)
            serve(service, args.ready_file, args.managed, replies=replies)
    except Exception as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
