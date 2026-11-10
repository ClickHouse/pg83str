# Initial cached huge-kernel policy

The `evex` configuration uses cached 512-bit copies for
`4096 <= n < 262144` and cached 512-bit fills for `4096 <= n < 16384`.
The huge helpers use inline `rep movsb` or `rep stosb` at and above their
respective upper bounds. Other host configurations retain their existing
choices. The copy contract permits forward overlap and excludes backward
overlap; it saves both edges before the loop and writes the head last.
The helpers never delegate back through their own huge slots.

These are initial engineering cutoffs from one finite experiment, not a
claim that every application regression is fixed. The experiment completed
on 2026-11-10 with 316,800 observations: 22 sizes from 4096 bytes through
4 MiB, copy offset pairs from `{0, 1, 7, 63}`, the same destination offsets
and two fill values, three reuse/consumer regimes, ten implementations,
and 20 rounds balanced in both directions. All observations remain in the
analysis, including adverse cells. No favorable retry was selected.

The controls include existing REP, SSE2 cached and AVX2 kernels, the new
cached 512-bit kernels, five predeclared cached-to-REP cutoffs, and fresh
standalone code generation from the actual ClickHouse LLVM memory bitcode.
That LLVM control is not the exact inlined code in ClickHouse. The shared
host was not paused. There is no measured application call-size histogram.

The following descriptive changes compare median nanoseconds per call to
the same case's REP control. Each of the 22 sizes has equal weight within
each family and regime; all original offset/value cases within a size have
equal weight. The geometric summaries are not pooled into an application
score.

| Initial policy | Reused buffer | Immediate consumer | Rotating buffers |
|---|---:|---:|---:|
| Copy below 256 KiB | -1.95% | -3.86% | -6.74% |
| Fill below 16 KiB | -1.01% | -1.73% | -3.61% |

All-cached copy has roughly 25–43% adverse hot/consumer bands around
512 KiB–1 MiB. All-cached fill has roughly 14–22% adverse hot/consumer
bands around 32 KiB. These motivate separate conservative cutoffs.
SSE2 cached controls win rotating cases but lose substantially in hot
cases; neither they nor the new cached kernels dominate all regimes.

The chosen copy policy still has 116 of 1056 cells more than 5% slower
than REP; the fill policy has 85 of 528. These include 46 copy cells and
73 fill cells above the respective cutoffs, where the wrappers reach the
same REP body. Large adverse observations on those paths and their sample
variation prevent attributing every difference to the cached body or its
selector. The complete adverse rows must accompany any performance claim.

The consumer uses a real 128-bit load and a loaded-value address
dependency. Both copy and fill remain on row zero for the deterministic
data used here; two rows are possible addresses, not two observed working
rows. Rotating samples touch at least 64 MiB against a reported 33 MiB LLC,
without claiming isolated cache misses.

The private experimental source preceded the integrated atomic-dispatch
and CFI changes. Integrated full API/async contracts, the original PG mixed
trace against host glibc, and the original four SQL cases remain separate
validation requirements. This grid does not predict their gains.

Evidence identities: source packet `73b1cc10a74fb211c11c5860264642d873b9a55dc646e10f98f99151717dc2bd`,
raw CSV `f53984530b95191075fd7566400a754c25e9a58767bc7c842d33d4bb6257a45e`,
full analysis `30dcbf50cd0d2c57f7b38bb5b10397305c590be6f34006caaab6ea99552f7162`,
and declared weighting analysis `f7f1b4dc10a05d1f91589f21d3a7311578ace4fa7b7243f7ad952a18c79c06ba`.
