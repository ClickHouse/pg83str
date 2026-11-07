# Proofs on the machine code

The library's functions are proved correct on the linked machine code
(`./build proof` links `libstr.a` alone into `.build/proof/libstr.elf`; the
post pass's copies `<config>.<function>` are what each configuration runs),
not on the C: what the compiler and the post pass made is what is checked.

```
ix run lib/c bld/compiler bin/z3 bin/bitwuzla -- ./build proof        # the whole chain: every proof below
ix run lib/c bld/compiler bin/z3 bin/bitwuzla -- ./build --dist=root@lab1.mesh:20,root@lab2.mesh:25,root@lab3.mesh:18 -j 100 \
    -Dproof_workers=4 -Dproof_timeout=5400 strspn_avx2           # one function's proof (its 16 classes) on the lab
ix run lib/c bld/compiler bin/z3 bin/bitwuzla -- ./build strspn_avx2_3 strcat_sse2   # a class; a proof and its callees'
ix run lib/c bld/compiler -- ./build proof_elf             # .build/proof/libstr.elf, .build/proof/oracle
ix run bin/z3 bin/bitwuzla -- python3 dev/proof/prove.py --elf .build/proof/libstr.elf \
    --func avx2.memchr_avx2 --spec memchr --induct --align 49 [--verbose]
dev/proof/run.py --z3 .../bin/z3 --bitwuzla .../bin/bitwuzla --func avx2.memchr_avx2,evex.memchr_evex \
    --spec memchr --induct                                 # every alignment class, spread over the lab
dev/proof/run.py ... --func memchr:sse2.memchr_sse2,strlen:sse42.strlen_sse2 --induct --hosts lab1,lab3
python3 dev/proof/replay.py --func avx2.strspn_avx2 --spec strspn --n 2000
ix run bin/z3 -- python3 dev/proof/check_smt.py
```

`./build proof` is the chain (dev/proof/targets.py): a node per configuration's
copy of each function by its spec, `<spec>_<config>` (strspn_sse2), and per
class where the function is proved in classes, `<spec>_<config>_<k>`; a copy
whose code is another's (by run.py's fingerprint, checked when it runs)
takes that one's proof; strcat's node depends on the proofs of the strlen,
strcpy and strcpy_long its contracts stand for, and checks every callee is
one of them. Each node is dev/proof/node.py; its output, under
`.build/proof/`, the proof's RESULT line. Under `--dist` the proof nodes run
on the hosts only (the runner's `remote` nodes: with no host connected they
wait, so a host given as `root@labN.mesh`), z3 and bitwuzla sent there
as the proof_solvers node's outputs; -Dproof_workers, -Dproof_timeout,
-Dproof_memory are prove.py's --workers, PROVE_TIMEOUT and PROVE_MEMORY,
-Dproof_node_timeout the seconds a node may take before it fails. A host's
`:N` is how many proofs it runs at once, each of them -Dproof_workers + 1
solvers busy: lab1's 88 cores with 4 workers, `root@lab1.mesh:17`. The
longest proofs start first (targets.priority). Each running proof writes a
line a minute to ~/proof-status/<func>-<classes>-<align>.txt on its host
(PROVE_STATUS); dev/proof/report.py lists what .build/proof/ holds proved
and what not yet.

## How

