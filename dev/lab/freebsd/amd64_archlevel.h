/* Export all donor variants. The public alias selects this host's best
 * available donor level; SSE4.2 support is checked by the benchmark. */
#include <machine/asm.h>
#ifndef BENCH_VARIANT
#define BENCH_VARIANT baseline
#endif
#define VARIANT(name,level) JOIN(name,JOIN(_,level))
#define ARCHFUNCS(name) .globl name; .type name,@function; .set name,VARIANT(name,BENCH_VARIANT)
#define ARCHFUNC(name,level)
#define NOARCHFUNC
#define ENDARCHFUNCS(name)
#define ARCHENTRY(name,level) _START_ENTRY; .globl VARIANT(name,level); .type VARIANT(name,level),@function; VARIANT(name,level):; .cfi_startproc
#define ARCHEND(name,level) END(VARIANT(name,level))
