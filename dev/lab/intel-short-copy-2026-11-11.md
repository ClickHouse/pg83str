# Intel short-copy frontends

The initial implementation checkpoint is `cc0d5a140ff9039444b3a2a3fe1c1f795112e6b7`,
compared with `20e32940115e018f7360a1b85cded01da55b42cc`.
Only the `evex512i` and `evex512ri` `memcpy` and `memmove` slots change.
The frontend uses 128-bit head/tail copies for 17–32 bytes and replaces
the masked 512-bit path for 33–64 bytes with 256-bit head/tail copies.
All loads precede stores, and access only the requested interval.
Larger-copy algorithms, huge-copy policy and the six earlier profiles retain
their existing implementations.

The motivating CI workload is
[`length_utf8_long_strings[1]`](https://performance.ci.clickhouse.com/runs/pr-130343-4d963a7b96f8ac24563b0ebd45bfdc090115a366/tests/length_utf8_long_strings/queries/1?metrics=client_time)
in https://github.com/ClickHouse/ClickHouse/pull/130343.
Its repeated 14-byte string calls `memcpy` nine times per row with sizes
14, 14, 28, 28, 56, 56, 112, 224 and 448. This targets the copy caller;
it changes no UTF-8 counting code.
The original XML sets `max_block_size=1024` and `max_threads=1`, matching
the model's row block and single execution thread. The compiled ClickHouse
`ColumnString` representation has contiguous, non-terminated bytes, so a
14-byte row repeated 70 times occupies 980 bytes before the next row.

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

## Rejected move frontend

The initial eight-case PG run compared two seeds and four stands, nine
passes per case, using the original driver and min-pass-minus-noop method.
It did not establish a mixed-workload win. In the dedicated `memmove`
trace, candidate/control paired net ratios had median increases of 2.706%
for `evex512i` and 2.568% for `evex512ri`; all eight cases were adverse.
The original PG best-profile statistic also increased by 2.360% and
2.506% for the two profiles. These observations are descriptive and share
the min-selection, code-layout and shared-host limitations of the method.

The follow-up restores the preceding `memmove` slots and removes its
proposed frontend, retaining the `memcpy` experiment. The initial archive
identities, contracts and caller-model measurements above belong to the
initial checkpoint. They do not substitute for validation of the revised
archive. All initial results and the interrupted seventh attempt are retained;
the distinct continuation reused six completed cases and collected only the
two missing cases. The interrupted attempt contained 54 raw rows and no
completed process receipt or validated CSV, so it was excluded from ratios.


## Memcpy-only native validation

The revised implementation is `ec023b643455b85aa8c0c01c472d6f511fcc8412`.
Its native validation completed on 2026-11-11. All 29 commands passed,
including nine full API suites, 280 one-slot mixes, 46 host cases, public
entries, three cached-boundary suites and early-constructor publication.
The full and mixed suites completed 7,897,675,716 checks. All 5,694 observed
provider samples recovered both the routine and standalone caller with CFI;
the same-code control without CFI recovered neither in 5,716 observations.
Every profile/path had at least 25 provider samples. Independent inspection
counts 408 FDE records in eleven members, without claiming a new semantic
CFI proof or actual ClickHouse capture.

The revised prefixed archive SHA256 is
`d0b8c304cbb1db8f2829ecfdd9708e6b68ed61378417fdb025f8756e55a46f2b`;
the revised public archive SHA256 is
`6435c93e9b19592158be33cbf4400d5fd767b22bb5efb1897f37b1bf0637d511`.


## Fresh memcpy-only caller measurements

The fixed caller experiment was repeated against the revised archive, with a
freshly compiled common caller object and all sixteen fresh processes.
The previous candidate's timings were not reused. The same CPU, profiles,
700,000 rows, 1,024-row buffer, two warmups and nine samples per process apply.

| Profile | Order | Control (ms) | Candidate (ms) | Runtime change |
|---|---|---:|---:|---:|
| `evex512i` | ABBA | 60.006169 | 54.310588 | -9.49% |
| `evex512i` | BAAB | 59.792066 | 55.264608 | -7.57% |
| `evex512ri` | ABBA | 58.771552 | 57.109773 | -2.83% |
| `evex512ri` | BAAB | 60.330243 | 55.540273 | -7.94% |

Each bracket value is the geometric mean of its two process medians.
All 144 raw samples, sixteen output-checked processes and nineteen command receipts
passed the actual log audit. Drift remains material: in the `evex512ri`
ABBA bracket, the second control endpoint was 5.45% lower than the first,
and the second candidate endpoint was 3.17% lower. These observations do
not establish statistical significance or a ClickHouse improvement.
The revised caller-analysis SHA256 is
`a252b00d7f3ce9d16c6b7f05b66110e1573a855e5f0542ce662f4fce7e15bad1`.


## Fresh memcpy-only PG comparison

All eight fresh seed/stand cases completed with the original driver,
nine passes per case, fourteen traces and eighteen timed tables. The
actual audit checked nineteen completed commands, 2,016 finite raw-equal
CSV rows, 1,683 strict-validation records, both full-byte fixture oracles
and unchanged binary/archive/libc identities. There were no noop floors.
No initial-candidate case was reused for this revised binary.

| Trace | Profile | Original PG runtime change | Paired net median change |
|---|---|---:|---:|
| Mixed | `evex512i` | -0.0369% | +0.8783% |
| Mixed | `evex512ri` | +1.2038% | +0.0927% |
| Copy | `evex512i` | -0.3236% | -0.0532% |
| Copy | `evex512ri` | -0.0302% | -0.0915% |
| Move | `evex512i` | -0.1410% | +0.1882% |
| Move | `evex512ri` | -0.4307% | +0.2111% |

The original PG statistic takes the best net time across all eight cases
separately for each numerator and denominator. Paired values compare each
case directly before taking a median. Both subtract that case's noop
minimum from its minimum pass; a 0.01 floor is specified but was never used.
Gross ratios compare the recorded median times without noop subtraction.
These statistics are descriptive, with different min-selection biases;
they are not a significance test.

The restored move path no longer has the previous consistent 2–3% loss.
Its paired ranges are -0.5168%..+0.4921% for `evex512i` and
-0.7237%..+0.5943% for `evex512ri`. There is no broad PG gain to claim.
The `evex512ri` mixed paired result is adverse in all eight cases, with
a +0.0927% median and +0.0437%..+1.6779% range; its gross median is
+0.1105%. This small persistent observation is retained.

Controls also vary: the six unchanged profiles' mixed PG changes span
-0.9813%..+0.7489%, paired medians -2.1360%..+0.6895% and gross medians
-5.4382%..+0.2989%. The worst individual PG comparison is the unchanged
`evex512r` `strlen` trace at +1.2075%. The `evex512i` `strcspn_long`
trace, whose function was not changed, is adverse in all eight paired/gross comparisons, with +0.4842%
paired and +0.8247% gross medians; unchanged `sse42` copy is also adverse
in all eight, at +0.2873% paired and +0.2794% gross medians. These controls
do not erase the candidate's adverse observations.

The publication rationale is the targeted repeat-copy caller benefit,
with the rejected move change removed and the broad observations retained.
This benchmark forces ISA-compatible Intel profiles on Skylake-SP, calls
backend tables directly and caps individual memory sizes at 4 KiB. It
measures neither public dispatch overhead nor the larger map/subcolumn
copy regressions. Those application regressions remain unresolved.

The revised PG analysis SHA256 is
`912119cce95102a6d0cad62c6068b8cd20308ba43b927aaf2fd9240d0f8c941f`.
