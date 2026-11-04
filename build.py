import os
import sys

import build

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev"))
from units import UNITS  # noqa: E402

# -fno-pic: the post passes rewrite the objects and want plain PC32/PLT32
# relocations; the library is meant to be linked into a static libc anyway.
# -ffunction-sections: one function per section, which the post pass needs
# to give every configuration its own copies of the functions it reaches.
build.cflags += ["-O3", "-fno-builtin", "-fno-stack-protector", "-fomit-frame-pointer", "-fno-pic", "-fno-pie", "-ffunction-sections"]

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

# Line and branch coverage of the C by the test: gcc, gcov, no post pass (see dev/coverage.py).
coverage = command(
    name="coverage",
    inputs=[*build.glob("$(S)/*.c"), *build.glob("$(S)/*.h"), "$(S)/tst/test.c", "$(S)/dev/coverage.py", "$(S)/dev/units.py"],
    outputs=["$(B)/coverage/summary.txt", "$(B)/coverage/uncovered.txt"],
    cmd=["python3", "$(S)/dev/coverage.py", "--out", "$(B)/coverage"],
    descr="CV",
    color="cyan",
)

# The kernel lab (dev/lab/README.md): every implementation of every primitive
# from glibc, FreeBSD, musl, LLVM libc, asmlib, ours and dev/lab/kernels,
# validated and timed over sizes, alignments and traces. Built on request
# only (./build lab); its outside inputs are pinned by hash in dev/lab/deps.py.
lab_deps = command(
    name="lab_deps",
    inputs=["$(S)/dev/lab/deps.py", "$(S)/dev/lab/freebsd.lock", "$(S)/dev/lab/llvm-libc.lock"],
    outputs=[f"$(B)/lab/deps/{name}" for name in ("libc6-dev.deb", "libgcc-dev.deb", "musl.tar.gz", "asmlib.zip", "freebsd.tar", "llvm-libc.tar")],
    cmd=["python3", "$(S)/dev/lab/deps.py", "--out", "$(B)/lab/deps"],
    descr="FE",
    color="yellow",
)
kbench = command(
    name="kbench",
    inputs=["$(S)/dev/lab/lab.py", "$(S)/dev/lab/kbench.c", "$(S)/dev/lab/kl.h", "$(S)/common.h", *build.glob("$(S)/dev/lab/kernels/*.c"),
            *build.glob("$(S)/dev/lab/canary/*.c"),
            *build.glob("$(S)/dev/lab/freebsd/*.h"), *build.glob("$(S)/dev/lab/freebsd/machine/*.h")],
    outputs=["$(B)/lab/kbench"],
    deps=[libstr, lab_deps],
    cmd=["python3", "$(S)/dev/lab/lab.py", "--deps", "$(B)/lab/deps", "--libstr", "$(B)/libstr.a", "--work", "$(B)/lab/work", "--binary", "$(B)/lab/kbench"],
    descr="LB",
    color="cyan",
)
trace = command(
    name="lab_trace",
    inputs=["$(S)/dev/lab/workload.py"],
    outputs=["$(B)/lab/workload.bin"],
    cmd=["python3", "$(S)/dev/lab/workload.py", "$(B)/lab/workload.bin"],
    descr="GN",
)
group("lab", kbench, trace)
# The same with dev/lab/canary/*.c, kernels broken on purpose: validation must reject each.
kbench_canary = command(
    name="kbench_canary",
    inputs=kbench.inputs,
    outputs=["$(B)/lab/kbench-canary"],
    deps=[libstr, lab_deps],
    cmd=["python3", "$(S)/dev/lab/lab.py", "--deps", "$(B)/lab/deps", "--libstr", "$(B)/libstr.a", "--work", "$(B)/lab/work-canary", "--binary",
         "$(B)/lab/kbench-canary", "--canary"],
    descr="LB",
    color="cyan",
)
group("lab_canary", kbench_canary)
