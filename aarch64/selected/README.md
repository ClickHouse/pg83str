# Selected Linux AArch64 provider

This directory packages the selected eleven-entry implementation developed
from the AArch64 workbench. It exports the prefixed `str_memcpy`,
`str_memmove`, `str_memset`, `str_memcmp`, `str_strlen`, `str_strcpy`,
`str_strcat`, `str_strcmp`, `str_strspn`, `str_strcspn` and `str_memchr`
entries. It does not implement the historical twenty-slot `str_table`
interface or replace that workbench's default configuration.

Include `aarch64/selected/str.h` for these eleven declarations, including
C++ linkage guards. The root `str.h` continues to describe the separate
workbench table and its additional slot entry points.

Do not link this archive together with the workbench archive: both define the
same eleven public names. Consumers that need libc names can rename those
eleven symbols after archiving, as the ClickHouse CMake integration does.
The prefixed archive keeps the system libc independent for validation.

## Build

Use Clang and an archiver supporting AArch64 ELF. No external Arm reference
checkout, CMake installation or x86 machine-code rewriting pass is required.

```sh
mkdir -p .build
CC=clang AR=llvm-ar ./build selected > .build/build_selected.log 2>&1
CC=clang AR=llvm-ar ./build selected-test > .build/test_selected.log 2>&1
```

The output under the build directory's `selected` subdirectory is
`libstr-prefixed.a`, `compile_commands.json` and `build.json`. The latter
records source, object and archive hashes, compiler versions and commands.
The selected target does not install or overwrite the workbench's
`libstr.a`. Existing x86 targets are unchanged.

Nine translation units target baseline ARMv8-A; five target scalable SVE.
The build keeps compiler unwind information and nonleaf frame chains.
Only `memory_copy.c`, `memory_fill.c`, `memory_search.c`,
`tiny_copy_sve.c` and `memory_search_sve.c` omit leaf frame bookkeeping,
with asynchronous unwind information. The recipe preserves 64-byte function
and 32-byte loop alignment. It disables builtin and loop-idiom calls back to
libc and does not perform the x86 object post-pass.

The standalone driver also supports cross compilation:

```sh
CC=clang AR=llvm-ar python3 dev/aarch64_selected.py build \
    --target aarch64-linux-gnu --out .build/selected-cross
```

Pass the intended toolchain and sysroot through `CC`, `AR`, `CPPFLAGS` and
`CFLAGS`; linker options for tests use `LDFLAGS`. Commands are argument lists,
not shell fragments, and execute from the library checkout root. Missing
tools, headers, unsupported flags and changed
inputs fail the operation. Rebuilding with a different compiler or flags
does not transfer historical object-code proofs or performance results.

## Validate

`selected-test` is declared only for a native build. It links
`tst/aarch64_selected.c` against the prefixed archive and independent host
libc with builtin substitution disabled and assertions enabled. The native
test covers all eleven public entries, overlapping moves, zero counts,
protected pages, return pointers and oversized early-match searches. A
second executable masks the SVE feature bit through a test-only
`getauxval` wrapper before constructors run. The production archive does
not contain that wrapper.

To request additional thread vector lengths explicitly, use the standalone
test action after building the archive:

```sh
CC=clang python3 dev/aarch64_selected.py test \
    --target aarch64-linux-gnu --out .build/selected-cross \
    --sve-vl 32 --sve-vl 48
```

This action requires native Linux AArch64. An unavailable requested vector
length fails; it is not counted as a passing test. Wider-vector runtime
checks are separate from the bounded machine-code proof, whose current
recorded scope covers VL16 and a required search stopping offset at most
64. These checks do not certify all vector lengths, the complete library,
or arbitrary consumers. See `provenance.json` and the recorded investigation
for the exact source and evidence history.