`machine.py` executes the code symbolically: registers, vector lanes, mask
registers and flags are terms (`smt.py`), memory is arrays of bytes, one per
object (a region: the object's base and the memory around it), the stack one
more. A pointer the spec classes is an unknown base aligned to 64 (or to
what the code rounds to) plus its low bits, one alignment class per run:
the page position is then symbolic, every place in a page at once. The
classes are a case split, any number of them covers every pointer; one
class, the low bits symbolic too, is every pointer in one proof: `run.py`
tries that first (strlen sse2 in 8 s, strcmp avx2 in 1102 s, strspn
avx2's long sets in 1079 s), for three hours at most, the classes queued
behind every such try (the slots the tries leave take them; those still
queued when the try proves are dropped). At each
branch z3 decides which ways are feasible; every feasible path is followed.

Loops (`--induct`): the second time a path reaches a loop's head, the
registers that moved become their first value plus K (any multiple of the
step; any number for a loop whose pointers moved by what an inner loop
did), the rest unknown; the base case is proved at the first arrival, and
every way round from the general state either leaves (and is followed to
the return) or comes back to the head in the same form with K + D, the
invariant kept. Invariant candidates (bounds on the frontier, the bytes
checked ahead of it, relations of the registers the loop compares) are
kept or dropped Houdini's way; what another way round changes is widened.
Loops that run down (memmove backwards), loops in a row and nested loops
are handled; a writer's coverage is tracked from its stores. A loop is
generalized once per first arrival: another path that comes round to its
head from the same arrival is in that general state, whose every way round
and every way out are explored (strcmp sse2: 323 s to 24 s).

A loop that builds a table on the stack from strspn's set (a side loop:
its frontier in the set, not in s) has the table's invariant as a
candidate read off its first iteration's stores: for every byte x, its
entry (a byte table's v or 0, a bit table's bit) says whether x is one of
the set's bytes before the frontier. That one has a quantifier; its step is
proved without one (for any x and any truth p for "x one of the first n",
the entry after the iteration says p or x the n-th byte). A byte of the set
is a bit of M, 256 bits defined by one axiom; past the loop (the first load
of s) the invariant, its count L, is the table holding M, written out as 256
facts without a quantifier: the scan's queries go to bitwuzla.

strspn's and strcspn's sets of up to 32 bytes: a byte in the set when it
equals one of them, written as pcmpistri computes it, so that those paths'
terms and the spec's are the same. Past 2048 bytes the avx2 and evex code
tests bytes through truffle tables (a nibble's lane, the high nibble's
bit), terms the spec's are not: every byte matched again, 466 s an
obligation, unknown under load. A one-bit term of the path that reads one
byte b of s and no other, c(b), is taken at a free byte x and proved, once,
to say x in the set (or not) for every x; each such c(b) = member(b) then
goes with the path's checks, instances of that lemma. strspn avx2's first
178 obligations: 3591 s to 581 s, none over 46 s. A mutant whose truffle
table is wrong (one byte of its xor constant 0) is refuted in the loop's
step: the lemma does not hold for it, so no fact is added. A set of 17 to
32 bytes the avx2 code tests with pcmpistrm on its two halves of 16, each a
one-bit term of b ("equals a valid byte of this half") that is not the set
alone: their disjunction is, proved the same way; a mutant that ors the
halves' "not in it" masks where the code ands them is refuted there (the
answer not a match). The term layer
also keeps psrlw's bytes their own: (w >> 4) & 15 of a word's low byte no
longer reads its high byte.

The prover's Python is under 1% of a proof's time, the solvers the rest,
and about 80% of it goes to checks that do not steer the walk: a return's
obligations, an access's bounds, a loop's base and step cases. With
`--workers N` (PROVE_WORKERS) those go to N threads, each with solver
processes of its own, on a copy of the path's state, while the walk goes
on with its branch queries; the first failure stops the proof as before.
8 workers: strcmp sse2 21 to 9.5 s, strlen avx2 355 to 116 s, strcmp avx2
859 to 316 s, strcpy_long sse2 84 to 48 s.

PROVE_STATUS=dir has a proof write a line a minute to
dir/<func>-<classes>-<align>.txt: its time, the paths closed, the
obligations, the loops generalized, the ways each walk under way has
still to take, where it is, the checking threads' backlog.

`run.py` proves a configuration's copy of a function once: the same
instructions, branch targets, callees' names and constants read (and the
same tzcnt) are the same machine to the prover. It hands the jobs out as
the hosts' slots free up, the longest specs first.

