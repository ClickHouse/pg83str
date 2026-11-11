# Intel short-copy frontends

The implementation checkpoint is `cc0d5a140ff9039444b3a2a3fe1c1f795112e6b7`,
compared with `20e32940115e018f7360a1b85cded01da55b42cc`.
Only the `evex512i` and `evex512ri` `memcpy` and `memmove` slots change.
For 17–64 bytes, ordinary head/tail vector loads replace the masked-copy
path. All loads precede stores, and access only the requested interval.
Larger-copy algorithms, huge-copy policy and the six earlier profiles retain
their existing implementations.

The motivating CI workload is
[`length_utf8_long_strings[1]`](https://performance.ci.clickhouse.com/runs/pr-130343-4d963a7b96f8ac24563b0ebd45bfdc090115a366/tests/length_utf8_long_strings/queries/1?metrics=client_time)
in https://github.com/ClickHouse/ClickHouse/pull/130343.
Its repeated 14-byte string calls `memcpy` nine times per row with sizes
14, 14, 28, 28, 56, 56, 112, 224 and 448. This targets the copy caller;
it changes no UTF-8 counting code.

## Minimized public-entry model

On 2026-11-11, Clang 22.1.8 compiled one `tst/repeat_copy.c` caller object,
which was linked separately against the preceding and candidate archives.
The host is an Intel Xeon Platinum 8175M. Both ISA-compatible Intel profiles
were forced explicitly; neither is selected by default on this Skylake-SP
host. CPU 2 and sibling 18 were reserved by scoped locks. Other sessions
continued on the shared host.

Each process produced 700,000 rows using a reused 1,024-row buffer, two
warmups and nine samples. Two fixed orders, ABBA and BAAB, gave sixteen
processes and 144 timings. The statistic below is the geometric ratio of
the two candidate process medians to the two control process medians within
each bracket. Every sample and endpoint drift is retained.

| Profile | ABBA runtime change | BAAB runtime change |
|---|---:|---:|
| `evex512i` | -6.59% | -4.89% |
| `evex512ri` | -5.54% | -6.30% |

The full final resident output buffer matched after every iteration. The
model does not check each overwritten intermediate row. Compile-time
constants allow specialization or loop unrolling. These are descriptive
caller-model results, not independent SQL trials, the original PG mixed
benchmark, or a measured ClickHouse improvement. Changes in shared-host
load, code layout and endpoint drift remain possible explanations for part
of the difference.

## Native correctness and unwind checks

All 29 native build/check commands completed successfully. Eight
configurations plus dispatched selection and all 280 one-slot mixes passed
7,897,675,716 checks. Guarded pages, overlaps, public entries, 46 host
selection cases, three cached-boundary suites and early-constructor
publication passed. Signal sampling recovered the routine and standalone
caller in all 5,674 observed provider samples with regenerated CFI; a
same-code control without CFI recovered neither in 5,694 samples.

The prefixed archive SHA256 is
`5996e8affec1ccfe20e72cc94cc7a7646c776470efad2c6d1dfdbdc04f706957`;
the public archive SHA256 is
`15b512066c4e0efb7672cbf13fb2d88f07889ca92fb7fa7336e9f3d0781b201d`.
The caller-model analysis SHA256 is
`88125b88eb841cce083a219e91128dd1dbe4a3acdd2417ff49a1a396e496dc51`.
No whole-library semantic proof or new ClickHouse sampling result is claimed.
Bulk-copy and bulk-fill regressions remain separate tuning work.
