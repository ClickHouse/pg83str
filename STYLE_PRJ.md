# Project style settings

Per-project settings that the shared [STYLE.md](STYLE.md) delegates here.

- **Macro prefix.** None reserved; the few macros (`NOINLINE`, `RETURN_OPAQUE`)
  are private to `common.h`.
- **Namespace.** None: the library is C. Its public functions are `str_memcpy`,
  `str_strlen` and so on, renamed to the libc names by the build that embeds
  them (`-Dstr_memcpy=memcpy ...`).
- **Formatter.** `./dev/style.py` applies the mechanical part of STYLE.md
  this project takes and nothing else: attached braces, braces around every
  control-statement body, no braced body on one line, and blank lines as the
  std library places them (none right after `{` or before `}`, never two,
  one after a block's closing brace, one before a control statement that
  follows other code, one between definitions). Pointer spacing, line length
  and the rest of `.clang-format` are not applied. `--check` is the CI gate.
  The Python under `dev/` and `tst/` is not formatted mechanically.

## Deviations

- The code is C, not C++, and follows the C library it replaces: functions,
  variables and types are `snake_case`; an implementation of a primitive is
  named by the primitive and the instruction set it uses as a suffix
  (`strlen_evex512`, `copy_large_avx2`), the slot of `str_table` it fills.
- `tst/test.c` is the test driver, not the product, and uses the host C
  library as the reference implementation.
- `dev/x86.py` and `dev/encode.py` are instruction tables: their data lines
  are long on purpose and are not wrapped.
