/* Architecture-independent table, contracts, and public entry points. */
#ifndef STR_API_H
#define STR_API_H
#include <stddef.h>
#include <stdint.h>

typedef unsigned char u8;

#define NOINLINE __attribute__((noinline))
/* A condition the branch predictor cannot learn: a select (cmov), not a branch. */
#if defined(__has_builtin) && __has_builtin(__builtin_unpredictable)
#define UNPREDICTABLE(x) __builtin_unpredictable(x)
#else
#define UNPREDICTABLE(x) (x)
#endif
#define ALWAYS_INLINE static inline __attribute__((always_inline))
#ifdef __clang__
    #define NO_UNROLL _Pragma("clang loop unroll(disable)")
#else
    #define NO_UNROLL _Pragma("GCC unroll 1")
#endif

/* The one table of primitives: the public functions and the kernels they hand
   the long cases to. Every implementation does its own hot path and reaches
   any other primitive only through the str_* entry points below, which jump
   through this table, so dispatch.c can fill the
   slots from cpuid with any mix: a 64-byte first block can continue in a
   64-byte tail on one machine and in a 32-byte one on another. */
struct str_table {
    void *(*memcpy)(void *, const void *, size_t);
    void *(*memmove)(void *, const void *, size_t);
    void *(*memset)(void *, int, size_t);
    int (*memcmp)(const void *, const void *, size_t);
    size_t (*strlen)(const char *);
    char *(*strcpy)(char *, const char *);
    char *(*strcat)(char *, const char *);
    int (*strcmp)(const char *, const char *);
    size_t (*strspn)(const char *, const char *);
    size_t (*strcspn)(const char *, const char *);
    void *(*memchr)(const void *, int, size_t);
    /* kernels; the contracts every implementation keeps: */
    void *(*copy_large)(u8 *d, const u8 *s, size_t n);         /* n > 64, any overlap */
    void *(*fill_large)(u8 *d, int c, size_t n);               /* n > 128 */
    int (*memcmp_large)(const u8 *a, const u8 *b, size_t n);   /* n > 128, the first 64 bytes equal */
    size_t (*strlen_tail)(const u8 *a, const u8 *s);           /* a 16-aligned, s <= a, no NUL in [s, a) */
    int (*strcmp_tail)(const u8 *a, const u8 *b);              /* any pointers */
    void *(*strcpy_long)(u8 *d, const u8 *s, const u8 *a);     /* no NUL in [s, a), a 16-aligned and past s */
    void *(*copy_huge)(u8 *d, const u8 *s, size_t n);          /* n >= REP_THRESHOLD, no backward overlap */
    void *(*fill_huge)(u8 *d, int c, size_t n);                /* n >= REP_THRESHOLD */
    void *(*memchr_large)(const u8 *s, u8 c, size_t n);        /* n > 32, no match in the first 32 bytes; aligned loads only (C11) */
};

extern struct str_table str_table;

/* What the host runs well, as bits of str_host(); dispatch.c picks a
   configuration from them. STR_ZMM: 512-bit registers cost nothing extra. */
enum { STR_SSE42 = 1, STR_AVX2 = 2, STR_AVX512 = 4, STR_ZMM = 8, STR_ERMS = 16, STR_FSRS = 32, STR_NEON = 64 }; /* FSRS: a short rep stosb starts fast */
unsigned str_host(void);
/* The decisions are pure functions of a cpuid snapshot, so that the test can
   make them for any host on the one it runs on. */
struct str_cpuid {
    unsigned max_leaf;                             /* cpuid(0).eax */
    unsigned vendor_ebx, vendor_edx, vendor_ecx;   /* cpuid(0) */
    unsigned eax1, ecx1;                           /* cpuid(1) */
    unsigned ebx7;                                 /* cpuid(7, 0) */
    unsigned ecx81;                                /* cpuid(0x80000001) */
    unsigned xcr0;                                 /* xgetbv(0), 0 without OSXSAVE */
    unsigned eax71;                                /* cpuid(7, 1).eax */
    unsigned max_ext;                              /* cpuid(0x80000000).eax */
    unsigned eax8021;                              /* cpuid(0x80000021).eax, AMD */
};

void str_cpuid_read(struct str_cpuid *c);
unsigned str_host_of(const struct str_cpuid *c);
const struct str_config *str_choose_for(unsigned host);
/* The configurations dispatch.c chooses from, by name; the test runs every
   one whose needs the host meets. */
struct str_config {
    const char *name;
    unsigned needs;
    struct str_table table;
};

extern const struct str_config str_configs[];
extern const unsigned str_nconfigs;
const struct str_config *str_choose(void);   /* the configuration for this host */
void str_dispatch(void);                     /* str_table = str_choose()->table */

/* The entry points: each jumps through its slot of the table. Implementations
   call each other only through these. */
void *str_memcpy(void *, const void *, size_t);
void *str_memmove(void *, const void *, size_t);
void *str_memset(void *, int, size_t);
int str_memcmp(const void *, const void *, size_t);
size_t str_strlen(const char *);
char *str_strcpy(char *, const char *);
char *str_strcat(char *, const char *);
int str_strcmp(const char *, const char *);
size_t str_strspn(const char *, const char *);
size_t str_strcspn(const char *, const char *);
void *str_memchr(const void *, int, size_t);
void *str_copy_large(u8 *d, const u8 *s, size_t n);
void *str_fill_large(u8 *d, int c, size_t n);
int str_memcmp_large(const u8 *a, const u8 *b, size_t n);
size_t str_strlen_tail(const u8 *a, const u8 *s);
int str_strcmp_tail(const u8 *a, const u8 *b);
void *str_strcpy_long(u8 *d, const u8 *s, const u8 *a);
void *str_copy_huge(u8 *d, const u8 *s, size_t n);
void *str_fill_huge(u8 *d, int c, size_t n);
void *str_memchr_large(const u8 *s, u8 c, size_t n);

#endif
