/*
 * common.h: shared helpers and short paths for the string library.
 *
 * Included by every implementation file. Everything here is SSE2 and compiles
 * under each file's own -march, so the same short path is emitted once per file.
 *
 * Memory safety rules, every function obeys them:
 *   - the page size is 4096 B; an aligned 16- or 32-byte load never crosses
 *     a page, so it may be issued whenever its first byte is readable;
 *   - an unaligned load is issued only after checking that it stays inside
 *     the page, or when it lies entirely inside [p, p+n);
 *   - groups of aligned loads are issued only when the whole group lies in
 *     one page (a 128-byte aligned group always does);
 *   - reads may extend past a NUL but never past the page that holds it;
 *   - stores never touch bytes outside [dst, dst+n).
 */
#ifndef STR_COMMON_H
#define STR_COMMON_H
#include <stddef.h>
#include <stdint.h>
#include <immintrin.h>

#define PAGE_SIZE 4096
#define PAGE_MASK (PAGE_SIZE - 1)

/* Copies and fills of at least REP_THRESHOLD bytes go to the copy_huge and
   fill_huge slots: rep movsb/stosb where dispatch.c found them fast (ERMS),
   the vector loops elsewhere; below it vector loops win on every host measured. */
#define REP_THRESHOLD 4096

#include "str.h"

/* sse2.c: every primitive, the baseline the table starts as */
void *memcpy_sse2(void *, const void *, size_t);
void *memmove_sse2(void *, const void *, size_t);
void *memset_sse2(void *, int, size_t);
int memcmp_sse2(const void *, const void *, size_t);
size_t strlen_sse2(const char *);
char *strcpy_sse2(char *, const char *);
char *strcat_sse2(char *, const char *);
int strcmp_sse2(const char *, const char *);
size_t strspn_sse2(const char *, const char *);
size_t strcspn_sse2(const char *, const char *);
void *memchr_sse2(const void *, int, size_t);
void *memchr_large_sse2(const u8 *s, u8 c, size_t n);
void *copy_large_sse2(u8 *d, const u8 *s, size_t n);
void *fill_large_sse2(u8 *d, int c, size_t n);
int memcmp_large_sse2(const u8 *a, const u8 *b, size_t n);
size_t strlen_tail_sse2(const u8 *a, const u8 *s);
int strcmp_tail_sse2(const u8 *a, const u8 *b);
void *strcpy_long_sse2(u8 *d, const u8 *s, const u8 *a);
void *copy_huge_sse2(u8 *d, const u8 *s, size_t n);
void *fill_huge_sse2(u8 *d, int c, size_t n);
void *copy_huge_rep(u8 *d, const u8 *s, size_t n);   /* rep movsb: the ERMS hosts */
void *fill_huge_rep(u8 *d, int c, size_t n);         /* rep stosb */
void *memset_rep(void *, int, size_t);                 /* rep stosb for every size */
size_t strspn_table_sse2(const u8 *s, const u8 *set);
size_t strcspn_table_sse2(const u8 *s, const u8 *set);
size_t find_byte_or_nul_sse2(const u8 *s, u8 c);
size_t skip_byte_sse2(const u8 *s, u8 c);
/* sse42.c */
size_t strspn_sse42(const char *, const char *);
size_t strcspn_sse42(const char *, const char *);
/* avx2.c */
void *memcpy_avx2(void *, const void *, size_t);
void *memmove_avx2(void *, const void *, size_t);
void *memset_avx2(void *, int, size_t);
int memcmp_avx2(const void *, const void *, size_t);
size_t strlen_avx2(const char *);
char *strcpy_avx2(char *, const char *);
char *strcat_avx2(char *, const char *);
int strcmp_avx2(const char *, const char *);
size_t strspn_avx2(const char *, const char *);
size_t strcspn_avx2(const char *, const char *);
size_t strspn_avx2_zen(const char *, const char *);   /* the shuffle scan's tables from 384 bytes, not 1.5 KiB */
size_t strcspn_avx2_zen(const char *, const char *);
void *memchr_avx2(const void *, int, size_t);
void *memchr_large_avx2(const u8 *s, u8 c, size_t n);
void *copy_large_avx2(u8 *d, const u8 *s, size_t n);
void *fill_large_avx2(u8 *d, int c, size_t n);
int memcmp_large_avx2(const u8 *a, const u8 *b, size_t n);
size_t strlen_tail_avx2(const u8 *a, const u8 *s);
int strcmp_tail_avx2(const u8 *a, const u8 *b);
void *strcpy_long_avx2(u8 *d, const u8 *s, const u8 *a);
void *copy_huge_avx2(u8 *d, const u8 *s, size_t n);           /* the loop below 32 KiB, rep movsb beyond */
/* evex.c: AVX-512 with 256-bit registers */
void *memcpy_evex(void *, const void *, size_t);
void *memmove_evex(void *, const void *, size_t);
void *memset_evex(void *, int, size_t);
int memcmp_evex(const void *, const void *, size_t);
size_t strlen_evex(const char *);
char *strcpy_evex(char *, const char *);
char *strcat_evex(char *, const char *);
int strcmp_evex(const char *, const char *);
int strcmp_tail_evex(const u8 *a, const u8 *b);
void *memchr_evex(const void *, int, size_t);
/* evex512.c: AVX-512 with 512-bit registers */
void *memcpy_evex512(void *, const void *, size_t);
void *memcpy_evex512_headtail(void *, const void *, size_t);
void *memmove_evex512(void *, const void *, size_t);
void *memset_evex512(void *, int, size_t);
void *copy_huge_cached_evex512(u8 *d, const u8 *s, size_t n);
void *fill_huge_cached_evex512(u8 *d, int c, size_t n);
int memcmp_evex512(const void *, const void *, size_t);
size_t strlen_evex512(const char *);
void *memchr_evex512(const void *, int, size_t);
void *fill_large_evex512(u8 *d, int c, size_t n);
size_t strlen_tail_evex512(const u8 *a, const u8 *s);
int strcmp_tail_evex512(const u8 *a, const u8 *b);
void *strcpy_long_evex512(u8 *d, const u8 *s, const u8 *a);

