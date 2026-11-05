# Proofs on the machine code

The library's functions are proved correct on the linked machine code
(`./build proof` links `libstr.a` alone into `.build/proof/libstr.elf`; the
post pass's copies `<config>.<function>` are what each configuration runs),
not on the C: what the compiler and the post pass made is what is checked.

```
ix run lib/c bld/compiler -- ./build proof                 # .build/proof/libstr.elf, .build/proof/oracle
ix run bin/z3 bin/bitwuzla -- python3 dev/proof/prove.py --elf .build/proof/libstr.elf \
    --func avx2.memchr_avx2 --spec memchr --induct --align 49 [--verbose]
dev/proof/run.py --z3 .../bin/z3 --bitwuzla .../bin/bitwuzla --func avx2.memchr_avx2,evex.memchr_evex \
    --spec memchr --induct                                 # every alignment class, spread over the lab
dev/proof/run.py ... --func memchr:sse2.memchr_sse2,strlen:sse42.strlen_sse2 --induct --hosts lab1,lab3
python3 dev/proof/replay.py --func avx2.strspn_avx2 --spec strspn --n 2000
ix run bin/z3 -- python3 dev/proof/check_smt.py
```

## How

`machine.py` executes the code symbolically: registers, vector lanes, mask
registers and flags are terms (`smt.py`), memory is arrays of bytes, one per
object (a region: the object's base and the memory around it), the stack one
more. A pointer the spec classes is an unknown base aligned to 64 (or to
what the code rounds to) plus its low bits, one alignment class per run:
the page position is then symbolic, every place in a page at once. The
classes are a case split, any number of them covers every pointer; one
class, the low bits symbolic too, is every pointer in one proof: `run.py`
tries that first (strlen sse2 in 8 s, strcmp avx2 in 2369 s where 64
classes take 1041 s each, strspn avx2's long sets in 1079 s) and splits
into the classes only when it does not prove in three hours. At each
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

`run.py` proves a configuration's copy of a function once: the same
instructions, branch targets, callees' names and constants read (and the
same tzcnt) are the same machine to the prover. It hands the jobs out as
the hosts' slots free up, the longest specs first.

At a return the spec's obligations are proved for every input the path
admits, quantifier-free: the stop of a scan is a free variable z ("any
stop"), so a property for all stops is one query.

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
