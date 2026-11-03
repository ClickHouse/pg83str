/*
 * avx2.c: the primitives for x86-64-v3 (AVX2, BMI2, SSE4.2 and friends).
 *
 * Compile with -march=x86-64-v3. Short paths come from common.h; inputs
 * longer than 64 bytes go through the table to the kernels, whose 32-byte
 * versions are defined here and serve the AVX-512 first blocks as well.
 */
#include "common.h"

static inline __m256i load256(const void *p) { return _mm256_loadu_si256((const __m256i *)p); }
static inline __m256i load256a(const void *p) { return _mm256_load_si256((const __m256i *)p); }
static inline void store256(void *p, __m256i v) { _mm256_storeu_si256((__m256i *)p, v); }
static inline void store256a(void *p, __m256i v) { _mm256_store_si256((__m256i *)p, v); }
static inline unsigned mask256(__m256i v) { return (unsigned)_mm256_movemask_epi8(v); }
static inline __m256i eq_zero(__m256i v) { return _mm256_cmpeq_epi8(v, _mm256_setzero_si256()); }

/* ------------------------------------------------------------ NUL scans */

/* a is 16-aligned, s <= a, no NUL in [s, a). The 32-byte block around a is
   checked with the bytes before a masked off (they may lie before s); then
   single 32-byte blocks up to a 128-byte boundary, then four per iteration:
   a 128-aligned group never crosses a page. */
size_t strlen_tail_avx2(const u8 *a, const u8 *s)
{
    const u8 *start = align_down(a, 32);
    unsigned first = mask256(eq_zero(load256a(start))) >> (a - start);
    if (first)
        return (size_t)(a - s) + ctz(first);
    a = start + 32;
    while ((uintptr_t)a & 127) {
        unsigned m = mask256(eq_zero(load256a(a)));
        if (m)
            return (size_t)(a - s) + ctz(m);
        a += 32;
    }
    for (;;) {
        __m256i x0 = eq_zero(load256a(a)), x1 = eq_zero(load256a(a + 32));
        __m256i x2 = eq_zero(load256a(a + 64)), x3 = eq_zero(load256a(a + 96));
        if (mask256(_mm256_or_si256(_mm256_or_si256(x0, x1), _mm256_or_si256(x2, x3)))) {
            unsigned m = mask256(x0);
            if (m) return (size_t)(a - s) + ctz(m);
            m = mask256(x1);
            if (m) return (size_t)(a - s) + 32 + ctz(m);
            m = mask256(x2);
            if (m) return (size_t)(a - s) + 64 + ctz(m);
            return (size_t)(a - s) + 96 + ctz(mask256(x3));
        }
        a += 128;
    }
}

/* One 32-byte block at s when the page allows (all 32 bytes count: 9 strings
   in 10 end there, median 8 bytes); else the aligned block around s, which
   never crosses a page, with the bytes before s shifted out. Either way the
   next aligned block is where the tail continues. */
size_t strlen_avx2(const char *str)
{
    const u8 *s = (const u8 *)str;
    unsigned m;
    if (page_left(s) >= 32)
        m = mask256(eq_zero(load256(s)));
    else
        m = mask256(eq_zero(load256a(align_down(s, 32)))) >> ((uintptr_t)s & 31);
    if (m)
        return ctz(m);
    return str_strlen_tail(align_down(s + 32, 32), s);
}

/* ------------------------------------------------------------------ copy */

/* n > 64. Head and tail vectors are loaded first and stored last, so an
   aligned loop may clobber source bytes only after they were consumed. */
