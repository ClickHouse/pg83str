# AArch64 object-code verification, 2026-11-06

The benchmarks ran natively on dev's Neoverse-V2, pinned to CPU 15. This
workflow takes the actual compiled object files, not translated C or a new
implementation of the algorithms. It links them into a proof ELF and executes
their decoded instructions symbolically against the existing specifications.

**Proof coverage remains partial.** Successful bounded checks, native replays,
and unsupported or timed-out proof attempts are distinguished from unbounded
induction proofs. None of the manifests certifies the complete ARM library.

## Inputs and provenance

The original measured executable has SHA-256
`d1f6d106394191a6d29811559131734d850d22d9ef1537a2260ca66c5681aa7b`.
Its nine original object files were copied from dev and individually hashed.
They link without compiling any C into a standalone ELF with SHA-256
`f862adead47d4dd594ef4d290764da46af5cdb39ee2e4e6de88e4a4c90dbd9f7`.
Only the eleven selected roots and their reachable dependencies are retained.

`dev/proof/aarch64_objects.py` can perform that link directly using `ld.lld`.
Alternatively, `--elf` accepts an ELF linked on the native host. It requires
every retained function to have object provenance and verifies that its
nonrelocation instruction bits match the supplied objects. `--benchmark`
performs the same comparison against the measured executable. The manifests
record object, code, ELF, decoder, solver, and model hashes. Relocation
application and data layout still trust the linker; this is not a proof of
the linker itself.

No kernel C is recompiled by the proof driver. The native replay harness is
compiled separately and linked with those same object files. Its startup
handshake reports the actual SVE vector length, which must match the model.

## A real bug exposed by the proof

The original `kl_memchr_sve_pred1` fails the memory-access specification.
Place the matching byte at the last readable byte before a protected page
and call it with a count of three. A normal SVE load activates three lanes
and faults on the next page before returning the first-byte match. On dev,
the original object receives SIGSEGV while glibc returns the matching pointer.

