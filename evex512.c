/*
 * evex512.c: first blocks with 512-bit registers, for the CPUs where zmm costs
 * nothing extra: Zen 4 and 5, Ice Lake and later (Skylake-SP stays at 256
 * bits: 512-bit instructions lower its clock, as glibc also avoids there).
 * Compile with -march=x86-64-v4 -mprefer-vector-width=512. The point is the
 * size distribution of real calls (median 8 bytes for strings, 14-29 for
 * memory blocks, a long tail): one 64-byte block settles 9 strings in 10, one
 * masked 64-byte load and store copies 90% of the blocks, with one branch on
 * the size instead of a tree of them. Longer inputs go through the table.
 */
#include "common.h"

static inline __mmask64 low_mask64(size_t n) { return (__mmask64)_bzhi_u64(~0ull, (unsigned)n); } /* n <= 64 */
static inline __m512i load512(const void *p) { return _mm512_loadu_si512(p); }
static inline __m512i load512a(const void *p) { return _mm512_load_si512(p); }
static inline void store512(void *p, __m512i v) { _mm512_storeu_si512(p, v); }
static inline void store512a(void *p, __m512i v) { _mm512_store_si512(p, v); }
static inline __m512i mload64(const void *p, __mmask64 k) { return _mm512_maskz_loadu_epi8(k, p); }
static inline void mstore64(void *p, __mmask64 k, __m512i v) { _mm512_mask_storeu_epi8(p, k, v); }
static inline unsigned long long zero_mask64(__m512i v) { return _mm512_testn_epi8_mask(v, v); }
static inline unsigned ctz64(unsigned long long m) { return (unsigned)__builtin_ctzll(m); }

/* ------------------------------------------------------------ NUL scans */

/* a is 16-aligned, s <= a, no NUL in [s, a). The 64-byte block around a is
   checked with the bytes before a masked off (they may lie before s); then
   single blocks up to a 256-byte boundary, then four at a time: a 256-aligned
   group never crosses a page. */
size_t strlen_tail_evex512(const u8 *a, const u8 *s)
{
    const u8 *start = align_down(a, 64);
    unsigned long long first = zero_mask64(load512a(start)) >> (a - start);
    if (first)
        return (size_t)(a - s) + ctz64(first);
    a = start + 64;
    while ((uintptr_t)a & 255) {
        unsigned long long m = zero_mask64(load512a(a));
        if (m)
            return (size_t)(a - s) + ctz64(m);
        a += 64;
    }
    for (;;) {
        __m512i x0 = load512a(a), x1 = load512a(a + 64), x2 = load512a(a + 128), x3 = load512a(a + 192);
        __m512i m = _mm512_min_epu8(_mm512_min_epu8(x0, x1), _mm512_min_epu8(x2, x3));
        if (_mm512_testn_epi8_mask(m, m)) {
            unsigned long long k = zero_mask64(x0);
            if (k) return (size_t)(a - s) + ctz64(k);
            k = zero_mask64(x1);
            if (k) return (size_t)(a - s) + 64 + ctz64(k);
            k = zero_mask64(x2);
            if (k) return (size_t)(a - s) + 128 + ctz64(k);
            return (size_t)(a - s) + 192 + ctz64(zero_mask64(x3));
        }
        a += 256;
    }
}

size_t strlen_evex512(const char *str)
{
    const u8 *s = (const u8 *)str;
    unsigned long long m;
    if (page_left(s) >= 64) {
        m = zero_mask64(load512(s));
    } else {
        /* too close to the page end for an unaligned load: the aligned block around s, shifted */
        m = zero_mask64(load512a(align_down(s, 64))) >> ((uintptr_t)s & 63);
    }
    if (m)
        return ctz64(m);
    return str_strlen_tail(align_down(s, 64) + 64, s);
}

/* ------------------------------------------------------------------ copy */

