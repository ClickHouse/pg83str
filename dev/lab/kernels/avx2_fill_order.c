/*
 * memset beyond 128 bytes on Broadwell, the order and the number of stores
 * (memcpy of 129..256 bytes: the same stores with the tail's written from
 * the end down ran 1.25x faster on the workload trace than up). Up to 128
 * bytes all are ours (memset_avx2), from 4096 str_fill_large.
 *   gasc   four unaligned stores at the start, aligned 128-byte steps, four
 *          unaligned ending at n, up (ours, glibc)
 *   gdesc  the same, the last four down from n - 32
 *   ht     one unaligned store at each end first, then aligned 32-byte
 *          stores between, up (LLVM's few stores, the far line early)
 *   htd    the same, the aligned stores down from the end
 * Result (Broadwell, the clock held, 2026-11-04), ns a call on the memset
 * trace's calls of 129..256 / 257..512 / 513..1024 bytes, and on the mixed
 * trace with the kernel in ours' memset slot:
 *   few2 (LLVM's: one unaligned store at each end, 0..3 aligned 32-byte
 *        stores after the loop)   24.7 / 28.3 / 43.6   mixed 13.12 (its own
 *                                                       path to 128 bytes)
 *   gdesc                         26.3 / 29.8 / 44.5   mixed 12.50
 *   gasc (glibc's order)          33.8 / 36.8 / 48.6   mixed 12.51
 *   htd                           28.8 / 36.9 / 49.9
 *   ht                            34.3 / 44.2 / 55.9
 *   glibc                         33.7 / 37.3 / 49.7   mixed 12.61
 * Where the stores queue (the memset trace) the fewest stores win; in the
 * mix the queue drains between calls and few2's branches on the 0..3
 * mispredict (ours with its shape: 12.72). Ours took gdesc.
 */
#include <immintrin.h>
#include <stdint.h>

#include "common.h"

void *memset_avx2(void *dst, int c, size_t n);

#define SU(p, v) _mm256_storeu_si256((__m256i *)(p), v)
#define SA(p, v) _mm256_store_si256((__m256i *)(p), v)

void *kl_memset_avx2_gasc(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 128) {
        return memset_avx2(dst, c, n);
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    __m256i v = _mm256_set1_epi8((char)c);
    SU(d, v);
    SU(d + 32, v);
    SU(d + 64, v);
    SU(d + 96, v);
    u8 *end = d + n - 128, *p = (u8 *)align_down(d + 128, 32);

    for (; p < end; p += 128) {
        SA(p, v);
        SA(p + 32, v);
        SA(p + 64, v);
        SA(p + 96, v);
    }

    SU(end, v);
    SU(end + 32, v);
    SU(end + 64, v);
    SU(end + 96, v);
    return dst;
}

void *kl_memset_avx2_gdesc(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 128) {
        return memset_avx2(dst, c, n);
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    __m256i v = _mm256_set1_epi8((char)c);
    SU(d, v);
    SU(d + 32, v);
    SU(d + 64, v);
    SU(d + 96, v);
    u8 *end = d + n - 128, *p = (u8 *)align_down(d + 128, 32);

    for (; p < end; p += 128) {
        SA(p, v);
        SA(p + 32, v);
        SA(p + 64, v);
        SA(p + 96, v);
    }

    SU(end + 96, v);
    SU(end + 64, v);
    SU(end + 32, v);
    SU(end, v);
    return dst;
}

void *kl_memset_avx2_ht(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 128) {
        return memset_avx2(dst, c, n);
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    __m256i v = _mm256_set1_epi8((char)c);
    u8 *end = d + n, *p = (u8 *)align_down(d + 32, 32);
    SU(d, v);
    SU(end - 32, v);

    for (; p + 128 <= end; p += 128) {
        SA(p, v);
        SA(p + 32, v);
        SA(p + 64, v);
        SA(p + 96, v);
    }

    for (; p + 32 <= end; p += 32) {
        SA(p, v);
    }

    return dst;
}

void *kl_memset_avx2_htd(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 128) {
        return memset_avx2(dst, c, n);
    }

    if (n >= 4096) {
        return str_fill_large(d, c, n);
    }

    __m256i v = _mm256_set1_epi8((char)c);
    u8 *end = d + n, *lo = (u8 *)align_down(d + 32, 32), *p = (u8 *)align_down(end, 32);
    SU(d, v);
    SU(end - 32, v);

    for (; p - 128 >= lo; p -= 128) {
        SA(p - 32, v);
        SA(p - 64, v);
        SA(p - 96, v);
        SA(p - 128, v);
    }

    for (; p - 32 >= lo; p -= 32) {
        SA(p - 32, v);
    }

    return dst;
}
