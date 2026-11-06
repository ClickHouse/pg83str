# AArch64 comparison with glibc, 2026-11-06

Object-code verification subsequently found a real page-boundary bug in the
measured `memchr` candidate: an early match can precede an inaccessible page
even when the count extends into that page. The original object faults on
dev while glibc succeeds. The timing figures below remain historical
measurements; they do not qualify that binary as a correct replacement.
The corrected objects must pass the strengthened guard test, proof work,
and a new performance comparison before acceptance.
The [object-code verification report](README.aarch64-proofs.md) records the
repair, partial proofs, native replay and mutation gates, and corrected
performance of approximately 31% less net time than glibc.

This continues the [initial tuning investigation](README.aarch64-tuning.md)
with a target of 30% less time than glibc. The initial comparison tuned only
five public slots; this round tunes all eleven and repairs the observed
long-string and large-set regressions.

The selected configuration reaches **33.4% less harness-subtracted time than
glibc** at the median of twelve paired runs. Gross median time improves by
**19.6%**. These are native Neoverse-V2 results against glibc 2.35.

The result is an experimental lab configuration, requiring SVE and NEON on
the measured host. Its machine-code proofs are incomplete. It has not been
promoted to the library dispatcher or evaluated in ClickHouse CI.

## Metric and environment

