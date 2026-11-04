/* Minimal Linux ELF glue; donor algorithms are compiled without edits. */
#ifndef IX_BENCH_ASM_H
#define IX_BENCH_ASM_H
#define CNAME(name) name
#define JOIN_RAW(a,b) a##b
#define JOIN(a,b) JOIN_RAW(a,b)
#define _START_ENTRY .text; .p2align 4,0x90
#define ENTRY(name) _START_ENTRY; .globl name; .type name,@function; name:; .cfi_startproc
#define END(name) .size name,.-name; .cfi_endproc
#define RCSID(text)
#define WEAK_REFERENCE(name,alias) .weak alias; .set alias,name
#endif