/* The same functions laid out by the post pass with the JCC-erratum padding
   (jcc-*.o, see dev/post.py), for Skylake-SP. */
void *memcpy_evex512_jcc(void *, const void *, size_t);
void *memmove_evex512_jcc(void *, const void *, size_t);
void *memset_evex512_jcc(void *, int, size_t);
int memcmp_evex512_jcc(const void *, const void *, size_t);
size_t strlen_evex512_jcc(const char *);
void *memcpy_evex_jcc(void *, const void *, size_t);
void *memmove_evex_jcc(void *, const void *, size_t);
void *memset_evex_jcc(void *, int, size_t);
int memcmp_evex_jcc(const void *, const void *, size_t);
size_t strlen_evex_jcc(const char *);
char *strcpy_evex_jcc(char *, const char *);
char *strcat_evex_jcc(char *, const char *);
int strcmp_evex_jcc(const char *, const char *);
int strcmp_tail_evex_jcc(const u8 *a, const u8 *b);
void *memchr_evex_jcc(const void *, int, size_t);
void *memchr_large_avx2_jcc(const u8 *s, u8 c, size_t n);
size_t strspn_avx2_jcc(const char *, const char *);
size_t strcspn_avx2_jcc(const char *, const char *);
size_t strspn_sse42_jcc(const char *, const char *);
size_t strcspn_sse42_jcc(const char *, const char *);
void *copy_large_avx2_jcc(u8 *d, const u8 *s, size_t n);
void *fill_large_avx2_jcc(u8 *d, int c, size_t n);
int memcmp_large_avx2_jcc(const u8 *a, const u8 *b, size_t n);
size_t strlen_tail_avx2_jcc(const u8 *a, const u8 *s);
int strcmp_tail_avx2_jcc(const u8 *a, const u8 *b);
void *strcpy_long_avx2_jcc(u8 *d, const u8 *s, const u8 *a);
void *copy_huge_rep_jcc(u8 *d, const u8 *s, size_t n);
void *copy_huge_avx2_jcc(u8 *d, const u8 *s, size_t n);
void *fill_huge_rep_jcc(u8 *d, int c, size_t n);
void *copy_huge_cached_evex512_jcc(u8 *d, const u8 *s, size_t n);
void *fill_huge_cached_evex512_jcc(u8 *d, int c, size_t n);

