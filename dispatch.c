/*
 * dispatch.c: CPU level selection and the public entry points.
 *
 * v1.c, v2.c, v3.c and v4.c are compiled with -march=x86-64, -v2, -v3 and
 * -v4 and each exports a table of its functions. A constructor checks cpuid
 * for the whole v2, v3 and v4 feature sets (the compiler may use any of them
 * in those files) and copies the matching table into str_table; the
 * public str_* functions tail-call through it, one indirect jump per call, as
 * glibc's IFUNC and asmlib do. str_table starts as v1, correct on every
 * x86-64, so a call before the constructor ran is safe. ERMS is not part of an
 * ISA level and goes to str_cpu_erms, read only by the large copy/fill kernels.
 *
 * The names are str_memcpy and so on; a libc build renames them with
 * -Dstr_memcpy=memcpy ... on the command line.
 */
#include "common.h"

struct str_table str_table = {
    v1_memcpy, v1_memmove, v1_memset, v1_memcmp, v1_strlen,
    v1_strcpy, v1_strcat, v1_strcmp, v1_strspn, v1_strcspn,
};

static inline void cpuid(unsigned leaf, unsigned sub, unsigned r[4])
{
    __asm__ volatile("cpuid" : "=a"(r[0]), "=b"(r[1]), "=c"(r[2]), "=d"(r[3]) : "a"(leaf), "c"(sub));
}

static inline int bit(unsigned word, unsigned i) { return (word >> i) & 1; }

__attribute__((constructor)) static void select_level(void)
{
    unsigned r[4];
    cpuid(0, 0, r);
    if (r[0] < 7)
        return;
    cpuid(1, 0, r);
    unsigned ecx1 = r[2];
    cpuid(7, 0, r);
    unsigned ebx7 = r[1];
    cpuid(0x80000001u, 0, r);
    unsigned ecx81 = r[2];
    str_cpu_erms = (unsigned char)bit(ebx7, 9);
    /* x86-64-v2: SSE3, SSSE3, SSE4.1, SSE4.2, POPCNT, CMPXCHG16B, LAHF/SAHF. */
    int v2 = bit(ecx1, 0) && bit(ecx1, 9) && bit(ecx1, 19) && bit(ecx1, 20) && bit(ecx1, 23) && bit(ecx1, 13) && bit(ecx81, 0);
    if (!v2)
        return;
    str_table = v2_table;
    /* x86-64-v3: AVX, AVX2, BMI1, BMI2, F16C, FMA, LZCNT, MOVBE, OSXSAVE with YMM state. */
    int v3 = bit(ecx1, 27) && bit(ecx1, 28) && bit(ecx1, 29) && bit(ecx1, 12) && bit(ecx1, 22)
          && bit(ebx7, 5) && bit(ebx7, 3) && bit(ebx7, 8) && bit(ecx81, 5);
    if (!v3)
        return;
    unsigned lo, hi;
    __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
    if ((lo & 0x06) != 0x06)
        return;
    /* x86-64-v4: AVX512F, BW, CD, DQ, VL with opmask and ZMM state. */
    int v4 = bit(ebx7, 16) && bit(ebx7, 30) && bit(ebx7, 28) && bit(ebx7, 17) && bit(ebx7, 31)
          && (lo & 0xe0) == 0xe0;
    str_table = v4 ? v4_table : v3_table;
}

void *str_memcpy(void *d, const void *s, size_t n) { return str_table.memcpy(d, s, n); }
void *str_memmove(void *d, const void *s, size_t n) { return str_table.memmove(d, s, n); }
void *str_memset(void *d, int c, size_t n) { return str_table.memset(d, c, n); }
int str_memcmp(const void *a, const void *b, size_t n) { return str_table.memcmp(a, b, n); }
size_t str_strlen(const char *s) { return str_table.strlen(s); }
char *str_strcpy(char *d, const char *s) { return str_table.strcpy(d, s); }
char *str_strcat(char *d, const char *s) { return str_table.strcat(d, s); }
int str_strcmp(const char *a, const char *b) { return str_table.strcmp(a, b); }
size_t str_strspn(const char *s, const char *set) { return str_table.strspn(s, set); }
size_t str_strcspn(const char *s, const char *set) { return str_table.strcspn(s, set); }