static inline void *copy(u8 *d, const u8 *s, size_t n)
{
    /* every load happens before any store: overlap-safe, so memmove is the same code */
    if (n <= 64) {
        __mmask64 k = low_mask64(n);
        mstore64(d, k, mload64(s, k));
        return d;
    }
    if (n <= 128) {
        __m512i x = load512(s), y = load512(s + n - 64);
        store512(d, x);
        store512(d + n - 64, y);
        return d;
    }
    if (n <= 256) {
        __m512i x0 = load512(s), x1 = load512(s + 64), y0 = load512(s + n - 128), y1 = load512(s + n - 64);
        store512(d, x0);
        store512(d + 64, x1);
        store512(d + n - 128, y0);
        store512(d + n - 64, y1);
        return d;
    }
    return str_copy_large(d, s, n);
}

void *memcpy_evex512(void *dst, const void *src, size_t n) { return copy((u8 *)dst, (const u8 *)src, n); }
void *memmove_evex512(void *dst, const void *src, size_t n) { return copy((u8 *)dst, (const u8 *)src, n); }

/* ------------------------------------------------------------------ fill */

/* n > 256. No vector argument (the library's convention: nothing vector is
   live across a call), the splat is remade. Four unaligned stores at each end,
   aligned 256-byte steps between; the loop is not unrolled (the compiler
   would, with a long prologue for a count that is usually 1-16). */
static inline void *fill_loop(u8 *d, int c, size_t n)
{
    __m512i v = _mm512_set1_epi8((char)c);
    store512(d, v);
    store512(d + 64, v);
    store512(d + 128, v);
    store512(d + 192, v);
    u8 *end = d + n - 256;
    u8 *dp = (u8 *)align_down(d + 256, 64);
    NO_UNROLL
    while (dp < end) {
        store512a(dp, v);
        store512a(dp + 64, v);
        store512a(dp + 128, v);
        store512a(dp + 192, v);
        dp += 256;
    }
    store512(end, v);
    store512(end + 64, v);
    store512(end + 128, v);
    store512(end + 192, v);
    RETURN_OPAQUE(d);
}

void *fill_large_evex512(u8 *d, int c, size_t n)
{
    if (n >= REP_THRESHOLD)
        return str_fill_huge(d, c, n);
    return fill_loop(d, c, n);
}

void *fill_huge_evex512(u8 *d, int c, size_t n) { return fill_loop(d, c, n); }

void *memset_evex512(void *dst, int c, size_t n)
{
    u8 *d = (u8 *)dst;
    __m512i v = _mm512_set1_epi8((char)c);
    if (n <= 64) {
        mstore64(d, low_mask64(n), v);
        return dst;
    }
    if (n <= 128) {
        store512(d, v);
        store512(d + n - 64, v);
        return dst;
    }
    if (n <= 256) {
        store512(d, v);
        store512(d + 64, v);
        store512(d + n - 128, v);
        store512(d + n - 64, v);
        return dst;
    }
    return str_fill_large(d, c, n);
}

/* --------------------------------------------------------------- compare */

int memcmp_evex512(const void *pa, const void *pb, size_t n)
{
    const u8 *a = (const u8 *)pa, *b = (const u8 *)pb;
    unsigned long long m;
    if (n <= 64) {
        __mmask64 k = low_mask64(n); /* n == 0 loads nothing */
        m = _mm512_mask_cmpneq_epu8_mask(k, mload64(a, k), mload64(b, k));
        return m ? byte_diff(a, b, ctz64(m)) : 0;
    }
    if (n <= 128) {
        m = _mm512_cmpneq_epu8_mask(load512(a), load512(b));
        if (m)
            return byte_diff(a, b, ctz64(m));
        m = _mm512_cmpneq_epu8_mask(load512(a + n - 64), load512(b + n - 64));
        return m ? byte_diff(a, b, n - 64 + ctz64(m)) : 0;
    }
    return str_memcmp_large(a, b, n);
}

