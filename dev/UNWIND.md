# Unwind metadata after postprocessing

Use `post.py --unwind` when the processed library must support stack unwinding.
The compiler objects must contain asynchronous unwind tables. Pass an explicit
optimization list without `frame`; removing a frame after compilation is not
supported in this mode. Unsupported compiler CFI, frame-changing rewrites or
inconsistent final control-flow states fail the command. No incomplete object
or alternative implementation is substituted.

The generator decodes the compiler's complete CIE/FDE rules, including saved
registers and remembered states. The layout records each emitted instruction
and its original instruction, including duplicated tails, configuration clones
and JCC twins. Preserved frame effects transfer the compiler's rules through
the final control-flow graph. Inserted or moved ordinary instructions inherit
the incoming state at their final location. Fresh FDEs use the final instruction
addresses; retaining the original `.eh_frame` PC advances would be incorrect.
The final graph must also preserve an original sequence of frame effects on
every path, including returns and tail calls. This catches omitted or reordered
pushes/pops even where the compiler keeps the CFA on `RBP` throughout them.
An external conditional tail branch contributes an exit action only on its
taken edge. Its fallthrough retains the incoming frame-effect history, including
when jump threading copies the external jump's relocation provenance onto the
conditional branch.

This mode does not modify instruction encoding, alignment, non-CFI relocations
or function symbols. It adds regenerated `.eh_frame` contents and relocations.
The supported ABI is ELF x86-64 with the SysV callee-saved general registers,
`RSP`/`RBP` CFA bases, saved-register CFA-offset/same-value rules and compiler CIE
augmentation `zR`. Register-to-register saves, expressions, exception
personality/LSDA data and other unsupported rule forms are rejected.

`tst/unwind.py` runs the same six compiler objects through both output modes,
checks all code/non-CFI payloads, independently decodes generated rows with
LLVM, and compares concrete recovery rules over every covered code byte. Its
negative controls reject changed frame instructions and malformed CFI. This
checks metadata encoding and the compiler-rule/control-flow model; it is not
an independent proof of compiler CFI or instruction semantics.

`tst/unwind_signal.c` provides a separate native asynchronous check. It runs
supported configurations through seven public-entry paths, records the actual
interrupted PC and recovered stack, and counts undelivered pending timer ticks
separately. `tst/analyze_unwind_signal.py` requires every observed provider PC
in the repaired executable to recover that PC and the expected caller. Link
the driver against the same LLVM libunwind used by the application. Sampled
PCs are runtime observations; the test does not claim every-PC runtime
coverage or replace an application profiler check.
