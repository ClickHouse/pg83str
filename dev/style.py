#!/usr/bin/env python3
"""Format the tracked C sources with clang-format and the repository .clang-format."""
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_PATTERNS = ("*.c", "*.h")


def source_files(arguments):
    if arguments:
        return [Path(argument).resolve() for argument in arguments]
    output = subprocess.check_output(["git", "ls-files", "-z", "--", *SOURCE_PATTERNS], cwd=ROOT)
    return [ROOT / name for name in output.decode().split("\0") if name]


def main():
    files = source_files(sys.argv[1:])
    command = shlex.split(os.environ.get("CLANG_FORMAT", "clang-format"))
    subprocess.run([*command, "-i", "--style=file", *map(str, files)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
