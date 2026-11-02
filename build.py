import os
import sys

import build

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev"))
from units import UNITS  # noqa: E402

# -fno-pic: the post passes rewrite the objects and want plain PC32/PLT32
# relocations; the library is meant to be linked into a static libc anyway.
build.cflags += ["-O3", "-fno-builtin", "-fno-stack-protector", "-fomit-frame-pointer", "-fno-pic", "-fno-pie"]

warning_flags = ["-Wall", "-Wextra", "-Werror"]

# One unit per ISA level, each compiled with its own -march; the compiler may
# use anything the level has anywhere in that file.
levels = [
    library(
        name="str_" + unit,
        srcs=[f"$(S)/{unit}.c"],
        cflags=[*march, *warning_flags],
    )
    for unit, march in UNITS.items()
]

# The objects, rewritten by the post passes, in one archive.
libstr = command(
    name="libstr",
    inputs=build.glob("$(S)/dev/*.py"),
    outputs=["$(B)/libstr.a", "$(B)/post/report.txt"],
    deps=levels,
    cmd=["python3", "$(S)/dev/post.py", "--check", "--out", "$(B)/post", "--archive", "$(B)/libstr.a", "--report", "$(B)/post/report.txt",
         *[f"$(B)/libstr_{unit}.a" for unit in UNITS]],
    ldflags=["$(B)/libstr.a"],
    descr="PO",
    color="cyan",
)
install(libstr)

# tst/test.c against the C library of the host: every level the host supports, and the dispatched table.
test = program(
    name="test",
    output="$(B)/test",
    srcs=["$(S)/tst/test.c"],
    includes=["$(S)"],
    cflags=["-Wall"],
    ldflags=["-no-pie"],
    deps=[libstr],
)

checks = [
    command(
        name="check_test",
        outputs=["$(B)/checks/test.txt"],
        deps=[test],
        cmd=["sh", "-c", '"$0" > "$1"', "$(B)/test", "$(B)/checks/test.txt"],
        descr="CK",
        color="light-green",
    ),
    # The decoder and emitter against every unit at every optimization level.
    command(
        name="check_roundtrip",
        inputs=[*build.glob("$(S)/dev/*.py"), "$(S)/tst/roundtrip.py", *build.glob("$(S)/*.c"), "$(S)/common.h"],
        outputs=["$(B)/checks/roundtrip.txt"],
        cmd=["sh", "-c", 'python3 "$0" --out "$1" > "$2"', "$(S)/tst/roundtrip.py", "$(B)/roundtrip", "$(B)/checks/roundtrip.txt"],
        descr="CK",
        color="light-green",
    ),
]
group("checks", *checks)