/* -------------------------------------------------------------- basics */

static inline size_t page_left(const void *p) {
    return PAGE_SIZE - ((uintptr_t)p & PAGE_MASK);
}

static inline const u8 *align_down(const void *p, size_t a) {
    return (const u8 *)((uintptr_t)p & ~(uintptr_t)(a - 1));
}

/* True when `bytes` bytes from both a and b stay inside their pages.
   Conservative: the OR can exceed the limit when each offset alone passes
   (a at 0x7c0 and b at 0x800, say). Fine for an entry's first block, whose
   fallback is a few bytes; a tail that compares byte by byte to the nearer
   page end on a miss must use the exact test, or that walk can be 2 KiB. */
static inline int both_in_page(const void *a, const void *b, size_t bytes) {
    return (((uintptr_t)a | (uintptr_t)b) & PAGE_MASK) <= PAGE_SIZE - bytes;
}

static inline int both_have(const void *a, const void *b, size_t bytes) {
    return page_left(a) >= bytes && page_left(b) >= bytes;
}

static inline __m128i load16(const void *p) {
    return _mm_loadu_si128((const __m128i *)p);
}

static inline __m128i load16a(const void *p) {
    return _mm_load_si128((const __m128i *)p);
}

static inline void store16(void *p, __m128i v) {
    _mm_storeu_si128((__m128i *)p, v);
}

static inline void store16a(void *p, __m128i v) {
    _mm_store_si128((__m128i *)p, v);
}

static inline uint64_t load64(const void *p) {
    uint64_t v;
    __builtin_memcpy(&v, p, 8);
    return v;
}

static inline void store64(void *p, uint64_t v) {
    __builtin_memcpy(p, &v, 8);
}

static inline uint32_t load32(const void *p) {
    uint32_t v;
    __builtin_memcpy(&v, p, 4);
    return v;
}

static inline void store32(void *p, uint32_t v) {
    __builtin_memcpy(p, &v, 4);
}

static inline uint16_t load16b(const void *p) {
    uint16_t v;
    __builtin_memcpy(&v, p, 2);
    return v;
}

static inline void store16b(void *p, uint16_t v) {
    __builtin_memcpy(p, &v, 2);
}

static inline unsigned mask16(__m128i v) {
    return (unsigned)_mm_movemask_epi8(v);
}

static inline unsigned zero_mask16(__m128i v) {
    return mask16(_mm_cmpeq_epi8(v, _mm_setzero_si128()));
}

static inline unsigned eq_mask16(__m128i a, __m128i b) {
    return mask16(_mm_cmpeq_epi8(a, b));
}

static inline unsigned ne_mask16(__m128i a, __m128i b) {
    return eq_mask16(a, b) ^ 0xffffu;
}

/* Bit i set when a[i] != b[i] or a[i] == 0. */
static inline unsigned diff_or_zero_mask16(__m128i a, __m128i b) {
    return ne_mask16(a, b) | zero_mask16(a);
}

static inline __m128i splat16(int c) {
    return _mm_set1_epi8((char)c);
}

static inline unsigned ctz(unsigned m) { return (unsigned)__builtin_ctz(m); } /* uncoverable: m != 0 by contract, the zero branch is the compiler's */
static inline int byte_diff(const u8 *a, const u8 *b, size_t i) {
    return (int)a[i] - (int)b[i];
}