void *copy_large_avx2(u8 *d, const u8 *s, size_t n)
{
    if (n <= 128) {
        __m256i x0 = load256(s), x1 = load256(s + 32), y0 = load256(s + n - 64), y1 = load256(s + n - 32);
        store256(d, x0);
        store256(d + 32, x1);
        store256(d + n - 64, y0);
        store256(d + n - 32, y1);
        RETURN_OPAQUE(d);
    }
    if (n <= 256) {
        __m256i x0 = load256(s), x1 = load256(s + 32), x2 = load256(s + 64), x3 = load256(s + 96);
        __m256i y0 = load256(s + n - 128), y1 = load256(s + n - 96), y2 = load256(s + n - 64), y3 = load256(s + n - 32);
        store256(d, x0);
        store256(d + 32, x1);
        store256(d + 64, x2);
        store256(d + 96, x3);
        store256(d + n - 128, y0);
        store256(d + n - 96, y1);
        store256(d + n - 64, y2);
        store256(d + n - 32, y3);
        RETURN_OPAQUE(d);
    }
    __m256i head = load256(s), tail = load256(s + n - 32);
    if (overlap_backward(d, s, n)) {
        /* down from the aligned end */
        size_t off = n - ((uintptr_t)(d + n) & 31);
        while (off >= 128) {
            off -= 128;
            __m256i x0 = load256(s + off), x1 = load256(s + off + 32), x2 = load256(s + off + 64), x3 = load256(s + off + 96);
            store256a(d + off, x0);
            store256a(d + off + 32, x1);
            store256a(d + off + 64, x2);
            store256a(d + off + 96, x3);
        }
        while (off >= 32) {
            off -= 32;
            store256a(d + off, load256(s + off));
        }
    } else if (n >= REP_THRESHOLD && str_cpu_erms) {
        rep_movsb(d, s, n);
        RETURN_OPAQUE(d);
    } else {
        size_t skew = 32 - ((uintptr_t)d & 31);
        u8 *dp = d + skew;
        const u8 *sp = s + skew;
        size_t left = n - skew;
        while (left >= 128) {
            __m256i x0 = load256(sp), x1 = load256(sp + 32), x2 = load256(sp + 64), x3 = load256(sp + 96);
            store256a(dp, x0);
            store256a(dp + 32, x1);
            store256a(dp + 64, x2);
            store256a(dp + 96, x3);
            dp += 128;
            sp += 128;
            left -= 128;
        }
        while (left >= 32) {
            store256a(dp, load256(sp));
            dp += 32;
            sp += 32;
            left -= 32;
        }
    }
    store256(d, head);
    store256(d + n - 32, tail);
    RETURN_OPAQUE(d);
}

static inline void *copy(void *dst, const void *src, size_t n)
{
    u8 *d = (u8 *)dst;
    const u8 *s = (const u8 *)src;
    if (n <= 32)
        copy_small(d, s, n);
    else if (n <= 64)
        copy_33_64(d, s, n);
    else
        return str_copy_large(d, s, n);
    return dst;
}

void *memcpy_avx2(void *dst, const void *src, size_t n) { return copy(dst, src, n); }
void *memmove_avx2(void *dst, const void *src, size_t n) { return copy(dst, src, n); }

/* ------------------------------------------------------------------ fill */

/* n > 64. */
void *fill_large_avx2(u8 *d, int c, size_t n)
{
    __m256i v = _mm256_set1_epi8((char)c);
    if (n <= 128) {
        store256(d, v);
        store256(d + 32, v);
        store256(d + n - 64, v);
        store256(d + n - 32, v);
        RETURN_OPAQUE(d);
    }
    if (n <= 256) {
        store256(d, v);
        store256(d + 32, v);
        store256(d + 64, v);
        store256(d + 96, v);
        store256(d + n - 128, v);
        store256(d + n - 96, v);
        store256(d + n - 64, v);
        store256(d + n - 32, v);
        RETURN_OPAQUE(d);
    }
    if (n >= REP_THRESHOLD && str_cpu_erms) {
        rep_stosb(d, c, n);
        RETURN_OPAQUE(d);
    }
    /* four unaligned stores at each end, aligned 128-byte steps between; the loop
       is not unrolled (the compiler would, with a long prologue for a count that
       is usually small) */
    store256(d, v);
    store256(d + 32, v);
    store256(d + 64, v);
    store256(d + 96, v);
    u8 *end = d + n - 128;
    u8 *dp = (u8 *)align_down(d + 128, 32);
    NO_UNROLL
    while (dp < end) {
        store256a(dp, v);
        store256a(dp + 32, v);
        store256a(dp + 64, v);
        store256a(dp + 96, v);
        dp += 128;
    }
    store256(end, v);
    store256(end + 32, v);
    store256(end + 64, v);
    store256(end + 96, v);
    RETURN_OPAQUE(d);
}

