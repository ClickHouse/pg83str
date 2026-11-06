# AArch64 tuning investigation, 2026-11-06

The [continued glibc comparison](README.aarch64-target.md) supersedes this
initial five-slot performance verdict with tuning of all eleven public slots,
upstream-compatible net metrics, and longer-tail repairs. The measurements
below retain the initial experiment's identities and conclusions.

This investigation extends `pg83/str` itself. It follows the author's own
experimental variants, including unsuccessful ones, and tests the corresponding
ARM hypotheses. The starting ARM workbench is commit `9cc10e2`; upstream is
pinned at `21ad0a33534c63096e2d2ee879a9f435cd474a3c`.

The lab now contains **34 new experimental kernels**. They are candidates for
further work, not a replacement configuration. The library archive, scalar
default, and ClickHouse integration remain as before this investigation.
The existing eleven partial machine-code proofs do **not** cover these new
kernels. In particular, the proof backend does not implement SVE predicates.

## What pg83 actually tuned

These observations come from the pinned source and its recorded experiments,
rather than inferring the author's process from the final implementations.
Historical numbers below describe his x86 hosts, not ARM measurements.

| Source | Finding and ARM hypothesis |
| --- | --- |
| [`avx2_short.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/kernels/avx2_short.c) | Scalar prefixes won fixed-size latency by up to 3 times but lost shuffled traces by 1.4–3.4 times. Test a scalar-first negative control, a vector first block, and predicated short memory operations. |
| [`avx2_copy_order.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/kernels/avx2_copy_order.c) | Identical head/tail data movements changed from 35.6 to 28.6 ns with descending tail stores on Broadwell. Test store scheduling separately from byte count, and inspect generated instructions. |
| [`avx2_fill_order.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/kernels/avx2_fill_order.c) | Fewer stores won size buckets but lost the mixed trace. Test the decision tree and whole composition, rather than minimizing stores alone. |
| [`avx2_memset.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/kernels/avx2_memset.c) | Different size-tree shapes, broadcast choices, and flat dispatches are explicit alternatives. Test tiered versus flat ARM short fills. |
| [`evex512.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/evex512.c) | A masked first block avoids branching among common short sizes. Test SVE predicates with one, two, or four OS-sized vectors; Neoverse-V2's SVE vectors here are only 16 bytes. |
| [`evex.c`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/evex.c) | Inline sequential first blocks preserve early-hit latency; grouped reductions suit a longer tail. Test separate first-block and tail designs, serial versus balanced reductions, and independent pointer updates. |
| [`opt.py`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/opt.py) | Tail duplication, early-return layout, ABI-copy sinking, frame removal, and dependency scheduling go beyond algorithm selection. Some apparently cheaper instruction substitutions were explicitly rejected. Inspect ARM codegen and measure frame policy separately. |
| [`phases.json`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/phases.json), [`tune.py`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/tune.py) | Entry offsets depend on the host. Shared slots are scored across their callers, require a 3% gain, and proposals must be checked on the mixed trace. Sweep placement and validate composed tables. |

## Experiments and measurement conditions

The candidates live in `dev/lab/kernels/aarch64_*.c`:

* Ten NEON memory candidates: head/tail size tiers, two fill decision trees,
  three plain-C store schedules, and three schedules with ordering enforced.
* Ten NEON string candidates: aligned versus unaligned first loads, scalar
  first-byte controls, 16-byte comparisons, sequential early exits, three
  64-byte reduction variants, and a first block with a separate longer tail.
* Fourteen SVE candidates: one/two/four predicated vectors, overlap-safe short
  moves, page-predicated string scans, and two ways to combine predicated
  first blocks with NEON tiers.

Hardware was native Linux AArch64, Neoverse-V2, 32 CPUs, kernel
`6.8.0-1066-aws`, 4096-byte pages, and glibc 2.35. Measurements were
pinned to CPU 15, chosen after sampling CPU activity. Existing development
workloads were left running. `PR_SVE_GET_VL` returned 16 bytes. The compiler
was Ubuntu Clang 21.1.8, using `-O3`, PIC, function sections, 64-byte function
alignment, and retained frame pointers unless explicitly stated. References
were host glibc and fourteen entry points from Arm optimized-routines, pinned
at `20859d3fd9e797dde592c489c8ef7d383f778620` with source hashes.

The upstream generated workload was kept unchanged: 262,144 mixed calls and
131,072 calls in each independent primitive trace. It is a synthetic model,
**not a ClickHouse workload**. Candidate kernels and references ran in the same
binary with interleaved timing samples. Time is thread CPU nanoseconds per
call; ARM clock columns are unavailable. PMU access was restricted
(`perf_event_paranoid=4`), so the ARM results do not establish branch-miss
counts or a microarchitectural cause for each timing difference.

All 105 registered kernels passed native validation after adding the final
seven controls. Strict validation also requires matching trace checksums.
There were two initial screening runs, two refined screening runs, thirty
initial layout/frame comparisons, twelve refined mixed-table comparisons,
and four fresh-seed holdout comparisons.
The fixed-size matrix contained 18,832 rows: 22 sizes from 0 through 4096,
aligned/unaligned/page-end placements, latency and throughput regimes,
seven samples per cell, plus equality/mismatch and overlap scenarios.
Size-bucket traces supplement these measurements; their shorter replay and
different branch history prevent treating them as the original workload.

## Findings

The following are medians from the two refined primitive-trace screens, in
ns per call. These are complete shuffled traces, not fixed-size cells.

| Primitive | Candidate | Run 1 / run 2 | glibc, same runs | Best Arm reference, same runs |
| --- | --- | ---: | ---: | ---: |
| `memcpy` | SVE `gate4vl` | 9.957 / 9.940 | 10.377 / 10.491 | 9.502 / 9.398 |
| `memset` | SVE `gate4vl` | 8.909 / 8.957 | 9.443 / 9.429 | 8.046 / 8.062 |
| `strlen` | NEON unaligned first block | 8.447 / 8.365 | 7.733 / 7.685 | 7.740 / 7.671 |
| `strcmp` | NEON first 16, grouped tail | 5.460 / 5.409 | 12.827 / 12.810 | 12.802 / 12.813 |
| `memmove` | SVE `pred4` | 10.292 / 10.534 | 10.352 / 10.423 | 9.861 / 9.933 |

**The first-block/tail boundary is a tuning parameter.** Unconditional SVE
`pred4` processes a masked first block, then enters a simple remainder loop.
`gate4vl` instead checks whether the entire operation fits in four vectors;
larger operations enter the NEON head/tail tiers. On this host the boundary
is 64 bytes. In the 65–128-byte trace bucket, copy time changed from 17.841
to 8.776 ns and fill from 16.323 to 5.013 ns. This is not an argument for
adding another branch per tiny size: one/two-vector variants win tiny learned
buckets but lose the complete shuffled trace.

The variant that always writes the first four masked vectors and then
repeats the full operation through NEON tiers (`hybrid4vl`) loses to the
gated version. More overlap work and a different hot path need measurement;
sharing a good short prefix does not automatically make a good composition.

**Fixed-size results can reverse the selection.** The gated copy/fill wins
the full trace while losing several fixed-size latency cells to `pred4`.
For example, at aligned 129-byte copy the gated and unconditional variants
take 2.095 and 1.525 times glibc's time; at 192-byte fill the corresponding
ratios are 3.178 and 2.078. Both kinds of evidence are retained.

**A prompt first comparison matters more than a larger first reduction.**
Grouped 64-byte `strcmp` entry variants take about 7.9 ns on the shuffled
trace versus about 5.4–5.5 ns for the 16-byte first-block choices. Serial
versus balanced minimum reductions survive compilation, but produce no
useful trace gain here. Compiler-opaque pointer updates also produce no
measured gain. `block16` and `first16_tail64` are close enough that choosing
between them requires the mixed and long-input results. Scalar first-byte
checks lose on these traces, consistent with the failure mode in pg83's lab.

Short-string trace success does not imply dominance over every input.
Fixed short equal `strcmp` cells can be 1.5–1.7 times glibc's latency, while
4096-byte equal comparisons with the grouped tail take about half its time.
SVE page-predicated `strlen` is especially unattractive for long strings:
approximately 8.08 times glibc's 4096-byte latency, versus approximately 1.50
for the NEON candidate. It remains a negative control.

**Source store order is insufficient evidence.** Clang canonicalized the
plain ascending and descending copy variants to identical 93-instruction,
372-byte functions. Their small timing difference cannot demonstrate a
store-order effect. The interleaved plain variant also changed the pairing
and instruction count. Three additional controls use explicit vector stores
to preserve ordering. Their whole-trace rankings reverse across repeats;
there is no robust ARM store-order winner from this experiment.

## Whole-table and placement checks

The initial composed table uses SVE `pred4` for copy/move/fill, NEON unaligned
`strlen`, and NEON `first16_tail64` for `strcmp`; all other slots retain the
original NEON configuration. The all-NEON table substitutes NEON size tiers
for the three memory entries. Both were tested with separate memory-only
and string-only substitutions.

Thirty repeated comparisons covered experimental entry phases 0/16/32/48,
benchmark stands 0/3, and a separate frame-omission build at phase zero.
Each run had nine passes. Entry padding is placed **before** each symbol,
outside its CFI and size range, and is not executed; the stand offset instead
changes the benchmark loop. They are different experiments.

| Experimental policy | Initial composed table / glibc | All-NEON proposal / glibc |
| --- | ---: | ---: |
| Frames, phase 0 | 1.0155 | 1.0561 |
| Frames, phase 16 | 1.0116 | 1.0518 |
| Frames, phase 32 | 1.0151 | 1.0569 |
| Frames, phase 48 | 1.0145 | 1.0562 |
| Frame omission allowed, phase 0 | 1.0041 | 1.0478 |

Entries are medians of same-run ratios across three repeats and two stands,
not ratios assembled from unrelated timings. The initial composed table
takes a median 0.7500 times the original NEON configuration's time across
these thirty runs. It remains slower than glibc in every one of them.
The apparent placement gains overlap repeat variation and fall below the
upstream 3% selection threshold. Retain phase zero. Frame omission removes
five instructions from the inspected first-block comparison function and
suggests a small additional gain, but also changes layout and scheduling;
this is not an isolated causal estimate and is not enabled in the library.

The refined twelve-run comparison tests the gated copy/fill proposal against
the original `pred4` proposal in the same binary, with `first16_tail64` and
`block16` alternatives. Each has three repeats at both stands for phases zero
and sixteen:

| Table | Median time / glibc | Range across twelve runs | Median time / original NEON |
| --- | ---: | ---: | ---: |
| `pred4` control | 1.01511 | 1.01009–1.02490 | 0.74973 |
| Gated copy/fill, first-block/tail comparison | 1.01172 | 1.00323–1.02821 | 0.74657 |
| Gated copy/fill, `block16` comparison | 1.01061 | 1.00548–1.03041 | 0.74579 |

**The isolated gate improvement does not establish a mixed-table improvement.**
Gating with the first-block/tail comparison takes a median 0.99895 times the
control, with a range of 0.98825–1.00974. It improves eight runs and regresses
four. The `block16` table also reverses across runs. Neither clears the 3%
threshold; none beats glibc in these twelve comparisons. Keep the controls
and candidates rather than promote a tiny apparent winner. The reproducible
result is roughly 25% less time than the initial ARM NEON implementation.

A fresh seed-two trace with the same distributions confirms that baseline
gain in four additional runs. The two gated tables take median 0.75553 and
0.75223 times the original NEON time. They still take 1.01739–1.03126 and
1.01582–1.02312 times glibc's time, respectively. This checks a new randomized
trace; it does not test a different application distribution or CPU.

## Reproduction and evidence

Build the native ARM lab as described in [README.aarch64.md](README.aarch64.md).
The builder automatically discovers the ARM experiment sources and records
their hashes, header hashes, flags, archive hash, and linked binary hash.
For example:

```sh
mkdir -p .build/tuning
CC=clang AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy \
    ./build -Daor="$PWD/.build/optimized-routines" lab \
    > .build/tuning/build.log 2>&1
.build/lab/kbench --cpu 15 --strict-validation --no-matrix --mixed \
    --exclude '.t+' --trace .build/lab/workload.bin --passes 9 \
    --meta .build/tuning/screen-meta.json \
    > .build/tuning/perf_screen.log 2> .build/tuning/test_screen.log
CC=clang NM=llvm-nm OBJCOPY=llvm-objcopy \
    python3 dev/lab/aarch64_sweep.py \
    --aor .build/optimized-routines --libstr .build/libstr.a \
    --trace .build/lab/workload.bin --out .build/tuning/sweep --cpu 15 \
    --phases 0,16,32,48 --stands 0,3 --repeats 3 --passes 9 \
    --table 'gate4vl=neon:memcpy=kl_memcpy_sve_gate4vl,memmove=kl_memmove_sve_pred4,memset=kl_memset_sve_gate4vl,strlen=kl_strlen_neon_unaligned,strcmp=kl_strcmp_neon_first16_tail64' \
    > .build/tuning/sweep.log 2>&1
```

Choose an available CPU on the actual machine. The sweep runs sequentially,
requires supported and validated public kernels, aborts on any validation or
checksum failure, and records every command and input hash. Repeat with
`--omit-frames` and a separate output directory to examine that policy.

Local raw evidence is under `.build/tuning/`: original screens, `phase*`
directories, `final/perf_matrix.log`, `final/perf_buckets.log`, assembly
analysis, `refined/`, and summary JSON. The corresponding remote directory is
`/home/ubuntu/clickhouse/tmp/pg83-str-arm-tuning-2026-11-06/src/.build/tuning`.
Raw files and exact binaries are retained outside the committed source.
[The recorded measurements](dev/lab/results/aarch64-2026-11-06.json) retain
primitive screens, all 46 layout/refinement/holdout runs, selected matrix
cells, and the corresponding binary, archive, source, and header identities.

## Next tuning and verification work

1. Retain the measured first-block/tail candidates and compare them on actual
   ClickHouse call distributions, including small copies inside parsers,
   string comparisons, overlap frequency, and cold-cache large transfers.
   The synthetic mixed trace cannot establish end-to-end ClickHouse gains.
2. Tune the longer copy/fill loops and the untouched public functions.
   Test alignment strategy, 64/128-byte loop strides, store pairing, early
   far-line stores, and preload distance as separate experiments. These
   options are not established winners by the current measurements.
3. Test more ARM microarchitectures and SVE lengths. Do not transplant
   x86 register-renaming, JCC-erratum padding, or host choices to ARM.
   Optional MOPS and first-fault SVE string algorithms need their own
   feature gating, correctness model, and measurements before selection.
4. Extend the machine-code model for predicate registers, active-lane memory
   accesses, runtime vector length, and the new NEON syndrome operations.
   Prove the exact selected binary, its tails and slot composition; replay
   native instruction cases and require incorrect mutants to fail. Current
   assumptions still exclude tagged pointers and MTE certification.
5. Only after selection and correctness coverage, integrate the candidate
   configuration and run paired ClickHouse performance tests on dev and CI.
   No ClickHouse build, CI comparison, production default switch, or upstream
   pull request is claimed by this tuning investigation.