/* memcmp's answer from the mask of the differing bytes of [0, n), 1 <= n <=
   64 (bit i: byte i differs; bits at n and beyond do not matter): the first
   difference, or byte n - 1 when there is none, which is equal then. No
   branch on the data: half the comparisons of a workload come out equal,
   and a branch on that is a coin toss (FreeBSD's memcmp does the same). */
static inline int memcmp_answer(const u8 *a, const u8 *b, uint64_t m, size_t n) {
    return byte_diff(a, b, (size_t)__builtin_ctzll(m | 1ull << (n - 1)));
}

/* 65..128 bytes: lo for bytes 0..63, hi for bytes n - 64..n - 1. */
static inline int memcmp_answer2(const u8 *a, const u8 *b, uint64_t lo, uint64_t hi, size_t n) {
    size_t lo_i = (size_t)__builtin_ctzll(lo | 1ull << 63), hi_i = n - 64 + (size_t)__builtin_ctzll(hi | 1ull << 63);
    return byte_diff(a, b, UNPREDICTABLE(lo != 0) ? lo_i : hi_i);
}

static inline void rep_movsb(u8 *d, const u8 *s, size_t n) {
    __asm__ volatile("rep movsb" : "+D"(d), "+S"(s), "+c"(n) : : "memory");
}

static inline void rep_stosb(u8 *d, int c, size_t n) {
    __asm__ volatile("rep stosb" : "+D"(d), "+c"(n) : "a"(c) : "memory");
}

static inline int overlap_backward(const u8 *d, const u8 *s, size_t n) {
    return d > s && d < s + n;
}

/* Hide "returns its argument" from the compiler: otherwise it reuses the
   argument after a call instead of the result, which costs a saved register
   and prevents the wrappers from tail-calling the kernels. */
#define RETURN_OPAQUE(p) do { void *r_ = (p); __asm__("" : "+r"(r_)); return r_; } while (0)

/* ------------------------------------------------------- short copy/fill */

/* n <= 32. Every load happens before every store, so overlap is fine. */
static inline void copy_small(u8 *d, const u8 *s, size_t n) {
    if (n >= 16) {
        __m128i x = load16(s), y = load16(s + n - 16);
        store16(d, x);
        store16(d + n - 16, y);
    } else if (n >= 8) {
        uint64_t x = load64(s), y = load64(s + n - 8);
        store64(d, x);
        store64(d + n - 8, y);
    } else if (n >= 4) {
        uint32_t x = load32(s), y = load32(s + n - 4);
        store32(d, x);
        store32(d + n - 4, y);
    } else if (n >= 2) {
        uint16_t x = load16b(s);
        u8 y = s[n - 1];
        store16b(d, x);
        d[n - 1] = y;
    } else if (n) {
        d[0] = s[0];
    }
}

/* 33..64: all loads first, then all stores, overlap-safe. */
static inline void copy_33_64(u8 *d, const u8 *s, size_t n) {
    __m128i x0 = load16(s), x1 = load16(s + 16), x2 = load16(s + n - 32), x3 = load16(s + n - 16);
    store16(d, x0);
    store16(d + 16, x1);
    store16(d + n - 32, x2);
    store16(d + n - 16, x3);
}

/* n <= 32. The 8- and 4-byte stores go straight from the vector, 2..3
   bytes as three byte stores of c (glibc's): no move to a general register
   first. Neither Broadwell's fixed-size matrix nor the traces told it from
   the move (memset of 1..3 bytes 1.4-1.6x glibc either way in the matrix:
   the size branches taken there, where glibc's fall through, cost that;
   with them laid out as glibc's the matrix was even at 0..7 bytes and the
   16..32-byte path 1.27x slower, the traces the same). */
