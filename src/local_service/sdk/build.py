"""Compile the reference Linux ABI; no vendor compatibility is inferred."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def build_reference_sdk(output_directory, compiler=None):
    if sys.platform != "linux":
        raise RuntimeError("The reference .so must be built on Linux")
    compiler = compiler or shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise RuntimeError("A Linux C compiler is required")
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "native" / "simulatorx_sdk.c"
    library = directory / "libsimulatorx_sdk.so"
    subprocess.run([compiler, "-std=c11", "-O2", "-fPIC", "-fvisibility=hidden", "-shared",
                    "-Wall", "-Wextra", "-Werror", "-Wl,-soname,libsimulatorx_sdk.so",
                    str(source), "-o", str(library)], check=True, timeout=60)
    return library


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the reference SimulatorX Linux SDK")
    parser.add_argument("--output", required=True)
    parser.add_argument("--compiler")
    args = parser.parse_args(argv)
    print(build_reference_sdk(args.output, args.compiler))


if __name__ == "__main__":
    main()
