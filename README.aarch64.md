# Linux AArch64 workbench

This is an experimental extension of `pg83/str`, based on upstream commit
`21ad0a33534c63096e2d2ee879a9f435cd474a3c`. It provides candidates and the
measurement/verification machinery needed to develop an ARM implementation.
It is not a tuned replacement for glibc or ClickHouse's existing ARM routines.

The [AArch64 tuning investigation](README.aarch64-tuning.md) follows pg83's
experimental variants, adds 34 lab candidates, and records native comparisons,
generated-code checks, rejected alternatives, and remaining proof work.
The [continued comparison with glibc](README.aarch64-target.md) tunes all
eleven public entries and separates gross time from the upstream summary's
empty-harness-subtracted metric.
The [object-code verification report](README.aarch64-proofs.md) records the
exact-object proof workflow and the page-boundary bug it subsequently found
and repaired in the measured `memchr` candidate.

The public table and its twenty slot contracts live in `str.h`, shared with
x86-64. `aarch64/scalar.c` supplies all eleven public primitives and their
kernels. `aarch64/neon.c` adds AdvSIMD candidates; the set-membership slots
still use the scalar algorithms. The dispatcher exposes both configurations
to the tests and lab and retains the scalar default while tuning and proof
coverage are incomplete. An initially valid table is available before any
constructor runs. Runtime feature detection uses Linux `AT_HWCAP`.

The ARM archive currently uses compiler output directly. The x86 decoder,
encoder, rewriting passes, CPU decisions, and tuning offsets are not applied
to ARM. ARM frame pointers and compiler unwind information are retained.

## How upstream develops the library

The approach is documented in [CONTRIBUTING.md](CONTRIBUTING.md),
[the kernel lab](dev/lab/README.md), and [the prover](dev/proof/README.md):

1. Human-directed design, implementation, profile analysis, and review, with
   LLMs writing the implementation.
2. Pinned comparison implementations, measured individually and as mixed
   workloads. The x86 lab includes glibc, FreeBSD, musl, LLVM libc, and asmlib;
   it does not currently register a Go implementation.
3. Guard pages, exhaustive sizes/alignments, overlap cases, random data,
   cross-page accesses, mixed slots, and deliberately broken canary kernels.
4. Latency and throughput matrices, long shuffled traces, four copies of the
   benchmark driver at different alignments, and one-slot-at-a-time tuning.
   Measurements select implementations; a larger vector width is not assumed
   to be better. The synthetic mix is a documented model, not a ClickHouse trace.
5. Correctness obligations over the linked machine code. Instruction semantics
   are compared with a hardware oracle; loop invariants are checked at their
   base and step. Z3 and Bitwuzla discharge the obligations. `unknown` is not
   accepted. Patched incorrect binaries must be rejected.

## Build and validate natively

Use Linux AArch64 and Clang. The current guard-page fixtures require a 4096-byte
page host. The generic scalar/AdvSIMD build requires the ARMv8-A SIMD baseline.

```sh
mkdir -p .build
CC=clang AR=llvm-ar ./build libstr test quick > .build/test_arm_quick.log 2>&1
CC=clang AR=llvm-ar ./build checks > .build/test_arm_full.log 2>&1
```

`--quick` skips the costly exhaustive sweeps and random section but retains
guard-page coverage and the one-slot mixes. The default executable runs the
full suite. Runtime check targets are declared only for a native build; a
cross build can compile `libstr` and `test` but does not execute the target.

## The ARM comparison lab

`dev/lab/aarch64.py` builds the existing `kbench.c` driver with a generated ARM
registry. It includes the host glibc, fourteen Arm optimized-routines entry
points, and forty candidate slot implementations. `aarch64.lock` records the
reference commit and every consumed file's SHA-256. Missing or mismatched
inputs fail the build. The current Arm reference set covers eight primitives;
missing `strcat`, `strspn`, and `strcspn` implementations are not fabricated.
Go, musl, LLVM libc, and FreeBSD are not yet registered in the ARM lab.

