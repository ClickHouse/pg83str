/*
 * AVX2's masked moves for the short paths: vpmaskmovd loads and stores whole
 * dwords under a mask, and does not fault on the masked-off ones. 4..32 bytes
 * in one path: the dwords [0, n / 4) by a masked store, the last 4 bytes by a
 * store that ends at n (it overlaps the masked ones, after both loads). The
 * size tree of the vector memcpy and memset (>= 16, >= 8, >= 4, >= 2), whose
 * branches the workload's sizes mispredict, shrinks to n < 4 and n <= 32.
 * AVX-512's byte masks do this for 64 bytes in one instruction, and that is
 * where ours leads glibc by 2x on Zen 5 and Skylake-SP; Broadwell has AVX2.
 *   cmp     the mask by a compare of the dword index with n / 4
 *   table   the mask loaded from a sliding window of all-ones and zeros
 * Beyond 32 bytes: ours (two blocks to 64, str_copy_large / the fill path).
 * Result (workload traces, clock held, three runs, -O3 builds, 2026-11-04):
 * Broadwell memcpy 29.5 ns a call against ours 24.1 and glibc 23.6, memset
 * 26.1 against 23.8 and 23.7: vpmaskmovd costs more there than the branches
 * it saves. Skylake-SP memset 20.2 against ours AVX2 21.4 (its configuration
 * takes the 64-byte entries anyway), memcpy 25.9 against 22.1.
 */
#include <immintrin.h>
#include <stdint.h>
#include <string.h>

#include "common.h"

static const int32_t window[16] __attribute__((aligned(64))) = {-1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0};

static inline __m256i mask_cmp(size_t n) {
    return _mm256_cmpgt_epi32(_mm256_set1_epi32((int)(n >> 2)), _mm256_setr_epi32(0, 1, 2, 3, 4, 5, 6, 7));
}

static inline __m256i mask_table(size_t n) {
    return _mm256_loadu_si256((const __m256i *)(window + 8 - (n >> 2)));
}

static inline uint32_t ld32(const void *p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

static inline void st32(void *p, uint32_t v) {
    memcpy(p, &v, 4);
}

static inline void *copy_tiny(u8 *d, const u8 *s, size_t n) {
    if (n >= 2) {
        uint16_t x;
        memcpy(&x, s, 2);
        u8 y = s[n - 1];
        memcpy(d, &x, 2);
        d[n - 1] = y;
    } else if (n) {
        d[0] = s[0];
    }

    return d;
}

static inline void *copy_mid(u8 *d, const u8 *s, size_t n) {
    if (n <= 64) {
        __m256i x = _mm256_loadu_si256((const __m256i *)s), y = _mm256_loadu_si256((const __m256i *)(s + n - 32));
        _mm256_storeu_si256((__m256i *)d, x);
        _mm256_storeu_si256((__m256i *)(d + n - 32), y);
        return d;
    }

    return str_copy_large(d, s, n);
}

void *kl_memcpy_avx2_mask_cmp(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if (n <= 32) {
        if (n < 4) {
            return copy_tiny(d, s, n);
        }

        __m256i m = mask_cmp(n);
        __m256i x = _mm256_maskload_epi32((const int *)s, m);
        uint32_t t = ld32(s + n - 4);
        _mm256_maskstore_epi32((int *)d, m, x);
        st32(d + n - 4, t);
        return dst;
    }

    return copy_mid(d, s, n);
}

void *kl_memcpy_avx2_mask_table(void *dst, const void *src, size_t n) {
    u8 *d = dst;
    const u8 *s = src;

    if (n <= 32) {
        if (n < 4) {
            return copy_tiny(d, s, n);
        }

        __m256i m = mask_table(n);
        __m256i x = _mm256_maskload_epi32((const int *)s, m);
        uint32_t t = ld32(s + n - 4);
        _mm256_maskstore_epi32((int *)d, m, x);
        st32(d + n - 4, t);
        return dst;
    }

    return copy_mid(d, s, n);
}

static inline void *fill_mid(u8 *d, int c, size_t n) {
    if (n <= 128) {
        __m256i v = _mm256_set1_epi8((char)c);
        _mm256_storeu_si256((__m256i *)d, v);
        _mm256_storeu_si256((__m256i *)(d + n - 32), v);

        if (n > 64) {
            _mm256_storeu_si256((__m256i *)(d + 32), v);
            _mm256_storeu_si256((__m256i *)(d + n - 64), v);
        }

        return d;
    }

    return str_fill_large(d, c, n);
}

void *kl_memset_avx2_mask_cmp(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 32) {
        if (n < 4) {
            if (n >= 2) {
                uint16_t q = (uint16_t)((u8)c * 0x0101u);
                memcpy(d, &q, 2);
                d[n - 1] = (u8)c;
            } else if (n) {
                d[0] = (u8)c;
            }

            return dst;
        }

        __m256i v = _mm256_set1_epi8((char)c);
        _mm256_maskstore_epi32((int *)d, mask_cmp(n), v);
        st32(d + n - 4, (uint32_t)_mm256_cvtsi256_si32(v));
        return dst;
    }

    return fill_mid(d, c, n);
}

void *kl_memset_avx2_mask_table(void *dst, int c, size_t n) {
    u8 *d = dst;

    if (n <= 32) {
        if (n < 4) {
            if (n >= 2) {
                uint16_t q = (uint16_t)((u8)c * 0x0101u);
                memcpy(d, &q, 2);
                d[n - 1] = (u8)c;
            } else if (n) {
                d[0] = (u8)c;
            }

            return dst;
        }

        __m256i v = _mm256_set1_epi8((char)c);
        _mm256_maskstore_epi32((int *)d, mask_table(n), v);
        st32(d + n - 4, (uint32_t)_mm256_cvtsi256_si32(v));
        return dst;
    }

    return fill_mid(d, c, n);
}
