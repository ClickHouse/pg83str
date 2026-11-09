import os
import json
import shlex
import sys
from pathlib import Path

import build

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev"))
from units import UNITS  # noqa: E402

if build.target.split("-", 1)[0] in ("aarch64", "arm64"):
    if 'linux' not in build.target:
        raise ValueError('The AArch64 port currently targets Linux; pass --target aarch64-linux-gnu')
    build.flags.allow({'aor': {'descr': 'Arm optimized-routines checkout matching dev/lab/aarch64.lock',
                              'default': ''}})
    # The first ARM candidates use compiler output directly. The x86 decoder,
    # encoder, rewriting passes and proof semantics must never process them.
    build.cflags += ["-O3", "-fno-builtin", "-fno-stack-protector", "-fPIC",
                     "-ffunction-sections", "-fno-omit-frame-pointer", "-mno-omit-leaf-frame-pointer"]
    scalar_flags = ["-fno-tree-vectorize", "-fno-tree-slp-vectorize"]
    libstr = library(name="str", srcs=["$(S)/aarch64/scalar.c", "$(S)/aarch64/neon.c", "$(S)/aarch64/dispatch.c"],
                     cflags=["-Wall", "-Wextra", "-Werror", *scalar_flags, "-march=armv8-a+simd"])
    group("libstr", libstr)
    install(libstr)
    # The selected eleven-entry provider is independent of the workbench's
    # twenty-slot table. Never combine their identically named public entries.
    selected_inputs = ["$(S)/dev/aarch64_selected.py"]
    selected_inputs += build.glob("$(S)/aarch64/selected/*.c")
    selected_inputs += build.glob("$(S)/aarch64/selected/*.h")
    selected_inputs += ["$(S)/aarch64/selected/LICENSE", "$(S)/aarch64/selected/provenance.json"]
    selected_env = {name: os.environ.get(name, "") for name in ("CPPFLAGS", "CFLAGS", "LDFLAGS")}
    selected_cc = os.environ.get("CC", "clang")
    selected_ar = os.environ.get("AR", "llvm-ar")
    selected = command(name="selected_archive", inputs=selected_inputs,
                       outputs=["$(B)/selected/libstr-prefixed.a", "$(B)/selected/build.json",
                                "$(B)/selected/compile_commands.json"],
                       cmd=["python3", "$(S)/dev/aarch64_selected.py", "build",
                            "--target", build.target, "--cc", selected_cc, "--ar", selected_ar,
                            "--out", "$(B)/selected"], env=selected_env)
    group("selected", selected)
    if build.target == build.host:
        selected_test = command(name="selected_test", inputs=["$(S)/tst/aarch64_selected.c"],
                                outputs=["$(B)/selected/test.json"], deps=[selected],
                                cmd=["python3", "$(S)/dev/aarch64_selected.py", "test",
                                     "--target", build.target, "--cc", selected_cc, "--ar", selected_ar,
                                     "--out", "$(B)/selected"], env=selected_env)
        group("selected-test", selected_test)
    test = program(name="test", output="$(B)/test", srcs=["$(S)/tst/test.c"], includes=["$(S)"], deps=[libstr])
    # Runtime checks execute target binaries and are available only natively.
    if build.target == build.host:
        check_test = command(name="check_test", outputs=["$(B)/checks/test.txt"], deps=[test],
                             cmd=["sh", "-c", '\"$0\" > \"$1\"', "$(B)/test", "$(B)/checks/test.txt"])
        check_quick = command(name="check_quick", outputs=["$(B)/checks/quick.txt"], deps=[test],
                              cmd=["sh", "-c", '\"$0\" --quick > \"$1\"', "$(B)/test", "$(B)/checks/quick.txt"])
        group("checks", check_test)
        group("quick", check_quick)
    lock = json.loads((Path(__file__).parent / 'dev/lab/aarch64.lock').read_text())
    lab_inputs = ["$(S)/dev/lab/aarch64.py", "$(S)/dev/lab/aarch64.lock", "$(S)/dev/lab/kbench.c",
                  "$(S)/dev/lab/kl.h", "$(S)/str.h"]
    lab_inputs += build.glob("$(S)/dev/lab/kernels/aarch64_*")
    # Unrequested lab targets must not require an external checkout while a
    # native test or a host-tool graph is being constructed.
    if build.flags.aor:
        lab_inputs += [os.path.join(build.flags.aor, name) for name in lock['sha256']]
    kbench = command(name="kbench", inputs=lab_inputs, outputs=["$(B)/lab/kbench", "$(B)/lab/work/build.json"],
                     deps=[libstr], cmd=["python3", "$(S)/dev/lab/aarch64.py", "--aor", build.flags.aor,
                                        "--target", build.target, "--libstr", "$(B)/libstr.a",
                                        "--work", "$(B)/lab/work", "--binary", "$(B)/lab/kbench"])
    trace = command(name="lab_trace", inputs=["$(S)/dev/lab/workload.py"], outputs=["$(B)/lab/workload.bin"],
                    cmd=["python3", "$(S)/dev/lab/workload.py", "$(B)/lab/workload.bin"])
    group("lab", kbench, trace)
    proof_elf = command(name="proof_elf", inputs=[], outputs=["$(B)/proof/libstr.elf"], deps=[libstr],
                        cmd=[*shlex.split(os.environ.get('CC', 'clang')), '--no-default-config',
                             '--target=' + build.target, '-fuse-ld=lld', '-nostdlib', '-static',
                             '-Wl,--whole-archive', '$(B)/libstr.a', '-Wl,--no-whole-archive',
                             '-Wl,-e,0', '-Wl,--unresolved-symbols=ignore-all', '-o', '$(B)/proof/libstr.elf'])
    proof_oracle = command(name="proof_oracle", inputs=["$(S)/dev/proof/oracle.c", "$(S)/dev/proof/oracle.py"],
                           outputs=["$(B)/proof/oracle"], deps=[libstr],
                           cmd=["python3", "$(S)/dev/proof/oracle.py", "--libstr", "$(B)/libstr.a",
                                "--out", "$(B)/proof/oracle"])
    group("proof", proof_elf, proof_oracle)