```sh
git clone https://github.com/ARM-software/optimized-routines .build/optimized-routines
git -C .build/optimized-routines checkout --detach 20859d3fd9e797dde592c489c8ef7d383f778620
CC=clang AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy \
    ./build -Daor="$PWD/.build/optimized-routines" lab > .build/build_arm_lab.log 2>&1
.build/lab/kbench --validate-only --meta .build/validation.json \
    > .build/test_arm_lab_validation.log 2>&1
.build/lab/kbench --cpu 20 --no-matrix --mixed --exclude '.t+' \
    --trace .build/lab/workload.bin --passes 7 --meta .build/run1-meta.json \
    > .build/run1.csv.log 2> .build/test_arm_run1.log
```

Choose an idle CPU on the actual host. Repeat comparisons sequentially and
retain raw samples, metadata, build/compiler flags, and binary hashes. ARM
core-frequency measurement is not implemented; zero in the existing clock
columns means unavailable, and the timing source is thread CPU time. The lab
does not change the machine's frequency policy or terminate other workloads.

## Experimental ARM machine-code proofs

`dev/proof/aarch64_machine.py` decodes an AArch64 ELF using pinned Capstone and
implements instruction semantics over upstream's SMT/memory term layer.
`prove.py --arch aarch64` reuses the specification and invariant engine. The
backend models AAPCS64 argument/return registers, the link register, a
16-byte-aligned stack, preserved general registers, and the preserved low
64 bits of vector registers 8 through 15. Unknown instructions and layouts
are rejected. Additional semantics and invariant support are still needed.

```sh
python3 -m venv .build/proof-venv
.build/proof-venv/bin/pip install -r dev/proof/aarch64-requirements.txt
CC=clang NM=llvm-nm ./build proof > .build/build_arm_proof.log 2>&1
# z3 must be on PATH. This asks for every candidate, and fails if any is unproved.
.build/proof-venv/bin/python dev/proof/aarch64_suite.py \
    --elf .build/proof/libstr.elf --out .build/arm-proofs \
    > .build/test_arm_proofs.log 2>&1
.build/proof-venv/bin/python dev/proof/aarch64_mutants.py \
    --elf .build/proof/libstr.elf --out .build/arm-mutants \
    > .build/test_arm_mutants.log 2>&1
```

The coverage manifest identifies the exact ELF, decoder, and solver. A subset
run leaves every other candidate explicitly `not_run`. It does not certify the
library, dispatcher, or instruction model. Proof assumptions currently retain
upstream's lower 47-bit untagged address range and 4096-byte page model. Tagged
pointers, MTE, broader ARM address ranges, SVE, and MOPS are not certified.

`replay.py --arch aarch64` compares modeled execution with the native oracle,
including returned values and all bytes of its memory image. The mutation
gate proves the original scalar copy by induction and finds bounded concrete
counterexamples for wrong data, a wrong destination, an omitted store, and
corruption of a preserved register. These are tests of the new model, not a
claim that the model is complete or independently verified.

## Evidence from 2026-11-06

The quick suite passed 266,099,361 checks on both a native ARM Linux VM and a
Neoverse-V2 dev host. All 71 generated lab kernels validated on both hosts.
Two Neoverse mixed runs with seven passes each measured the initial NEON
configuration at 27.456/28.490 ns per call versus glibc at 20.295/20.742 ns,
or 1.353/1.374 times the glibc time. This candidate should not replace glibc.

Completed model proofs cover seven scalar primitives and four NEON primitives:
scalar `memcpy`, `memmove`, `memset`, `memcmp`, `strlen`, `strcpy`, `memchr`, and
NEON `memcpy`, `memmove`, `memset`, `memcmp`. Remaining kernels are unproved.
Scalar `strcmp` needs an invariant for its carried, preloaded comparison byte;
NEON `strlen` needs support for its vector lookahead before the scalar tail.
Passing these partial proofs is not full ARM verification.

Before promotion, extend the reference corpus, tune the observed size ranges
and alignments on native hosts, validate the full suite, expand replay and
mutation coverage, complete the function/slot/dispatch proofs, then run paired
ClickHouse workloads and CI. The present experiment changes no ClickHouse
default and has not been submitted upstream.