static inline void fill_small(u8 *d, __m128i v, int c, size_t n) {
    if (n >= 16) {
        store16(d, v);
        store16(d + n - 16, v);
    } else if (n >= 8) {
        _mm_storel_epi64((__m128i *)d, v);
        _mm_storel_epi64((__m128i *)(d + n - 8), v);
    } else if (n >= 4) {
        _mm_storeu_si32(d, v);
        _mm_storeu_si32(d + n - 4, v);
    } else if (n >= 2) {
        d[0] = (u8)c;
        d[1] = (u8)c;
        d[n - 1] = (u8)c;
    } else if (n) {
        d[0] = (u8)c;
    }
}

/* 33..64. */
static inline void fill_33_64(u8 *d, __m128i v, size_t n) {
    store16(d, v);
    store16(d + 16, v);
    store16(d + n - 32, v);
    store16(d + n - 16, v);
}

/* --------------------------------------------------------- short compare */

/* n <= 16. Big-endian words from both ends preserve unsigned byte ordering
   without a masked vector load or reading beyond the object. */
static inline int memcmp_scalar_le16(const u8 *a, const u8 *b, size_t n)
{
    if (n >= 8)
    {
        uint64_t x = __builtin_bswap64(load64(a));
        uint64_t y = __builtin_bswap64(load64(b));
        if (x != y)
        {
            return (x > y) - (x < y);
        }
        x = __builtin_bswap64(load64(a + n - 8));
        y = __builtin_bswap64(load64(b + n - 8));
        return (x > y) - (x < y);
    }
    if (n >= 4)
    {
        uint32_t x = __builtin_bswap32(load32(a));
        uint32_t y = __builtin_bswap32(load32(b));
        if (x != y)
        {
            return (x > y) - (x < y);
        }
        x = __builtin_bswap32(load32(a + n - 4));
        y = __builtin_bswap32(load32(b + n - 4));
        return (x > y) - (x < y);
    }
    if (n >= 2)
    {
        int x = __builtin_bswap16(load16b(a));
        int y = __builtin_bswap16(load16b(b));
        if (n == 2 || x != y)
        {
            return x - y;
        }
        return (int)a[2] - b[2];
    }
    if (n)
    {
        return (int)a[0] - b[0];
    }
    return 0;
}


/* n <= 16. A 16-byte load is taken only when both pointers have room in
   their pages; bit 16 bounds the search without a variable shift. */
static inline int memcmp_le16(const u8 *a, const u8 *b, size_t n) {
    if (n == 0) {
        return 0;
    }

    if (both_in_page(a, b, 16)) {
        return memcmp_answer(a, b, ne_mask16(load16(a), load16(b)), n);
    }

    for (size_t i = 0; i < n; i++) {
        if (a[i] != b[i]) {
            return byte_diff(a, b, i);
        }
    }

    return 0;
}

/* 17..32: two overlapping blocks, their masks in one at their positions. */
static inline int memcmp_17_32(const u8 *a, const u8 *b, size_t n) {
    uint64_t m = ne_mask16(load16(a), load16(b)) | (uint64_t)ne_mask16(load16(a + n - 16), load16(b + n - 16)) << (n - 16);
    return memcmp_answer(a, b, m, n);
}

/* 33..64: four blocks, two from each end. */
static inline int memcmp_33_64(const u8 *a, const u8 *b, size_t n) {
    uint64_t m = ne_mask16(load16(a), load16(b)) | (uint64_t)ne_mask16(load16(a + 16), load16(b + 16)) << 16 |
                 (uint64_t)ne_mask16(load16(a + n - 32), load16(b + n - 32)) << (n - 32) |
                 (uint64_t)ne_mask16(load16(a + n - 16), load16(b + n - 16)) << (n - 16);
    return memcmp_answer(a, b, m, n);
}

/* First 16 bytes of strcmp when both strings have room in their pages.
   Returns 1 with the answer in *result, else 0 with the pointers advanced. */