else:
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

    if build.target == build.host:
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
        cmd=["python3", "$(S)/dev/lab/lab.py", "--deps", "$(B)/lab/deps", "--libstr", "$(B)/libstr.a", "--post-report", "$(B)/post/report.txt", "--work",
             "$(B)/lab/work", "--binary", "$(B)/lab/kbench"],
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
    # An A/B of two revisions of ours in one binary: dev/lab/base.py REV puts the
    # other revision's libstr.a and its name in .lab/base/, then ./build lab_base.
    kbench_base = command(
        name="kbench_base",
        inputs=[*kbench.inputs, *build.glob("$(S)/.lab/base/libstr.a"), *build.glob("$(S)/.lab/base/rev")],
        outputs=["$(B)/lab/kbench-base"],
        deps=[libstr, lab_deps],
        cmd=["python3", "$(S)/dev/lab/lab.py", "--deps", "$(B)/lab/deps", "--libstr", "$(B)/libstr.a", "--post-report", "$(B)/post/report.txt", "--work",
             "$(B)/lab/work-base", "--binary", "$(B)/lab/kbench-base", "--base", "$(S)/.lab/base/libstr.a", "--base-rev-file", "$(S)/.lab/base/rev"],
        descr="LB",
        color="cyan",
    )
    group("lab_base", kbench_base, trace)
    # The same with dev/lab/canary/*.c, kernels broken on purpose: validation must reject each.
    kbench_canary = command(
        name="kbench_canary",
        inputs=kbench.inputs,
        outputs=["$(B)/lab/kbench-canary"],
        deps=[libstr, lab_deps],
        cmd=["python3", "$(S)/dev/lab/lab.py", "--deps", "$(B)/lab/deps", "--libstr", "$(B)/libstr.a", "--post-report", "$(B)/post/report.txt", "--work",
             "$(B)/lab/work-canary", "--binary", "$(B)/lab/kbench-canary", "--canary"],
        descr="LB",
        color="cyan",
    )
    group("lab_canary", kbench_canary)

    # The library linked on its own for the prover (dev/proof): every function at
    # its address, the calls between them resolved; ./build proof.
    proof_elf = command(
        name="proof_elf",
        inputs=[],
        outputs=["$(B)/proof/libstr.elf"],
        deps=[libstr],
        cmd=["clang", "--no-default-config", "-fuse-ld=lld", "-nostdlib", "-static", "-Wl,--whole-archive", "$(B)/libstr.a", "-Wl,--no-whole-archive",
             "-Wl,-e,0", "-Wl,--unresolved-symbols=ignore-all", "-o", "$(B)/proof/libstr.elf"],
        descr="LD",
        color="cyan",
    )
    # The oracle: the same functions run natively, for dev/proof/replay.py.
    proof_oracle = command(
        name="proof_oracle",
        inputs=["$(S)/dev/proof/oracle.c", "$(S)/dev/proof/oracle.py"],
        outputs=["$(B)/proof/oracle"],
        deps=[libstr],
        cmd=["python3", "$(S)/dev/proof/oracle.py", "--libstr", "$(B)/libstr.a", "--out", "$(B)/proof/oracle"],
        descr="LD",
        color="cyan",
    )
    group("proof", proof_elf, proof_oracle)
