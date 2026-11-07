import os
import sys

import build

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev", "proof"))
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

# The proofs (dev/proof/README.md): a node per configuration's copy of each
# function by its spec, <spec>_<config>, and per class where the function is
# proved in classes, <spec>_<config>_<k> (dev/proof/targets.py says which,
# which copies have another's code, and which proofs take others' as their
# callees' contracts). ./build proof is the whole chain; hours to days of
# solver time, so on the lab: ./build --dist=lab1.mesh:20,lab2.mesh:25 -j 100
# proof (or a node: strspn_sse2, strspn_avx2_3), the proofs on the hosts only.
build.flags.allow({
    "proof_workers": {"descr": "a proof's checking threads (dev/proof/prove.py --workers)", "default": "1"},
    "proof_timeout": {"descr": "seconds a solver query may take before it is unknown (PROVE_TIMEOUT)", "default": "1800"},
    "proof_memory": {"descr": "megabytes a solver may take (PROVE_MEMORY)", "default": "3000"},
})
import targets as proof_targets  # noqa: E402

# z3 and bitwuzla from PATH (ix run bin/z3 bin/bitwuzla -- ./build proof), static: what the proofs on a host run
proof_solvers = command(
    name="proof_solvers",
    outputs=["$(B)/proof/bin/z3", "$(B)/proof/bin/bitwuzla"],
    cmd=["sh", "-c", 'cp "$(command -v z3)" "$0" && cp "${PROVE_BITWUZLA:-$(command -v bitwuzla)}" "$1"', "$(B)/proof/bin/z3",
         "$(B)/proof/bin/bitwuzla"],
    descr="CP",
    color="cyan",
    local=True,
)
proof_inputs = [f"$(S)/dev/proof/{f}.py" for f in ("node", "prove", "machine", "smt", "run", "targets")] + ["$(S)/dev/x86.py", "$(S)/dev/elf.py"]
proof_env = {"STR_PROOF_WORKERS": build.flags.proof_workers, "STR_PROOF_TIMEOUT": build.flags.proof_timeout,
             "STR_PROOF_MEMORY": build.flags.proof_memory}


def proof_node(name, out, args, deps=()):
    return command(
        name=name,
        inputs=proof_inputs,
        outputs=[out],
        deps=[proof_elf, proof_solvers, *deps],
        cmd=["python3", "$(S)/dev/proof/node.py", "--elf", "$(B)/proof/libstr.elf", "--bin", "$(B)/proof/bin", "--out", out, *args],
        env=proof_env,
        descr="PR",
        color="light-magenta",
        remote=True,
    )


proofs = {}  # (spec, config) -> the function's node
for spec, copies in proof_targets.FUNCS.items():
    for config in proof_targets.CONFIGS:
        func, name = copies[config], proof_targets.name(spec, config)
        out = f"$(B)/proof/{name}.txt"
        base = ["--spec", spec, "--func", func]
        if (spec, config) in proof_targets.SAME:  # the code of another configuration's copy: its proof
            other = proof_targets.SAME[(spec, config)]
            rep = proofs[(spec, other)]
            node = proof_node(name, out, [*base, "--same-as", copies[other], "--of", rep.outputs[0]], [rep])
        elif (spec, config) in proof_targets.SPLIT:  # the classes, then their sum
            n = proof_targets.SPLIT[(spec, config)]
            parts = [proof_node(proof_targets.name(spec, config, k), f"$(B)/proof/{name}/{k}.txt",
                                [*base, "--classes", str(n), "--align", str(k)]) for k in range(n)]
            node = proof_node(name, out, [*base, "--of", *[p.outputs[0] for p in parts]], parts)
        else:
            callees = [proofs[(dep, config)] for dep in proof_targets.DEPS.get(spec, ())]
            node = proof_node(name, out, [*base, *(["--callee", *[c.outputs[0] for c in callees]] if callees else [])], callees)
        proofs[(spec, config)] = node
group("proof", proof_elf, proof_oracle, *proofs.values())
