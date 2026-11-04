# The kernel lab

Every implementation of every primitive (`memcpy memmove memset memcmp strlen
strcpy strcat strcmp strspn strcspn memchr`) is a *kernel*: validated, then
timed over sizes, alignments and workload traces, on every host we have.
A kernel is the unit, not a library: each glibc IFUNC variant, each of our
configurations and slot choices, each new idea in `kernels/`.

```
ix run lib/c bld/compiler -- ./build lab            # .build/lab/kbench, .build/lab/workload.bin
.build/lab/kbench --list                            # every kernel, what it needs
.build/lab/kbench --validate-only --jobs 12
.build/lab/kbench --cpu 2 --prims strlen --kernels kl/,glibc/__strlen_evex,ours/evex512 --sizes quick --regimes lat
dev/lab/run.py --tag r1 -- --regimes thr,lat        # local, lab2, lab3 in parallel: .lab/r1/<host>/, the clock held
dev/lab/run.py --tag m1 --hosts lab2 --clock free -- --prims none --no-matrix --mixed --table 'old=evex:strlen=strlen_evex_jcc'
dev/lab/report.py r1 --regime lat                   # winners per size bucket, where ours lose
dev/lab/run.py --tag lib-1 -- --prims none --no-matrix --libs   # whole libraries on the workload (repeat: lib-2..5)
dev/lab/libs.py 'lib-*'                             # the platform x library table, glibc = 1
ix run lib/c bld/compiler -- ./build lab_canary     # kernels broken on purpose: every one must be rejected
```

## The kernels

- **glibc 2.44** (Debian's static `libc.a`, also kbench's C runtime): every IFUNC
  variant of the eleven primitives, the `_rtm` ones too (glibc runs them on
  CPUs with RTM, Broadwell among them).
- **ours**: every configuration of `str_configs` as dispatch installs it
  (`ours/evex512`, the post pass's copies with direct calls); the host's
  configuration through the table (`ours/evex512.t`); and that with one slot
  of the primitive replaced by every other implementation of the slot
  (`ours/evex512.t+strcmp_tail=strcmp_tail_avx2`). A primitive's slots are its
  entry and the kernels behind it (`copy_large`, `copy_huge`, `strcmp_tail`, …).
- **FreeBSD** `lib/libc/amd64/string`: scalar, baseline and x86_64_v2 variants.
- **musl** 1.2.5: its string files (C, and x86_64 `memcpy`/`memmove`/`memset` in asm).
- **LLVM libc**: the string functions in the Linux configuration, built for
  x86-64-v2, -v3 and -v4 (`llvm/llvm_v3_strlen`).
- **asmlib**: Agner Fog's dispatchers and variants (`asm_memcpyAVX512BW`).
- **kl**: `kernels/*.c`, new ones.

What a kernel needs (SSE4.2, AVX2 = x86-64-v3, AVX-512 = x86-64-v4, ERMS, RTM) comes
from its name; a host runs the kernels it can. One binary serves every host.

## A new kernel

`kernels/<isa>_<anything>.c`, `<isa>` one of `sse2 ssse3 sse42 avx2 evex
evex512` (evex: AVX-512 at 256 bits). The file is compiled like our unit of
that level (`-O3 -march=…`, `-I` the repository for `common.h`). Every global
`kl_<prim>_<isa>_<name>` with the libc signature is a kernel; the `<isa>` of
the name is the file's. A kernel may call our primitives through `str_*` (a
hybrid: its own path for small sets, `str_strspn` beyond) and says so in its
name. The rules are the library's: read only the object's bytes and any byte
of the pages it lies in, write only the result's bytes, compare unsigned
bytes, `memmove` takes any overlap, `memchr` matches `(unsigned char)c`.

## Inputs

Nothing from outside is kept here. `deps.py` fetches, by sha256 with a
fallback URL each: Debian's `libc6-dev` 2.44-3 and `libgcc-16-dev`, musl
1.2.5, asmlib's zip, and the files of `freebsd.lock` and `llvm-libc.lock` one
by one at their pinned commits. `freebsd/` holds the three headers that make
FreeBSD's assembly link on Linux with every variant exported. `./build`
caches all of it by content.

## What is measured

- **Validation**, each kernel in a child process of its own (a crash, a hang
  or a wrong answer drops the kernel, not the run): sizes 0..160 and up to 12
  KiB, 20 offsets per pointer, high bytes, every overlap direction, guard
  pages on both sides of every object, strspn sets of 1 to 64 bytes (also at
  a page end).
- **The matrix**: scenarios (memmove disjoint and overlapping by ±1 and ±n/2;
  memset a byte and zero; memcmp/strcmp equal, differing first and last;
  strspn/strcspn sets of 1, 4, 16 and 32 bytes; memchr absent and last),
  sizes (`full`: 0..64 each, then to 1 MiB, 115 points; `quick`: 34), offset
  pairs (22 for two pointers, 14 for one; `pe` ends the object at a page end).
  In a cell all kernels of the primitive in a shuffled order per round, a
  batch of about 40 us each, 5 rounds, the minimum and median ns per call of
  thread CPU time. Regimes: `thr`, independent calls; `lat`, each call's
  pointers depend on the previous result (what a caller that uses the result
  sees).
- **Traces** (`workload.py`): every function's trace of the workload and the
  long strspn/strcspn ones, each kernel, 7 passes, checksums against glibc.
  `--mixed`: the whole workload, all functions in their proportions, for
  whole tables: every configuration of ours, glibc as dispatched, and
  `--table NAME=CONFIG:SLOT=FN,...` (a configuration with slots replaced).
  A clock license one function takes slows the others, which only this shows.
- **Whole libraries** (`--libs`, the workload stand of the old bench.c
  `--workload`): ours as dispatched, glibc as dispatched, FreeBSD, LLVM libc
  at the host's level, musl, asmlib, each with its own function for every
  primitive, on the mixed trace and every function's trace; noop measures
  the stand's own cost per call, which `libs.py` subtracts. A library runs a
  trace only with a valid function for every primitive in it.
- **The core's clock** of every trace pass from APERF/MPERF where
  `/dev/cpu/N/msr` can be read (root: the lab hosts; here run.py uses sudo):
  turbo and the AVX-512 licenses in numbers (the `ghz` column).

The clock is held at the nominal frequency for a run (`freq.py`: boost or
turbo off, the performance governor, or on a host without cpufreq every core's
HWP request pinned at its guaranteed ratio) and given back after; kbench
measures it at the start and the end with a chain of dependent adds.

`out.csv.gz` has `prim,scenario,regime,n,align,kernel,min_ns,med_ns,iters,ghz`;
`meta.json` the host, its features, glibc's and our dispatch, every kernel and
its validation; `run.json` the commit, the binary's hash and the options.
