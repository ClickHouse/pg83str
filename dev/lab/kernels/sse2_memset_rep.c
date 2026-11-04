/*
 * memset by rep stosb alone, as glibc's __memset_erms: on Zen 5 it won the
 * memset trace (r2: 9.3 ns a call against ours 11.0 at 2.0 GHz), where the
 * fills are short (median 29 bytes) and 80% zero. The microcode's start is
 * fast there (FSRM, fast short rep stosb); on Intel before Ice Lake it is not.
 */
#include <stddef.h>

void *kl_memset_sse2_rep(void *d, int c, size_t n) {
    void *p = d;
    __asm__ volatile("rep stosb" : "+D"(p), "+c"(n) : "a"(c) : "memory");
    return d;
}