A load's or a store's bounds are asked first on what the path says of the
addresses alone, its conditions that read memory (the bytes compared
before) left out: fewer hypotheses, so unsat holds for the whole path; the
whole path only when that is not unsat (strcmp avx2 1213 s to 1102 s). A
masked load whose lanes all lie in lane 0's page (a mask up to the page's
end) has lane 0's bounds, one address; else its lanes go all at once, one
at a time when that does not settle in 20 s (strcmp evex past three loops:
a query of 431 s, of 30 minutes on a loaded host, is 0.8 and 1.1 s so).

At a return the spec's obligations are proved for every input the path
admits, quantifier-free: the stop of a scan is a free variable z ("any
stop"), so a property for all stops is one query. "No match in [lo, hi)"
is written out over offsets from lo when the path bounds the span; past a
loop whose blocks start at an address rounded down those are not the
terms of the bytes it loaded, and the solver matched 960 of them against
the path's (strcmp evex: 554 s, unknown under load). Its last way is by
the loads: a match at one of the path's loaded bytes, as their own terms,
or at an offset outside them all (two queries, together the span; 20 s
there); a way that did not do in 130 s gets the full timeout after it.
A load whose bound needs the bytes compared before (a byte at a mask's
first set bit past strcmp evex's loops: 870 s, unknown under load) has
first a lemma proved the same ways: no stop below the page of its last
byte; the bound then follows from the addresses. strcpy's last store, its
end at the NUL a mask found, likewise: no NUL below its last byte. Both
when the full path has not settled the bound in 60 s.

## The specs

| spec | what is proved |
|------|----------------|
| `strlen` | the answer is the offset of the first NUL; every load in the pages of `[s, s + len]` (C11: the string may end at its NUL next to an unmapped page) |
| `memchr` | the first `(u8)c` in `[s, s + n)` or null; loads within the pages up to the match (C11 lets the object end at it) or of the n bytes; nothing read for n = 0 |
| `memset` | every byte of `[d, d + n)` written with `(u8)c`, nothing else written, the answer `d` |
| `memcpy` | every byte of `d` the corresponding byte of `s`, nothing else written, loads in the pages of `[s, s + n)`; the objects apart |
| `memmove` | the same with the objects overlapping in any way: one memory, every byte written the source's as it was before the call |
| `memcmp` | the sign (in fact the value) of the first differing byte pair, 0 without one; loads in the pages of both objects |
| `strcmp` | the difference of the first pair that differs or ends; loads of each string in its pages up to that byte |
| `strcpy` | the string with its NUL at `d`, nothing else written, the source read up to its NUL's page; the objects apart |
| `strcpy_long` | the kernel past strcpy's short path, under its precondition (the contract strcat relies on) |
| `strcat` | `d`'s bytes before its NUL kept, `s` with its NUL copied from there; the callees by their contracts |
| `strspn`, `strcspn` | the length of the prefix in (not in) the set; sets of up to 32 bytes |
| `strspn_long`, `strcspn_long` | the same for sets of more than 32 bytes, any length |

Every function and configuration is also checked for: aligned accesses
(`movaps`, `movdqa`, legacy SSE memory operands) aligned; callee-saved
registers and `rsp` restored; rep movs with no backward overlap.

## What the proofs rest on

- the solvers, bitwuzla for every query it reads and z3 for those with an
  array written as a lambda (each query's `unsat`; `unknown` is a failure,
  never a pass); bitwuzla is what makes the slow proofs go (6 to 50 times
  faster than z3 on their queries);
- the instruction semantics in `machine.py`, checked against the hardware by
  `replay.py` (random inputs run natively by the oracle and through the
  machine, results and memory compared);
- the term layer's rewrites (`smt.py`), checked by `check_smt.py` (random
  expressions folded and raw, z3 asked for a difference);
- the specs as written in `prove.py`;
- mutants of the code (patched instructions) are refuted with
  counterexamples: a proof that cannot fail proves nothing.

Limits: lengths are unbounded up to the 2^47 bytes of user space; strspn's
and strcspn's sets any length (up to 32 bytes written out byte by byte,
longer through the mask M and its axiom).