static inline int strcmp_block16(const u8 **pa, const u8 **pb, int *result) {
    const u8 *a = *pa, *b = *pb;

    if (!both_in_page(a, b, 16)) {
        return 0;
    }

    unsigned m = diff_or_zero_mask16(load16(a), load16(b));

    if (m) {
        *result = byte_diff(a, b, ctz(m));
        return 1;
    }

    *pa = a + 16;
    *pb = b + 16;
    return 0;
}

/* Compare up to the nearer page end, eight bytes at a time (x ^ y marks the
   differing bytes, (x - 1..) & ~x & 0x80.. the first NUL exactly, the
   lowest set bit of either is the first stop), the rest byte by byte.
   Returns 1 with the answer in *result, or 0 with both pointers advanced to
   the page boundary. */
static inline int strcmp_to_page_end(const u8 **pa, const u8 **pb, int *result) {
    const u8 *a = *pa, *b = *pb;
    size_t n = page_left(a), k = page_left(b);

    if (n > k) {
        n = k;
    }

    size_t i = 0;

    for (; i + 8 <= n; i += 8) {
        uint64_t x = load64(a + i), y = load64(b + i);
        uint64_t stop = ((x - 0x0101010101010101ull) & ~x & 0x8080808080808080ull) | (x ^ y);

        if (stop) {
            size_t j = i + (size_t)__builtin_ctzll(stop) / 8; /* uncoverable: stop != 0, the zero branch is the compiler's */
            *result = byte_diff(a, b, j); /* 0 when both are NUL */
            return 1;
        }
    }

    for (; i < n; i++) {
        if (a[i] != b[i]) {
            *result = byte_diff(a, b, i);
            return 1;
        }

        if (!a[i]) {
            *result = 0;
            return 1;
        }
    }

    *pa = a + n;
    *pb = b + n;
    return 0;
}

/* ------------------------------------------------------------- short NUL */

/* First 64 bytes of strlen with aligned blocks; the first block is aligned
   down to cover s and its mask is shifted by the misalignment. Returns the
   length, or SIZE_MAX with *next = the 16-aligned address 64 bytes on. */
static inline size_t strlen_prefix64(const u8 *s, const u8 **next) {
    const u8 *a = align_down(s, 16);
    unsigned m = zero_mask16(load16a(a)) >> ((uintptr_t)s & 15);

    if (m) {
        return ctz(m);
    }

    for (int k = 1; k < 4; k++) {
        a += 16;
        m = zero_mask16(load16a(a));

        if (m) {
            return (size_t)(a - s) + ctz(m);
        }
    }

    *next = a + 16;
    return SIZE_MAX;
}

/* ---------------------------------------------------------- long strcpy */

/* One pass over a string that has no NUL before the 16-byte boundary after s
   (the caller checked at least that much). Each aligned source block is tested

   for NUL before the previous one is stored, and the last 16 bytes of the
   string are copied as one overlapping pair, as in FreeBSD's stpcpy. */
static inline void *strcpy_long16(u8 *d, const u8 *s) {
    const u8 *a = align_down(s, 16) + 16;
    __m128i next = load16a(a); /* aligned: never crosses a page */
    unsigned m = zero_mask16(next);

    if (m) {
        copy_small(d, s, (size_t)(a - s) + ctz(m) + 1); /* at most 32 bytes */
        RETURN_OPAQUE(d);
    }

    store16(d, load16(s)); /* [s, s+16) lies within the two blocks just checked */
    size_t off = (size_t)(a - s);

    for (;;) {
        __m128i block = next;
        a += 16;
        next = load16a(a);
        m = zero_mask16(next);
        store16(d + off, block);
        off += 16;

        if (m) {
            break;
        }
    }

    const u8 *nul = a + ctz(m);
    store16(d + (nul - s) - 15, load16(nul - 15)); /* the string is longer than 16 here */
    RETURN_OPAQUE(d);
}

#endif