This is a valid early-match case: `memchr` behaves as though it reads bytes
sequentially and stops at the first match, as documented by the
[GNU C Library](https://sourceware.org/glibc/manual/latest/html_node/Search-Functions.html).
The old guard fixtures required the entire count to fit in readable memory,
so they missed it. The original 33.4% performance result is preserved as a
historical measurement, with its correctness acceptance withdrawn.

The corrected SVE candidates limit each group to the current page and advance
by the number of bytes actually examined. The NEON block candidate also
avoids crossing a page before checking for an early match. Native validation
now tests readable suffixes of 1–128 bytes with an oversized count, including
`SIZE_MAX`. The repaired objects pass all 143 registered kernels and the
original reproducer; both glibc and the repaired candidate exit successfully.

The corrected object-only proof ELF has SHA-256
`8ece707433500ccef2ab628ea4aa88a3eef36a558c1aaa23eb835a38749e83be`.
Its native validation/benchmark executable is
`f75c302facfc5005d6386765893d8e55aad3a887aae2001e5f241fd5e85f55e7`.
Original and repaired inputs are retained separately.

## Completed verification and gaps

Five selected kernels pass unbounded induction in the experimental model.

The eleven-kernel audit uses the model pinned at commit
`be66a492bb995ecb5b0482b0ed23a62c7c82d0e6`. Later model checks are recorded
separately with their own hashes, preserving the original proof evidence.

| Function | Paths | Obligations | Solver queries |
| --- | ---: | ---: | ---: |
| `kl_memcpy_sve_bulk128` | 7 | 48 | 183 |
| `kl_memmove_sve_bulk64` | 44 | 234 | 853 |
| `kl_memset_sve_bulk128` | 7 | 45 | 117 |
| `kl_memcmp_neon_words` | 13 | 77 | 250 |
| `kl_strcmp_neon_first16_tail64` | 191 | 1155 | 2305 |

These runs cover symbolic addresses and every modeled length, with loop base
and step obligations. The specifications check permitted accesses, returned
values, required bytes written, source preservation where required, and ABI
preservation. Word comparisons are checked by the sign at the first differing
byte rather than requiring the numerical difference of two byte values.

The remaining selected functions still have failed induction obligations,
missing frontier/invariant support, or proof timeouts. The manifest lists
each result; none is counted as proved. A failed induction obligation is not
automatically a concrete implementation bug. The original `memchr` failure
was additionally reproduced on hardware, unlike the remaining proof gaps.

A subsequent model revision fixes an overly conservative zero-length check:
an SVE load with no active lanes performs no memory access. The repaired
`kl_memchr_sve_pred1` then passes a **bounded check for lengths 0–4**, with
symbolic addresses, 4 paths, 20 obligations, and 59 solver queries. Unbounded
coverage remains five kernels. The revised model still refutes the original
page-boundary bug and all four copy mutants. Separate SMT checks confirm
that an active load at length zero is rejected while an inactive load is
permitted. Symbolic returns are resolved only when the solver proves their
target; unconstrained returns and unmodeled vector-scaled SVE memory offsets
are rejected. Variable-index frontier candidates still require base and
step proofs.

With this revision, the unbounded `memchr` attempt reaches the 240-second
limit; `strspn` and `strcspn` each reach the 180-second limit. Neither a
timeout nor discharged intermediate obligations count as a completed proof.

[The recorded object audit](dev/proof/results/aarch64-objects-2026-11-06.json)
contains exact input identities, per-function proof status, replay and mutation
results, and the post-fix performance comparison.

All eleven repaired functions agree with native dev execution for 100
generated inputs each, including returned values and the entire memory image.
This is instruction-model validation on 1,100 cases, not exhaustive proof.
Existing scalar ARM and x86 proof smoke checks also pass.

The selected copy passes a separate mutation gate: after proving the original
unboundedly, four patched binaries are refuted with concrete bounded
counterexamples. The mutations omit a store, read the destination instead of
the source, write to the source, and empty the first predicate. Unsupported
instructions, timeouts, and solver `unknown` do not count as rejected mutants.

The SVE model currently requires an explicit **16-byte vector length**.
Other SVE lengths are rejected rather than assumed equivalent. Other retained
assumptions are untagged lower-47-bit addresses, 4096-byte pages, and standard
AAPCS64. Tagged pointers, MTE, streaming SVE, other vector lengths, dispatch
selection, and full slot composition are not certified. The instruction
model and prover remain part of the trusted computing base.

## Reproduce from objects

Use the exact native objects from the lab build, a pinned Capstone environment,
and Z3 on `PATH`. Select the same public table by name from the recorded
benchmark JSON or provide a JSON map of specification name to function name.

```sh
mkdir -p .build/object-proofs
.build/proof-venv/bin/python dev/proof/aarch64_objects.py \
    --objects .build/lab/work/aarch64_*.o \
    --selection dev/lab/results/aarch64-target-2026-11-06.json \
    --linker ld.lld --benchmark .build/lab/kbench \
    --sve-vl 16 --timeout 240 --workers 2 --out .build/object-proofs/coverage \
    > .build/object-proofs/test_coverage.log 2>&1
.build/proof-venv/bin/python dev/proof/aarch64_object_mutants.py \
    --elf .build/object-proofs/coverage/kernels.elf \
    --out .build/object-proofs/mutants \
    > .build/object-proofs/test_mutants.log 2>&1
```

The full proof command exits nonzero while any requested kernel is unproved.
`--functions` restricts a run but leaves every other selected kernel explicitly
`not_run`. `--max` requests bounded checks and records the bound separately.
Replay takes `--sve-vl 16` and verifies that assumption against the native
oracle's handshake before comparing execution.

## Corrected performance

Eight new native comparisons use two trace seeds, two benchmark offsets, two
repeats, and nine interleaved passes. The repaired table's median paired ratio
to glibc is **0.69017** on the upstream minimum-minus-empty-harness metric
(range 0.68047–0.69631). Median-minus-median-harness gives 0.69099, and gross
median time gives 0.80711. Thus the corrected candidate still clears the
30% net target in all eight runs, with approximately 19.3% less gross time.

These corrected timings supersede the buggy configuration's acceptance
verdict. They remain synthetic Neoverse-V2/glibc-2.35 measurements, not
ClickHouse or CI results. Complete proof coverage is still required before
promoting the configuration.
