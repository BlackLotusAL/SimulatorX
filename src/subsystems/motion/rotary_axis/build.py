"""Compile the reference ABI with Linux cc or Windows MinGW-w64 GCC."""
import argparse
import shutil
import subprocess
import sys
import struct
from pathlib import Path
from framework.source import hidden_process_options


def build_reference_sdk(output_directory, compiler=None):
    if sys.platform not in ("linux", "win32"):
        raise RuntimeError("The reference SDK requires Windows x64 or Linux")
    windows = sys.platform == "win32"
    if windows and struct.calcsize("P") != 8:
        raise RuntimeError("Windows SDK requires a 64-bit Python process")
    compiler = compiler or (shutil.which("gcc") if windows else shutil.which("cc") or shutil.which("gcc"))
    if not compiler:
        raise RuntimeError("MinGW-w64 GCC is required" if windows else "A Linux C compiler is required")
    if windows:
        target = subprocess.check_output([compiler, "-dumpmachine"], text=True, timeout=10,
                                         **hidden_process_options()).strip()
        if target != "x86_64-w64-mingw32":
            raise RuntimeError("Windows SDK requires x86_64-w64-mingw32 GCC")
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "native" / "simulatorx_sdk.c"
    library = directory / ("simulatorx_sdk.dll" if windows else "libsimulatorx_sdk.so")
    flags = (["-lws2_32", "-static-libgcc"] if windows else
             ["-fPIC", "-fvisibility=hidden", "-Wl,-soname,libsimulatorx_sdk.so"])
    subprocess.run([compiler, "-std=c11", "-O2", "-shared", "-Wall", "-Wextra", "-Werror",
                    str(source), "-o", str(library), *flags], check=True, timeout=60,
                   **hidden_process_options())
    return library


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the reference SimulatorX Windows/Linux SDK")
    parser.add_argument("--output", required=True)
    parser.add_argument("--compiler")
    args = parser.parse_args(argv)
    print(build_reference_sdk(args.output, args.compiler))


if __name__ == "__main__":
    main()
