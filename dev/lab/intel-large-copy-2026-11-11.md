# Large Intel copies, 2026-11-11

The `evex`, `evex512i` and `evex512ri` configurations use a forward SSE2 loop
with a bounded read prefetch for copies of at least 64 MiB. Copies below
256 KiB retain their cached vector implementation; the interval from 256 KiB
through 64 MiB exclusive retains REP. Fill functions and the public 16-byte
copy bypass are unchanged.

The new loop loads two 16-byte vectors before storing either, advances by
32 bytes, and handles the remainder in bounds. It permits forward overlap
with the destination below the source, as required by the huge-copy helper
contract. A prefetch at source + 512 is issued only when more than 576 bytes
remain, keeping the complete hinted cache line inside the source range.

## Experiments

An exploratory matrix compared twelve cached loops: vector widths of
16/32/64 bytes, unroll factors of two/four, and read-prefetch distances of
zero/512 bytes, alongside REP, the existing SSE2 helper and LLVM libc.
It retained 95,850 timings across copy and fill cases. The selected loop
was promising against REP for large copies. The cached fill alternatives
had substantial hot-data losses, so no fill change was selected.

A second matrix compared the actual processed public-copy implementations
from `5e9afb8` and `1b4fdc6`, using identical caller and registry objects.
It retained all 25,920 timings: nine sizes from 256 KiB through 64 MiB,
four source/destination page-offset pairs, three reuse patterns, three
ISA-compatible forced configurations, and both ABBA/BAAB process orders.
The configurations ran on the same Xeon Platinum 8175M; forcing a
configuration does not reproduce another CPU.

For each bracket, the comparison uses the geometric mean of its two process
medians per implementation. Raw samples and orders are not pooled. All
sizes, offsets, adverse cells, endpoint drift and variation remain in the
saved experiment artifacts.

At the sampled 64 MiB size, all 72 bracket comparisons improved:

| Configuration | Observed runtime change across offsets, patterns and orders |
|---|---:|
| `evex` | -44.53% to -1.18% |
| `evex512i` | -40.28% to -15.64% |
| `evex512ri` | -43.96% to -5.35% |

The original 8 MiB activation candidate also had adverse comparisons at
8, 16 and 32 MiB, including +82.35% at 16 MiB and +32.93% at 32 MiB.
Unchanged lower-size routes varied too. Those results were retained, and
the implementation boundary was raised to 64 MiB in `b147a37`.

This is a post-hoc activation boundary. Only the sampled 64 MiB size
improved in every measured offset/pattern/order combination. The experiment
does not establish a universal improvement at larger sizes, on other CPUs,
or in ClickHouse queries. Application and CI validation are separate.

## Correctness and unwind checks

The predecessor `1b4fdc6` passed the original nine full API suites and 280
one-slot mixes: 7,897,675,716 checks. Its activation boundary was 8 MiB.
The 64 MiB successor was then checked separately:

- All eleven processed objects match the predecessor byte for byte after
  reversing only five decoded threshold immediates from 64 MiB to 8 MiB.
  The comparison includes code, relocations, symbols and unwind data.
- The expanded guard test passed 7,730 calls, including all previous cases,
  64 MiB boundaries at ±1 and ±16 bytes and 128 MiB, independent libc results,
  returns, complete destination sentinels, read-only sources, guard pages,
  four page-offset pairs, forward helper overlap and backward public
  `memmove` overlap.
- Constructor/publication, three cached-copy configurations and all nine
  public quick suites passed on the new archive.
- Roundtrip, post-pass code/relocation equivalence with and without CFI,
  libc symbol renaming, helper targets, JCC layout and final FDE checks
  passed. Compiler leaf frames may be omitted; asynchronous unwind tables
  remain enabled and the post-pass frame-removal pass remains disabled.
- The 64 MiB + 64-byte signal case recovered the interrupted IP and caller
  for all 5,771 observed provider samples, including actual new-helper PCs
  in all three affected configurations. This is standalone signal evidence.

The full API sweep was not rerun on the threshold-only successor; its
predecessor evidence is conditional on the exact object comparison above.
The prefetch instruction is not modeled by the existing symbolic executor,
so no new unbounded object-code proof is claimed for this loop.

The processed prefixed archive tested for `b147a37` has SHA256
`c15a0296be8cff399e75ec2b9c6232ca597b6eb8781b0094dd7888fdbe885303`.
The libc-renamed archive has SHA256
`2f18f594e1067308905c6992effe8948cc3746cd6e459f091902dca95ce9ee16`.
Subsequent documentation changes do not change the implementation.

## Prefetch roundtrip repair

The first production-object roundtrip caught an existing codec error:
`0f 18 /1` (`prefetcht0`) was re-encoded as `/0` (`prefetchnta`). The
decoder and encoder now preserve the four supported hint selectors.
Controls cover 28 literal forms, 112 hint substitutions, 29 refusals,
the complete compiled SSE2 object and four RIP-relative relocation cases.
The failed attempt remains preserved; no incorrect hint rewrite was used
for the accepted production matrix.