The whole-library summary in upstream
[`libs.py`](https://github.com/pg83/str/blob/21ad0a33534c63096e2d2ee879a9f435cd474a3c/dev/lab/libs.py)
subtracts the empty benchmark harness from minimum times:

```text
net = library minimum ns/call - same-run noop minimum ns/call
paired ratio = candidate net / same-run glibc net
target = paired ratio <= 0.70
```

The earlier ARM report used gross medians. Both metrics are now recorded,
along with medians after subtracting the median empty-harness time. A 30%
net improvement must not be described as a 30% gross or ClickHouse query
improvement. The upstream best-across-runs statistic is also reported
separately from the median of paired ratios.

Measurements use native Linux AArch64 on dev: Neoverse-V2, CPU 15, SVE vector
length 16 bytes, 4096-byte pages, kernel `6.8.0-1066-aws`, and glibc 2.35.
Clang 21.1.8 compiles at `-O3` with PIC, 64-byte function alignment, and
retained frame pointers. Other development workloads continue running.
Thread CPU time is the timer. No host frequency settings were changed.

The workload and its distribution are upstream's synthetic model, rather
than a recorded ClickHouse call stream. Each mixed trace has 262,144 calls;
independent primitive traces have 131,072 calls each. Confirmation uses two
random seeds, benchmark stands zero and three, three repeats per combination,
and nine interleaved timing passes per table. All tables in a comparison
share one binary, including the previous candidates and reference controls.

References include host glibc and Arm optimized-routines pinned at
`20859d3fd9e797dde592c489c8ef7d383f778620`. The Arm reference hybrid uses its
eight available primitives and glibc for the three missing ones; it is
identified as a hybrid rather than a complete external library.

## What changed

* **Copy and fill tails:** four predicated SVE vectors handle short operations;
  larger operations enter NEON size tiers and loops with 64- or 128-byte
  strides. The longer loop matters: replacing only the first block left a
  costly scalar remainder loop. Eight-vector prefixes remain comparison
  candidates, rather than assuming that greater coverage is always faster.
* **`strlen`:** a 32-byte first block packs two mask bits per character into
  one 64-bit syndrome. This reduces the frequency of the first-block miss.
  A separate 128-byte NEON reduction tail addresses long strings without
  enlarging the hot prefix. The packing uses the approach visible in the
  pinned Arm `strlen` page-crossing path; its application here is measured
  as an unaligned normal prefix and compared with the prior 16-byte variants.
* **`memcmp`:** word-sized head/tail comparisons replace the byte-resolution
  loop for short inputs. Big-endian ordering of loaded words selects the
  earliest difference. Predicate-based alternatives remain negative controls.
* **`memchr`:** an active-lane SVE comparison handles short buffers without
  a scalar alignment prologue or byte-by-byte remainder scan.
* **`strcpy` and `strcat`:** SVE inclusive break predicates store through the
  first NUL, avoiding a variable-sized scalar store tail. Two initial blocks
  lead into a 64-byte NEON reduction loop for longer strings. The earlier
  version's per-vector predicate loop was over five times glibc's time in
  some 4096-byte cells; the split tail repairs that regression. Concatenation
  combines the new length prefix and split copy.
* **`strspn` and `strcspn`:** one-to-three-character sets use vector broadcasts.
  Larger sets use a 32-byte membership table and NEON table lookup. Five
  character bits choose a table byte; the remaining three choose a bit.
  This replaces the first prototype's slow scalar bitmap scan. In the
  long-set trace, the adaptive candidates take approximately half glibc's
  time while retaining the short-set gains.

All new candidates remain visible together, including rejected variants.
The lab now has 72 experimental kernels beyond the original ARM workbench.
The configuration is a table assembled for measurement, not a change to the
default archive. Slot-composition proofs and production integration remain
separate work.

## Confirmation results

The final confirmation results and exact identities are recorded in
[`aarch64-target-2026-11-06.json`](dev/lab/results/aarch64-target-2026-11-06.json).
Raw logs, complete matrices, and binaries are retained in `.build/target30/`.
The final configuration is named `target_split`.

The final repaired configuration validates all **143 registered kernels**
and passes the target in all twelve paired runs. Its exact binary SHA-256 is
`d1f6d106394191a6d29811559131734d850d22d9ef1537a2260ca66c5681aa7b`.
Earlier five-slot, packed-prefix, and ungrouped-copy results are retained as
controls; the figures here use the final configuration and its exact binary.

| Metric, candidate / glibc | Median across paired runs | Observed range |
| --- | ---: | ---: |
| Minimum minus minimum `noop`, upstream per-run metric | 0.66571 | 0.65239–0.69983 |
| Median minus median `noop` | 0.68357 | 0.65968–0.69147 |
| Gross median | 0.80418 | 0.79124–0.81007 |

Both net metrics are below 0.70 in every run. The separate upstream
best-across-runs ratio is 0.66197. This is not substituted for the paired
distribution when judging stability. The Arm reference hybrid has a median
net ratio of 0.96237 against glibc and remains a separate comparison control.

The independent primitive-trace screen explains where further tuning helped.
These are gross median ns/call, not subtracted times or a decomposition of
the mixed trace:

| Primitive | Selected candidate | glibc |
| --- | ---: | ---: |
| `memcpy` | 8.767 | 10.309 |
| `memmove` | 9.089 | 10.302 |
| `memset` | 6.413 | 9.509 |
| `memcmp` | 10.213 | 9.679 |
| `strlen` | 5.777 | 7.643 |
| `strcpy` | 9.694 | 16.067 |
| `strcat` | 17.236 | 26.332 |
| `strcmp` | 5.409 | 12.784 |
| `strspn` | 3.074 | 11.755 |
| `strcspn` | 16.119 | 20.453 |
| `memchr` | 9.098 | 10.025 |

The final fixed-size matrix contains 15,664 rows and covers all eleven
selected primitives. At 4096 bytes, median ratios across four alignments
are 0.693 for `strlen` latency, 0.487 for `strcpy`, and 0.419 for `strcat`.
This repairs the long-copy regression rather than merely tolerating it in
the aggregate. Long-set traces take 90.645 versus 196.273 ns for `strspn`,
and 97.787 versus 197.494 ns for `strcspn`.

A separate read-only PMU pass measures 43.476 cycles and 1.771 branch misses
per mixed call for the selected table, versus glibc's 57.939 cycles and 2.947
misses. Instructions are 47.112 versus 51.446. These counts support the mixed
timing improvement; one counter pass does not isolate each change's cause.

The size matrix remains necessary even when the target passes. Tiny fixed
overlap moves, short equal comparisons, and some medium-size throughput
cells can lose to glibc. The workload result is not a claim of dominance for
every size, alignment, or input distribution. The simple packed `strlen`
variant is faster in some medium cells but loses to the separated tail on
long strings; both alternatives are retained.
In particular, some 32–65-byte copy cells and tiny overlap moves still lose,
and `memcmp` remains slightly slower on its shuffled primitive trace. These
are retained tuning opportunities, not omitted from the result inventory.

## Reproduce the selection

Build on native Linux AArch64 with the pinned reference checkout described
in [README.aarch64.md](README.aarch64.md). Choose an available CPU.

```sh
mkdir -p .build/target30
CC=clang AR=llvm-ar NM=llvm-nm OBJCOPY=llvm-objcopy \
    ./build -Daor="$PWD/.build/optimized-routines" lab \
    > .build/target30/build.log 2>&1
table='target_split=neon:memcpy=kl_memcpy_sve_bulk128,memmove=kl_memmove_sve_bulk64,memset=kl_memset_sve_bulk128,memcmp=kl_memcmp_neon_words,strlen=kl_strlen_neon_prefix32_tail128,strcpy=kl_strcpy_sve_split64,strcat=kl_strcat_sve_split64,strcmp=kl_strcmp_neon_first16_tail64,strspn=kl_strspn_neon_adaptive,strcspn=kl_strcspn_neon_adaptive,memchr=kl_memchr_sve_pred1'
CC=clang NM=llvm-nm OBJCOPY=llvm-objcopy \
    python3 dev/lab/aarch64_sweep.py \
    --aor .build/optimized-routines --libstr .build/libstr.a \
    --trace .build/lab/workload.bin --out .build/target30/sweep --cpu 15 \
    --phases 0 --stands 0,3 --repeats 3 --passes 9 --noop --table "$table" \
    > .build/target30/sweep.log 2>&1
python3 dev/lab/aarch64_compare.py \
    .build/target30/sweep/phase0/perf_stand*.log \
    --target ours/target_split --require-ratio 0.70 \
    --out .build/target30/comparison.json \
    > .build/target30/test_comparison.log 2>&1
```

Repeat with a different generated seed and a separate output directory.
`aarch64_compare.py` requires the same table set and explicit empty-harness
measurements in every input. It rejects nonpositive or nonfinite times,
preserves gross figures, and can fail when the paired target exceeds the
requested ratio. It never fabricates an empty-harness value or clamps a
nonpositive subtraction to a small constant.

The experiment driver records commands, source/header hashes, compiler flags,
archive and binary identities, and both trace hashes. The existing archive
hash is `b7534a52b6c8a28cd11f6cccf89e2350bf550f98b1dc9068dbc998862f6adb63`.
The benchmark adds `--noop` independently of unavailable comparison libraries.
Read-only PMU sampling works through `sudo -n`; unscheduled events are
reported as unavailable rather than as zero. It changes no host settings.

## Remaining acceptance work

The prior eleven partial proofs cover the original library functions, not
these lab candidates. The selected machine code requires additional SVE
predicate and active-lane memory semantics, including inclusive breaks,
runtime vector length, and NEON table lookup and packed syndromes. Its
loops and slot compositions need proofs over the selected linked binary,
native replay, and incorrect-binary mutation checks.

Runtime guard-page validation is evidence, not a substitute for those proofs.
The existing model also retains untagged lower-47-bit pointers and 4096-byte
pages; tagged-pointer and MTE correctness is not certified. After correctness
coverage, compare actual ClickHouse workloads on dev and CI and add results
from more ARM microarchitectures and SVE lengths before choosing defaults.
