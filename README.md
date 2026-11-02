# str

[![CI](https://github.com/pg83/str/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pg83/str/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Permissively licensed string and memory functions for x86-64: `memcpy`,
`memmove`, `memset`, `memcmp`, `strlen`, `strcpy`, `strcat`, `strcmp`,
`strspn`, `strcspn`. Written in C with the `immintrin.h` intrinsics, one
translation unit per ISA level, dispatched once by `cpuid`, and then
rewritten after the compiler by a small post-pass over the objects. Meant to
be linked into a static libc in place of its string routines.

## Layout

- `v1.c`, `v2.c`, `v3.c`, `v4.c` — the library at x86-64, x86-64-v2 (SSE4.2),
  x86-64-v3 (AVX2) and x86-64-v4 (AVX-512VL/BW). Each file is compiled with
  its own `-march` and exports a table of its functions. `common.h` holds the
  short paths (up to 64 bytes) and helpers they share.
- `dispatch.c` — a constructor checks the full feature set of each level and
  copies the right table into `str_table`; the public `str_memcpy` and friends
  jump through it. A libc build renames them: `-Dstr_memcpy=memcpy ...`.
- `dev/` — the post pass, Python only: `x86.py` decodes x86-64 (legacy,
  VEX, EVEX) into structured instructions, `opt.py` rewrites them,
  `encode.py` encodes them again from the same tables, `layout.py` lays the
  sections out (branch relaxation, alignment, relocations), `elf.py` and
  `elfout.py` read and write the objects and the archive, `post.py` drives
  it. No assembler, no `ar`.
- `tst/test.c` — the library against the host C library: all sizes and
  alignments, overlaps, guard pages, random sets, for every level the host
  supports and for the dispatched table. `tst/roundtrip.py` — the decoder and
  emitter against the compiler at -O0…-O3.

## Build

```sh
./build -j8 checks libstr      # .build/libstr.a, the test, the round-trip
cat .build/post/report.txt     # what the post pass changed
```

`CC` selects the compiler (clang or gcc); the objects are compiled
`-fno-pic`, so the post pass sees plain relocations. The pass itself needs
only Python 3.

## Post pass

The compiler cannot be asked for some things from C, so `dev/opt.py` does
them on the objects, with a control-flow graph and register liveness per
function:

- `rename` — def-use webs of vector registers that touch a `ymm` are moved
  to `ymm16-31` and `vzeroupper` is dropped: nothing dirties the upper halves
  of `ymm0-15` any more. Webs that only use `xmm` stay VEX-encoded.
- `fold` — a masked load whose only use is a masked compare becomes the
  compare's memory operand; the `kand`/`ktest` that follow are simplified
  from the fact that the result is a subset of the mask.
- `tail`, `hot`, `thread` — short `ret` tails are copied over the `jmp` that
  reached them, a conditional branch to such a tail is inverted so the tail
  falls through (short inputs carry most of the weight), a jump to a lone
  `jmp` goes to its target.
- `ktest` — where a block starts with `kmov k,r` and every way in ends with
  `kortest k,k; jcc`, the `kortest` becomes `kmov k,r; test r,r` (fused with
  the branch) and the block's `kmov` goes away.
- `lea` — `lea (a,b),c; add $imm,c` becomes one `lea`.

`post.py --roundtrip --check` without passes must reproduce every object
byte for byte (code, relocations, symbols); the CI does that for clang and
gcc at every optimization level.

## Results

Against glibc's own implementations, geometric mean of time per call over
the ten functions at sizes 5, 25, 125, … weighted 1/size: 0.85× on Zen 5,
0.82× on Skylake-SP, 0.82× on Broadwell-EP (lower is faster). The post pass
is worth 14% on `strlen` at 5 bytes on Zen 5 and 28% on Skylake-SP, 29% on
`memcmp` at 5 bytes and 12% on `strcpy` on Skylake-SP, and nothing slower
anywhere, measured A/B in one process with both copies aligned alike.

## License

MIT, see [LICENSE](LICENSE).
