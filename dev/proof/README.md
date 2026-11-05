# Proofs on the machine code

The library's functions are proved correct on the linked machine code
(`./build proof` links `libstr.a` alone into `.build/proof/libstr.elf`; the
post pass's copies `<config>.<function>` are what each configuration runs),
not on the C: what the compiler and the post pass made is what is checked.

```
ix run lib/c bld/compiler -- ./build proof                 # .build/proof/libstr.elf, .build/proof/oracle
ix run bin/z3 -- python3 dev/proof/prove.py --elf .build/proof/libstr.elf \
    --func avx2.memchr_avx2 --spec memchr --induct --align 49 [--verbose]
dev/proof/run.py --z3 $(ix path bin/z3)/bin/z3 --func avx2.memchr_avx2,evex.memchr_evex \
    --spec memchr --induct                                 # every alignment class, spread over the lab
python3 dev/proof/replay.py --func avx2.strspn_avx2 --spec strspn --n 2000
ix run bin/z3 -- python3 dev/proof/check_smt.py
```

## How

`machine.py` executes the code symbolically: registers, vector lanes, mask
registers and flags are terms (`smt.py`), memory is arrays of bytes, one per
object (a region: the object's base and the memory around it), the stack one
more. A pointer the spec classes is an unknown base aligned to 64 (or to
what the code rounds to) plus its low bits, one alignment class per run:
the page position is then symbolic, every place in a page at once. At each
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
are handled; a writer's coverage is tracked from its stores.

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

Every function and configuration is also checked for: aligned accesses
(`movaps`, `movdqa`, legacy SSE memory operands) aligned; callee-saved
registers and `rsp` restored; rep movs with no backward overlap.

## What the proofs rest on

- z3 (each query's `unsat`; `unknown` is a failure, never a pass);
- the instruction semantics in `machine.py`, checked against the hardware by
  `replay.py` (random inputs run natively by the oracle and through the
  machine, results and memory compared);
- the term layer's rewrites (`smt.py`), checked by `check_smt.py` (random
  expressions folded and raw, z3 asked for a difference);
- the specs as written in `prove.py`;
- mutants of the code (patched instructions) are refuted with
  counterexamples: a proof that cannot fail proves nothing.

Limits: lengths are unbounded up to the 2^47 bytes of user space; strspn and
strcspn sets of up to 32 bytes (longer sets take the table-building loop,
not proved yet).
