/*
 * memcpy of 129..256 bytes on Broadwell: eight 32-byte loads, then eight
 * stores, as glibc does, and 1.25x glibc's time on the workload trace's
 * calls of those sizes (the destination lines in L3, the stores queued for
 * them). The order of the stores:
 *   asc    the head's four up from 0, the tail's four up from n - 128 (ours)
 *   desc   the head's four up, the tail's four down from n - 32 (glibc)
 *   alt    head and tail by turns, from the outside in
 * Up to 128 bytes and beyond 256 all of them are ours.
 * Result (Broadwell, the memcpy trace's calls of 129..256 bytes, the clock
 * held, two runs, 2026-11-04): desc 28.6 ns a call, glibc 28.7, asc 35.6,
 * alt 41.2; the same loads and stores, 3.65 of them split across lines in
 * each. Ours took desc for 65..256.
 */
#include <immintrin.h>
#include <stdint.h>

#include "common.h"

#define LD(p) _mm256_loadu_si256((const __m256i *)(p))
#define ST(p, v) _mm256_storeu_si256((__m256i *)(p), v)

static inline int short_copy(u8 *d, const u8 *s, size_t n) {
    if (n <= 32) {
        copy_small(d, s, n);
    } else if (n <= 64) {
        __m256i a = LD(s), b = LD(s + n - 32);
        ST(d, a);
        ST(d + n - 32, b);
    } else if (n <= 128) {
        __m256i a = LD(s), b = LD(s + 32), c = LD(s + n - 64), e = LD(s + n - 32);
        ST(d, a);
        ST(d + 32, b);
        ST(d + n - 64, c);
        ST(d + n - 32, e);
    } else {
        return 0;
    }

    return 1;
}

void *kl_memcpy_avx2_asc(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if (short_copy(d, s, n)) {
        return dst;
    }

    if (n > 256) {
        return str_copy_large(d, s, n);
    }

    __m256i x0 = LD(s), x1 = LD(s + 32), x2 = LD(s + 64), x3 = LD(s + 96);
    __m256i y0 = LD(s + n - 128), y1 = LD(s + n - 96), y2 = LD(s + n - 64), y3 = LD(s + n - 32);
    ST(d, x0);
    ST(d + 32, x1);
    ST(d + 64, x2);
    ST(d + 96, x3);
    ST(d + n - 128, y0);
    ST(d + n - 96, y1);
    ST(d + n - 64, y2);
    ST(d + n - 32, y3);
    return dst;
}

void *kl_memcpy_avx2_desc(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if (short_copy(d, s, n)) {
        return dst;
    }

    if (n > 256) {
        return str_copy_large(d, s, n);
    }

    __m256i x0 = LD(s), x1 = LD(s + 32), x2 = LD(s + 64), x3 = LD(s + 96);
    __m256i y0 = LD(s + n - 128), y1 = LD(s + n - 96), y2 = LD(s + n - 64), y3 = LD(s + n - 32);
    ST(d, x0);
    ST(d + 32, x1);
    ST(d + 64, x2);
    ST(d + 96, x3);
    ST(d + n - 32, y3);
    ST(d + n - 64, y2);
    ST(d + n - 96, y1);
    ST(d + n - 128, y0);
    return dst;
}

void *kl_memcpy_avx2_alt(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if (short_copy(d, s, n)) {
        return dst;
    }

    if (n > 256) {
        return str_copy_large(d, s, n);
    }

    __m256i x0 = LD(s), x1 = LD(s + 32), x2 = LD(s + 64), x3 = LD(s + 96);
    __m256i y0 = LD(s + n - 128), y1 = LD(s + n - 96), y2 = LD(s + n - 64), y3 = LD(s + n - 32);
    ST(d, x0);
    ST(d + n - 32, y3);
    ST(d + 32, x1);
    ST(d + n - 64, y2);
    ST(d + 64, x2);
    ST(d + n - 96, y1);
    ST(d + 96, x3);
    ST(d + n - 128, y0);
    return dst;
}