void *memset_avx2(void *dst, int c, size_t n)
{
    u8 *d = (u8 *)dst;
    if (n <= 32)
        fill_small(d, splat16(c), n);
    else if (n <= 64)
        fill_33_64(d, splat16(c), n);
    else
        return str_fill_large(d, c, n);
    return dst;
}

/* --------------------------------------------------------------- compare */

/* Two 32-byte blocks at o1 < o2 (they may overlap): 1 with the answer in
   *result when they differ, else 0. */
static inline int pair_differs(const u8 *a, const u8 *b, size_t o1, size_t o2, int *result)
{
    __m256i e0 = _mm256_cmpeq_epi8(load256(a + o1), load256(b + o1));
    __m256i e1 = _mm256_cmpeq_epi8(load256(a + o2), load256(b + o2));
    if (mask256(_mm256_and_si256(e0, e1)) == 0xffffffffu)
        return 0;
    unsigned m = mask256(e0) ^ 0xffffffffu;
    *result = m ? byte_diff(a, b, o1 + ctz(m)) : byte_diff(a, b, o2 + ctz(mask256(e1) ^ 0xffffffffu));
    return 1;
}

/* n > 64: pairs of 32-byte blocks while more than 128 bytes remain, then at
   most two pairs: one at the current position when more than 64 bytes are
   left, and one covering the last 64 bytes. Overlap is harmless: bytes before
   a block's start were already verified, so the first mismatch is found in
   address order. */
int memcmp_large_avx2(const u8 *a, const u8 *b, size_t n)
{
    int result;
    size_t i = 0;
    while (n - i > 128) {
        if (pair_differs(a, b, i, i + 32, &result))
            return result;
        i += 64;
    }
    if (n - i > 64 && pair_differs(a, b, i, i + 32, &result))
        return result;
    if (pair_differs(a, b, n - 64, n - 32, &result))
        return result;
    return 0;
}

/* n <= 32 in one 32-byte compare when both pointers have the room in their
   pages: eq + 1 sets the bit of the first differing lane (all equal wraps to
   0), bzhi drops the lanes at n and beyond. No split at 16, no test for 0.
   33..64: the first and the last 32 bytes. */
int memcmp_avx2(const void *pa, const void *pb, size_t n)
{
    const u8 *a = (const u8 *)pa, *b = (const u8 *)pb;
    unsigned m;
    if (n <= 32) {
        if (__builtin_expect(both_in_page(a, b, 32), 1)) {
            m = _bzhi_u32(mask256(_mm256_cmpeq_epi8(load256(a), load256(b))) + 1, (unsigned)n);
            return m ? byte_diff(a, b, ctz(m)) : 0;
        }
        return n <= 16 ? memcmp_le16(a, b, n) : memcmp_17_32(a, b, n);
    }
    if (n <= 64) {
        m = mask256(_mm256_cmpeq_epi8(load256(a), load256(b))) + 1;
        if (m)
            return byte_diff(a, b, ctz(m));
        m = mask256(_mm256_cmpeq_epi8(load256(a + n - 32), load256(b + n - 32))) + 1;
        return m ? byte_diff(a, b, n - 32 + ctz(m)) : 0;
    }
    return str_memcmp_large(a, b, n);
}

/* Bit i set when a[i] and b[i] are equal and a[i] is not NUL. */
static inline __m256i same_and_nonzero(__m256i x, __m256i y)
{
    return _mm256_andnot_si256(eq_zero(x), _mm256_cmpeq_epi8(x, y));
}

/* Four 32-byte blocks per iteration while both pointers have 128 bytes left
   in their pages, single blocks near a page end, bytes at the very end. */
