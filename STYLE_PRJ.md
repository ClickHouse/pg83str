# Project style settings

Per-project settings that the shared [STYLE.md](STYLE.md) delegates here.

- **Macro prefix.** None reserved; the few macros (`NOINLINE`, `RETURN_OPAQUE`)
  are private to `common.h`.
- **Namespace.** None: the library is C. Its public functions are `str_memcpy`,
  `str_strlen` and so on, renamed to the libc names by the build that embeds
  them (`-Dstr_memcpy=memcpy ...`).
- **Formatter.** `./dev/style.py` formats every tracked C source. The Python
  under `dev/` and `tst/` is not formatted mechanically.

## Deviations

- The code is C, not C++, and follows the C library it replaces: functions,
  variables and types are `snake_case`; an implementation of a primitive is
  named by the primitive and the instruction set it uses as a suffix
  (`strlen_evex512`, `copy_large_avx2`), the slot of `str_table` it fills.
- `tst/test.c` is the test driver, not the product, and uses the host C
  library as the reference implementation.
- `dev/x86.py` and `dev/encode.py` are instruction tables: their data lines
  are long on purpose and are not wrapped.