int strcmp_tail_avx2(const u8 *a, const u8 *b)
{
    int result;
    /* One block alone first: strings of a few dozen bytes end here without
       touching the 128-byte group. */
    if (both_in_page(a, b, 32)) {
        unsigned m = mask256(same_and_nonzero(load256(a), load256(b))) ^ 0xffffffffu;
        if (m)
            return byte_diff(a, b, ctz(m));
        a += 32;
        b += 32;
    }
    for (;;) {
        if (both_in_page(a, b, 128)) {
            __m256i s0 = same_and_nonzero(load256(a), load256(b));
            __m256i s1 = same_and_nonzero(load256(a + 32), load256(b + 32));
            __m256i s2 = same_and_nonzero(load256(a + 64), load256(b + 64));
            __m256i s3 = same_and_nonzero(load256(a + 96), load256(b + 96));
            if (mask256(_mm256_and_si256(_mm256_and_si256(s0, s1), _mm256_and_si256(s2, s3))) != 0xffffffffu) {
                unsigned m = mask256(s0) ^ 0xffffffffu;
                if (m) return byte_diff(a, b, ctz(m));
                m = mask256(s1) ^ 0xffffffffu;
                if (m) return byte_diff(a, b, 32 + ctz(m));
                m = mask256(s2) ^ 0xffffffffu;
                if (m) return byte_diff(a, b, 64 + ctz(m));
                return byte_diff(a, b, 96 + ctz(mask256(s3) ^ 0xffffffffu));
            }
            a += 128;
            b += 128;
        } else if (both_in_page(a, b, 32)) {
            unsigned m = mask256(same_and_nonzero(load256(a), load256(b))) ^ 0xffffffffu;
            if (m)
                return byte_diff(a, b, ctz(m));
            a += 32;
            b += 32;
        } else if (strcmp_to_page_end(&a, &b, &result)) {
            return result;
        }
    }
}

int strcmp_avx2(const char *left, const char *right)
{
    const u8 *a = (const u8 *)left, *b = (const u8 *)right;
    int result;
    if (strcmp_block16(&a, &b, &result))
        return result;
    return str_strcmp_tail(a, b);
}

/* ---------------------------------------------------------- strcpy/strcat */

/* One pass with 32-byte blocks for a string with no NUL before the 16-byte
   boundary after s (the caller checked that block). Each aligned source block
   is tested before the previous one is stored; the last 32 bytes of the string
   go out as one overlapping store. Exported for v4. */
void *strcpy_long_avx2(u8 *d, const u8 *s)
{
    const u8 *a = align_down(s, 16) + 16; /* everything before a is nonzero */
    if ((uintptr_t)a & 31) {
        /* reach a 32-byte boundary: check the 16 bytes in between */
        unsigned m16 = zero_mask16(load16a(a));
        if (m16) {
            copy_small(d, s, (size_t)(a - s) + ctz(m16) + 1); /* at most 32 bytes */
            RETURN_OPAQUE(d);
        }
        a += 16;
    }
    __m256i next = load256a(a);
    unsigned m = mask256(eq_zero(next));
    __m256i head = load256(s); /* [s, s+32) lies within the bytes checked so far and this block */
    if (m) {
        size_t n = (size_t)(a - s) + ctz(m) + 1; /* at most 64 bytes */
        if (n <= 32)
            copy_small(d, s, n);
        else
            copy_33_64(d, s, n);
        RETURN_OPAQUE(d);
    }
    store256(d, head);
    size_t off = (size_t)(a - s);
    for (;;) {
        __m256i block = next;
        a += 32;
        next = load256a(a);
        m = mask256(eq_zero(next));
        store256(d + off, block);
        off += 32;
        if (m)
            break;
    }
    const u8 *nul = a + ctz(m);
    store256(d + (nul - s) - 31, load256(nul - 31)); /* the string is longer than 32 here */
    RETURN_OPAQUE(d);
}

static NOINLINE void *strcpy_long(u8 *d, const u8 *s)
{
    return str_strcpy_long(d, s);
}

char *strcpy_avx2(char *dst, const char *src)
{
    const u8 *s = (const u8 *)src;
    u8 *d = (u8 *)dst;
    /* Strings ending in the first block are copied in one pass. */
    const u8 *a = align_down(s, 16);
    unsigned m = zero_mask16(load16a(a)) >> ((uintptr_t)s & 15);
    if (m) {
        copy_small(d, s, ctz(m) + 1);
        return dst;
    }
    return strcpy_long(d, s);
}

char *strcat_avx2(char *dst, const char *src)
{
    str_strcpy(dst + str_strlen(dst), src);
    return dst;
}

